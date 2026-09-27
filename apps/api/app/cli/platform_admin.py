"""Provision and recover the platform's own accounts (operator-only).

Run on the server: ``python -m app.cli.platform_admin create``. Passwords are
read interactively and never appear in arguments, environment or logs. The
session comes from ``app.database.new_session``, the one factory code outside
a request is allowed to use.
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from ..database import new_session
from ..models import PlatformAdmin
from ..security import hash_password


def _find(db, email: str) -> PlatformAdmin | None:
    return db.scalar(select(PlatformAdmin).where(PlatformAdmin.email == email.lower().strip()))


def _read_password() -> str:
    password = getpass.getpass("Password: ")
    if not password:
        sys.exit("A password is required")
    if getpass.getpass("Repeat password: ") != password:
        sys.exit("The two passwords do not match")
    return password


def create(name: str | None, email: str | None) -> None:
    with new_session() as db:
        resolved_name = (name or input("Name: ")).strip()
        resolved_email = (email or input("E-mail: ")).strip().lower()
        if not resolved_name or not resolved_email:
            sys.exit("A name and an e-mail are required")
        if _find(db, resolved_email):
            sys.exit(f"A platform admin with the e-mail {resolved_email} already exists; use reset-password or disable")
        db.add(PlatformAdmin(name=resolved_name, email=resolved_email, password_hash=hash_password(_read_password())))
        db.commit()
        print(f"Created platform admin {resolved_email}")


def disable(email: str) -> None:
    with new_session() as db:
        admin = _find(db, email)
        if not admin:
            sys.exit(f"No platform admin with the e-mail {email}")
        admin.is_active = False
        admin.session_version += 1
        db.commit()
        print(f"Disabled platform admin {admin.email}")


def reset_password(email: str) -> None:
    with new_session() as db:
        admin = _find(db, email)
        if not admin:
            sys.exit(f"No platform admin with the e-mail {email}")
        admin.password_hash = hash_password(_read_password())
        admin.session_version += 1
        db.commit()
        print(f"Reset the password of {admin.email}; every session of that account is now invalid")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.platform_admin",
        description="Provision and recover the platform's own accounts. Run on the server; never exposed over HTTP.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create", help="create the first (or another) platform admin")
    create_parser.add_argument("--name")
    create_parser.add_argument("--email")
    disable_parser = sub.add_parser("disable", help="lock an account out and revoke its sessions")
    disable_parser.add_argument("email")
    reset_parser = sub.add_parser("reset-password", help="set a new password and revoke every session")
    reset_parser.add_argument("email")
    args = parser.parse_args(argv)

    if args.command == "create":
        create(args.name, args.email)
    elif args.command == "disable":
        disable(args.email)
    elif args.command == "reset-password":
        reset_password(args.email)


if __name__ == "__main__":
    main()
