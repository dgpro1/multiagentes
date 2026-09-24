# AUDIT.md

How to audit a commit of this repository, whether you are a person or a coding agent
(Claude Code, Antigravity, OpenCode, Freebuff, ...). The mechanical part is one script;
the reading part is a short checklist. The task you are given is usually just:
"Read AUDIT.md and audit commit `<sha>`".

## Rules for an auditing agent

- **Read only.** Do not edit files, commit, push, merge, or change branches of any checkout other than
  the temporary one the script creates. Do not "fix" what fails: report it.
- Do not run the API tests against the normal `openlivery_test` database. The script uses its own.
- Never print secret values (`.env` contents, keys, tokens). Names are fine.
- Finish with a report of at most 300 words: overall PASS/FAIL, the failing checks with the first
  error line and the file it points to, and any finding from the reading checklist.

## 1. Run the script

```bash
python scripts/audit.py <sha>          # a commit or branch; default HEAD
```

It checks the commit out into a temporary worktree, creates two throwaway databases, runs everything
below, deletes them, and writes `work/audit-<sha>.md` (git-ignored) plus a one-line verdict.
It takes 15 to 25 minutes (the API suite is most of it). `--skip-tests` skips the suite for a quick pass.

| Check | Why |
| --- | --- |
| migrations: single head | Two heads break `alembic upgrade head` on the server. |
| migrations: up, down, up | A migration must apply, reverse and apply again on an empty database. |
| api: full test suite | Everything, including the route/scope/portal-feature coverage tests. |
| api: pyflakes (advisory) | Undefined names and unused imports; a few known findings exist, only new ones matter. |
| web: lockfile installs with npm 10 | GitHub Actions and the Docker build use npm 10; a lockfile written by a newer npm can fail there. |
| web: npm ci, type-check, lint, build | What CI and the image build run. |
| deploy: env generator + preflight, compose | The Coolify kit still produces a valid environment and compose file. |

Requirements: git, Node 22+, the API virtualenv (`apps/api/.venv`, or `--python`), and PostgreSQL
reachable with the URL in `AUDIT_DATABASE_URL` (default: the test URL of `apps/api/tests/conftest.py`)
whose user may create databases. If a requirement is missing the check says so and is marked SKIP or FAIL;
report that instead of working around it.

## 2. Review by reading (only when the report lists sensitive files)

The script lists changed files that touch routes, permissions, scopes, migrations, deploy files or
workflows. For those, read the diff (`git diff <base>...<sha> -- <file>`) and answer:

1. **Agency and client scoping.** Does every new query filter by the caller's agency, and by the client for
   portal routes? Could a user of client A read or change client B's rows by guessing an id?
2. **Door coverage.** A new API route must be scoped (`Depends(require(SCOPE))`) or deliberately closed; a new
   portal route must declare its function (`require_feature`) and, for writes, a permission. Are the
   route matrices in `tests/test_portal_features.py` and `tests/test_api_scope_coverage.py` updated?
3. **Secrets.** Are keys, tokens or passwords logged, returned in a response, or stored unencrypted?
   `ENCRYPTION_KEY` and `SECRET_KEY` must not be renamed, regenerated or given defaults.
4. **Migrations.** Additive and reversible? A destructive change needs the `# contract: reviewed` line and an
   explanation. Does the API still start against the previous schema (the deploy applies migrations on start)?
5. **Deploy files.** No published host ports, required variables still use `${VAR:?...}`, nothing binds 80/443.

## 3. Report format

```
Audit of <sha>: PASS|FAIL
Failing checks: <name> - <first error line> (<file>) ...   (or "none")
Reading findings: <file:line - what and why> ...            (or "none needed" / "none")
Not run: <checks skipped and why>
```

## GitHub does part of this on every push

`Tests` (API suite, migrations, web lint and build) runs on every push to `main`, and `Publish images` only
runs, and the `production` branch only advances, after `Tests` is green. Check a commit without any tool
installed beyond git and Python:

```bash
python scripts/ci-status.py <sha> --wait 30
```

This script audits before a merge; GitHub gates what reaches the server. Use both for large changes
(migrations, permissions and scopes, portal functions, deploy files, dependencies and lockfiles).
