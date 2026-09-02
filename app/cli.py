"""Wartungs-CLI: `python -m app.cli <befehl>`.

Befehle:
  create-admin    Legt einen Admin-User interaktiv an (oder mit --username/--password).
  create-agent    Legt einen Reader-Agenten an und gibt den API-Key einmalig aus.
  purge           Führt den Retention-Wartungsjob aus (siehe app/services/retention.py).
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.db import init_db, session_scope
from app.models import AdminUser
from app.security import generate_agent_api_key, hash_agent_api_key, hash_password
from app.services.retention import purge as run_purge


def cmd_create_admin(args: argparse.Namespace) -> int:
    username = args.username or input("Benutzername: ").strip()
    password = args.password or getpass.getpass("Passwort: ")
    if not password:
        print("Passwort darf nicht leer sein.", file=sys.stderr)
        return 1

    with session_scope() as db:
        existing = db.scalar(select(AdminUser).where(AdminUser.username == username))
        if existing is not None:
            print(f"Admin '{username}' existiert bereits.", file=sys.stderr)
            return 1
        db.add(AdminUser(username=username, password_hash=hash_password(password)))

    print(f"Admin '{username}' angelegt.")
    return 0


def cmd_create_agent(args: argparse.Namespace) -> int:
    from app.models import Agent

    api_key = generate_agent_api_key()
    with session_scope() as db:
        if db.get(Agent, args.agent_id) is not None:
            print(f"Agent '{args.agent_id}' existiert bereits.", file=sys.stderr)
            return 1
        db.add(
            Agent(
                agent_id=args.agent_id,
                bezeichnung=args.bezeichnung or args.agent_id,
                api_key_hash=hash_agent_api_key(api_key),
            )
        )

    print(f"Agent '{args.agent_id}' angelegt. API-Key (nur jetzt sichtbar):\n{api_key}")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    with session_scope() as db:
        result = run_purge(db, dry_run=args.dry_run)

    modus = "Vorschau (--dry-run)" if args.dry_run else "Ausgeführt"
    print(
        f"[{modus}] Stichtag: {result.cutoff.isoformat()} — "
        f"{result.checklog_entries_removed} Log-Einträge, "
        f"{result.visitors_removed} verwaiste Besucherprofile entfernt."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    subparsers = parser.add_subparsers(dest="befehl", required=True)

    p_admin = subparsers.add_parser("create-admin", help="Admin-User anlegen")
    p_admin.add_argument("--username")
    p_admin.add_argument("--password")
    p_admin.set_defaults(func=cmd_create_admin)

    p_agent = subparsers.add_parser("create-agent", help="Reader-Agenten anlegen")
    p_agent.add_argument("agent_id")
    p_agent.add_argument("--bezeichnung")
    p_agent.set_defaults(func=cmd_create_agent)

    p_purge = subparsers.add_parser("purge", help="Retention-Wartungsjob ausführen")
    p_purge.add_argument("--dry-run", action="store_true", help="Nur anzeigen, nicht löschen")
    p_purge.set_defaults(func=cmd_purge)

    args = parser.parse_args(argv)
    init_db()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
