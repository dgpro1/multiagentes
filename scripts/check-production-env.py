#!/usr/bin/env python3
"""Preflight for a production deployment: checks a .env file (KEY=VALUE lines) before
it is pasted into Coolify (or any host) and stops on the mistakes that cause outages.

Usage: python scripts/check-production-env.py path/to/env-file
Exit code 0 when nothing is wrong, 1 otherwise. Values are never printed.
"""
import re
import sys
from urllib.parse import urlparse

REQUIRED = [
    "FRONTEND_URL", "POSTGRES_PASSWORD", "SECRET_KEY", "ENCRYPTION_KEY", "WHATSAPP_BRIDGE_TOKEN",
    "MESSAGING_PROVIDER_WEBHOOK_SECRET", "EVOLUTION_API_KEY", "EVOLUTION_WEBHOOK_SECRET", "EVOLUTION_DB_PASSWORD",
]
SECRETS = [key for key in REQUIRED if key != "FRONTEND_URL"]
PLACEHOLDERS = ("CHANGE_THIS", "changeme", "dev-local", "example", "your-")


def read(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    env = read(sys.argv[1])
    problems: list[str] = []

    for key in REQUIRED:
        if not env.get(key):
            problems.append(f"{key} is missing or empty")
    for key in SECRETS:
        value = env.get(key, "")
        if value and any(token.lower() in value.lower() for token in PLACEHOLDERS):
            problems.append(f"{key} still holds a placeholder")
        elif value and len(value) < 32 and "PASSWORD" not in key and key != "EVOLUTION_API_KEY":
            problems.append(f"{key} is shorter than 32 characters")
    for key in ("POSTGRES_PASSWORD", "EVOLUTION_DB_PASSWORD"):
        if env.get(key) and not re.fullmatch(r"[A-Za-z0-9]{16,}", env[key]):
            problems.append(f"{key} must be letters and numbers only (it goes inside a URL), 16 or more")
    if len({env.get(k) for k in ("SECRET_KEY", "ENCRYPTION_KEY", "WHATSAPP_BRIDGE_TOKEN") if env.get(k)}) < len([k for k in ("SECRET_KEY", "ENCRYPTION_KEY", "WHATSAPP_BRIDGE_TOKEN") if env.get(k)]):
        problems.append("SECRET_KEY, ENCRYPTION_KEY and WHATSAPP_BRIDGE_TOKEN must be different from each other")

    url = urlparse(env.get("FRONTEND_URL", ""))
    if env.get("FRONTEND_URL"):
        if url.scheme != "https":
            problems.append("FRONTEND_URL must be an https address")
        if url.hostname in (None, "localhost", "127.0.0.1") or url.path not in ("", "/"):
            problems.append("FRONTEND_URL must be the bare public domain (no localhost, no path)")
    if env.get("COOKIE_SECURE", "true").lower() != "true":
        problems.append("COOKIE_SECURE must be true behind https")

    redirect = env.get("GOOGLE_REDIRECT_URI", "")
    if env.get("GOOGLE_CLIENT_ID") or env.get("GOOGLE_CLIENT_SECRET"):
        if not (env.get("GOOGLE_CLIENT_ID") and env.get("GOOGLE_CLIENT_SECRET")):
            problems.append("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET go together")
        if redirect and env.get("FRONTEND_URL") and redirect != f"{env['FRONTEND_URL'].rstrip('/')}/api/calendar/oauth/callback":
            problems.append("GOOGLE_REDIRECT_URI should be FRONTEND_URL + /api/calendar/oauth/callback, and registered in Google Cloud")

    if problems:
        print("Fix before deploying:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("OK: the environment passes the preflight. Remember to keep ENCRYPTION_KEY backed up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
