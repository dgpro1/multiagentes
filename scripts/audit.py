#!/usr/bin/env python3
"""Audit one commit in isolation and write a green/red report.

    python scripts/audit.py                 # audit HEAD
    python scripts/audit.py <commit|branch> [--base origin/main] [--keep] [--skip-tests]

What it does, without touching your checkout or your normal test database:
  * checks the commit out into a temporary git worktree,
  * creates two throwaway databases (openlivery_audit_test, openlivery_audit_migrate),
  * runs the full API suite, the migration chain (single head, up, down, up), the web
    install with npm 10 (what CI and the Docker build use), type-check, lint and build,
    the Coolify env kit, and the compose file when Docker is available,
  * lists the changed files that need a human/agent security read-through,
  * writes work/audit-<sha>.md (git-ignored) and exits 1 if anything failed.

It never edits, commits or pushes. Needs: git, Node 22+, the API virtualenv
(apps/api/.venv, or pass --python), and PostgreSQL reachable with the URL of
AUDIT_DATABASE_URL (default: the test database URL of tests/conftest.py) whose user may
create databases.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_URL = "postgresql+psycopg://openlivery:openlivery@localhost:5432/openlivery_test"
SENSITIVE = re.compile(
    r"^apps/api/app/(routers/|api_scopes\.py|deps\.py|security\.py|portal_[a-z_]+\.py|services/(teams|professionals|client_details|"
    r"portal_return)\.py)|^apps/api/migrations/|^docker-compose|^docker/|^\.github/workflows/|^scripts/")
NPM = "npm.cmd" if os.name == "nt" else "npm"
NPX = "npx.cmd" if os.name == "nt" else "npx"


def run(command, cwd, env=None, timeout=3600, merge=True):
    started = time.time()
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                                encoding="utf-8", errors="replace")
        return result.returncode, (result.stdout + result.stderr if merge else result.stdout), time.time() - started
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s", time.time() - started
    except FileNotFoundError as error:
        return 127, f"command not found: {error}", time.time() - started


def git(*args, cwd=REPO):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def find_python(explicit):
    candidates = [explicit, os.environ.get("AUDIT_PYTHON")]
    roots = [REPO]
    try:
        roots.append(Path(git("worktree", "list", "--porcelain").splitlines()[0].split(" ", 1)[1]))
    except Exception:
        pass
    for root in roots:
        candidates += [root / "apps/api/.venv/Scripts/python.exe", root / "apps/api/.venv/bin/python"]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return sys.executable


def split_url(url):
    match = re.match(r"^(.*://[^/]+/)([^?]+)(.*)$", url)
    if not match:
        sys.exit(f"cannot read the database url: {url}")
    return match.group(1), match.group(3)


def pg(python, admin_url, sql):
    code = ("import sys, psycopg\n"
            "with psycopg.connect(sys.argv[1].replace('+psycopg', ''), autocommit=True) as c:\n"
            "    c.execute(sys.argv[2])\n")
    return run([python, "-c", code, admin_url, sql], REPO, timeout=60)


class Report:
    def __init__(self):
        self.rows = []

    def add(self, name, status, seconds=0.0, detail=""):
        self.rows.append((name, status, seconds, detail))
        print(f"  [{status:^5}] {name} ({seconds:.0f}s)", flush=True)

    @property
    def failed(self):
        return any(status == "FAIL" for _, status, _, _ in self.rows)


def tail(text, lines=30):
    return "\n".join(text.strip().splitlines()[-lines:])


def main():
    parser = argparse.ArgumentParser(description="Audit one commit in isolation")
    parser.add_argument("commit", nargs="?", default="HEAD")
    parser.add_argument("--base", help="what to diff against for the review list (default origin/main)")
    parser.add_argument("--python", help="python of the API virtualenv")
    parser.add_argument("--keep", action="store_true", help="keep the worktree and databases")
    parser.add_argument("--skip-tests", action="store_true", help="skip the (slow) API suite")
    options = parser.parse_args()

    sha = git("rev-parse", options.commit)
    short = sha[:7]
    python = find_python(options.python)
    base_url = os.environ.get("AUDIT_DATABASE_URL", DEFAULT_URL)
    prefix, query = split_url(base_url)
    test_db, migrate_db = "openlivery_audit_test", "openlivery_audit_migrate"
    admin_url = prefix + "postgres" + query
    worktree = Path(tempfile.gettempdir()) / f"openlivery-audit-{short}"
    report = Report()
    print(f"Auditing {short} with {python}", flush=True)

    if worktree.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO, capture_output=True)
        shutil.rmtree(worktree, ignore_errors=True)
    git("worktree", "add", "--detach", str(worktree), sha)
    api, web = worktree / "apps/api", worktree / "apps/web"
    databases_created = False
    try:
        # Databases
        for name in (test_db, migrate_db):
            pg(python, admin_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            code, out, seconds = pg(python, admin_url, f'CREATE DATABASE "{name}"')
            if code != 0:
                report.add("create audit databases", "FAIL", seconds, tail(out))
                raise SystemExit
        databases_created = True
        env = {**os.environ, "RATE_LIMIT_ENABLED": "false"}

        # Migrations: one head, then up, down, up on an empty database.
        code, out, seconds = run([python, "-m", "alembic", "heads"], api, env={**env, "DATABASE_URL": prefix + migrate_db + query})
        heads = len([line for line in out.splitlines() if "(head)" in line])
        report.add("migrations: single head", "PASS" if code == 0 and heads == 1 else "FAIL", seconds, tail(out))
        migrate_env = {**env, "DATABASE_URL": prefix + migrate_db + query}
        steps = [["upgrade", "head"], ["downgrade", "-1"], ["upgrade", "head"]]
        cycle_seconds, cycle_ok, cycle_out = 0.0, True, ""
        for step in steps:
            code, out, seconds = run([python, "-m", "alembic", *step], api, env=migrate_env)
            cycle_seconds += seconds
            if code != 0:
                cycle_ok, cycle_out = False, f"alembic {' '.join(step)}\n{tail(out)}"
                break
        report.add("migrations: up, down, up", "PASS" if cycle_ok else "FAIL", cycle_seconds, cycle_out)

        # API suite
        if options.skip_tests:
            report.add("api: full test suite", "SKIP", 0, "--skip-tests")
        else:
            test_env = {**env, "TEST_DATABASE_URL": prefix + test_db + query}
            code, out, seconds = run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rf"], api, env=test_env, timeout=3600)
            summary = tail(out, 40)
            report.add("api: full test suite", "PASS" if code == 0 else "FAIL", seconds, summary)

        # Static check (advisory: known findings exist, only a change in them matters).
        code, out, seconds = run([python, "-m", "pyflakes", "app"], api)
        if code == 127 or "No module named pyflakes" in out:
            report.add("api: pyflakes (advisory)", "SKIP", seconds, "pyflakes is not installed in the virtualenv")
        else:
            findings = [line for line in out.splitlines() if line.strip()]
            report.add("api: pyflakes (advisory)", "PASS" if not findings else "WARN", seconds,
                       f"{len(findings)} finding(s)\n" + "\n".join(findings[:15]))

        # Web: npm 10 is what GitHub Actions and the Docker build use.
        code, out, seconds = run([NPX, "-y", "npm@10", "ci", "--dry-run", "--ignore-scripts"], web)
        report.add("web: lockfile installs with npm 10", "PASS" if code == 0 else "FAIL", seconds, tail(out, 15))
        code, out, seconds = run([NPM, "ci", "--no-audit", "--no-fund"], web, timeout=900)
        installed = code == 0
        report.add("web: npm ci", "PASS" if installed else "FAIL", seconds, tail(out, 15))
        for name, command in (("web: type-check", [NPX, "tsc", "--noEmit"]), ("web: lint", [NPM, "run", "lint"]),
                              ("web: production build", [NPM, "run", "build"])):
            if not installed:
                report.add(name, "SKIP", 0, "npm ci failed")
                continue
            code, out, seconds = run(command, web, env={**os.environ, "NEXT_TELEMETRY_DISABLED": "1"}, timeout=900)
            report.add(name, "PASS" if code == 0 else "FAIL", seconds, tail(out, 25))

        # Coolify kit
        bash = shutil.which("bash")
        if bash:
            env_file = worktree / "audit.env"
            code, out, seconds = run([bash, "scripts/generate-coolify-env.sh", "app.example.com"], worktree, merge=False)
            if code == 0:
                env_file.write_text(out, encoding="utf-8")
                code, out2, seconds2 = run([python, "scripts/check-production-env.py", str(env_file)], worktree)
                report.add("deploy: env generator + preflight", "PASS" if code == 0 else "FAIL", seconds + seconds2, tail(out2, 15))
                if shutil.which("docker"):
                    code, out3, seconds3 = run(["docker", "compose", "--env-file", str(env_file), "-f", "docker-compose.coolify.yml", "config", "-q"], worktree)
                    report.add("deploy: coolify compose is valid", "PASS" if code == 0 else "FAIL", seconds3, tail(out3, 15))
                else:
                    report.add("deploy: coolify compose is valid", "SKIP", 0, "docker is not installed")
            else:
                report.add("deploy: env generator + preflight", "FAIL", seconds, tail(out, 15))
        else:
            report.add("deploy: env generator + preflight", "SKIP", 0, "bash is not available")
    except SystemExit:
        pass
    finally:
        # Which changed files need a human (or agent) to read them?
        try:
            base = options.base or ("origin/main" if subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], cwd=REPO).returncode != 0 else f"{sha}~1")
            changed = git("diff", "--name-only", f"{base}...{sha}").splitlines()
        except Exception:
            base, changed = "?", []
        review = [name for name in changed if SENSITIVE.search(name)]
        if not options.keep:
            if databases_created:
                for name in (test_db, migrate_db):
                    pg(python, admin_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO, capture_output=True)
            shutil.rmtree(worktree, ignore_errors=True)

    out_dir = REPO / "work"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"audit-{short}.md"
    lines = [f"# Audit of {short}", "", f"Base for the review list: `{base}`. Overall: **{'FAIL' if report.failed else 'PASS'}**", "",
             "| Check | Result | Seconds |", "| --- | --- | --- |"]
    lines += [f"| {name} | {status} | {seconds:.0f} |" for name, status, seconds, _ in report.rows]
    lines += ["", "## Needs a security read-through" if review else "## Security read-through", ""]
    lines += ([f"- {name}" for name in review] + ["", "Read AUDIT.md, section 'Review by reading'."]) if review else ["No sensitive files changed."]
    for name, status, _, detail in report.rows:
        if status in ("FAIL", "WARN") and detail:
            lines += ["", f"## {status}: {name}", "", "```", detail, "```"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{'FAIL' if report.failed else 'PASS'} - report: {path}")
    sys.exit(1 if report.failed else 0)


if __name__ == "__main__":
    main()
