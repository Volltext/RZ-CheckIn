from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminUser
from app.security import hash_password, verify_password

# Fester Dummy-Hash für den Timing-Ausgleich unten. Wird einmalig beim Import erzeugt,
# nicht bei jedem Login-Versuch (Argon2-Hashing ist bewusst langsam).
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-fuer-timing-ausgleich")


class LocalAuthProvider:
    """Prüft Benutzername/Passwort gegen die lokale admin_users-Tabelle."""

    def authenticate(self, db: Session, username: str, password: str) -> AdminUser | None:
        admin = db.scalar(select(AdminUser).where(AdminUser.username == username))
        if admin is None:
            # Timing-Angriff auf Benutzernamen-Existenz erschweren: trotzdem einen
            # Hash-Vergleich in vergleichbarer Zeit durchführen, auch wenn kein User
            # gefunden wurde.
            verify_password(password, _DUMMY_PASSWORD_HASH)
            return None
        if not verify_password(password, admin.password_hash):
            return None
        return admin
