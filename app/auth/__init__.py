from app.auth.base import AuthProvider
from app.auth.local import LocalAuthProvider

__all__ = ["AuthProvider", "LocalAuthProvider", "get_auth_provider"]


def get_auth_provider() -> AuthProvider:
    """Liefert den aktiven Auth-Provider für den Admin-Login.

    Aktuell fest auf LocalAuthProvider verdrahtet (Konzept 7: v1 hat genau einen lokalen
    Admin-User). Ein LDAP/AD-Provider lässt sich hier später ergänzen, ohne das
    Datenmodell oder den Admin-Router anzufassen — dafür ist die Prüfung hinter dem
    AuthProvider-Protokoll gekapselt.
    """
    return LocalAuthProvider()
