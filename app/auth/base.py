"""Protokoll für Admin-Auth-Provider. Ein zukünftiger LDAP/AD-Provider implementiert
dieselbe Schnittstelle und wird in app/auth/__init__.py eingehängt."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.orm import Session

from app.models import AdminUser


class AuthProvider(Protocol):
    def authenticate(self, db: Session, username: str, password: str) -> AdminUser | None:
        """Prüft die Zugangsdaten und liefert den AdminUser bei Erfolg, sonst None."""
        ...
