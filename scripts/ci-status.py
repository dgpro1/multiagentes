#!/usr/bin/env python3
"""Print the GitHub Actions state of a commit (Tests, Publish images) and whether
the `production` branch has reached it. No dependencies, no token: the API is public.

    python scripts/ci-status.py                 # HEAD
    python scripts/ci-status.py <sha> --wait 30 # wait up to 30 minutes for a verdict

Exit code: 0 all green, 1 something failed, 2 still running or not started.
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def repo_slug():
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", git("remote", "get-url", "origin"))
    if not match:
        sys.exit("origin is not a GitHub repository")
    return match.group(1)


def api(path):
    request = urllib.request.Request("https://api.github.com/" + path, headers={"Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        sys.exit(f"GitHub answered {error.code}: {error.reason} (rate limit? try again in a few minutes)")


def snapshot(repo, sha):
    runs = (api(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=20") or {}).get("workflow_runs", [])
    latest = {}
    for run in runs:
        if run["name"] not in latest:
            latest[run["name"]] = run
    branch = api(f"repos/{repo}/branches/production")
    return latest, (branch or {}).get("commit", {}).get("sha")


def verdict(latest, production_sha, sha):
    rows, failed, pending = [], False, False
    for name in ("Tests", "Publish images"):
        run = latest.get(name)
        if not run:
            rows.append((name, "not started"))
            pending = True
        elif run["status"] != "completed":
            rows.append((name, run["status"]))
            pending = True
        else:
            rows.append((name, run["conclusion"]))
            failed = failed or run["conclusion"] != "success"
    rows.append(("production branch", "at this commit" if production_sha == sha else "not yet at this commit"))
    return rows, failed, pending


def main():
    parser = argparse.ArgumentParser(description="GitHub Actions state of a commit")
    parser.add_argument("commit", nargs="?", default="HEAD")
    parser.add_argument("--wait", type=float, default=0, metavar="MINUTES", help="wait for a verdict")
    options = parser.parse_args()
    sha = git("rev-parse", options.commit)
    repo = repo_slug()
    deadline = time.time() + options.wait * 60
    while True:
        latest, production_sha = snapshot(repo, sha)
        rows, failed, pending = verdict(latest, production_sha, sha)
        if failed or not pending or time.time() >= deadline:
            break
        time.sleep(30)
    print(f"{repo} @ {sha[:7]}")
    for name, state in rows:
        print(f"  {name:<18} {state}")
    for run in latest.values():
        if run["status"] == "completed" and run["conclusion"] != "success":
            print(f"  details: {run['html_url']}")
    sys.exit(1 if failed else 2 if pending else 0)


if __name__ == "__main__":
    main()
