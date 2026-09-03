"""Zur Laufzeit über den Admin-Bereich änderbare Einstellungen (Tabelle `settings`,
Key-Value). Im Unterschied zu app/config.py (Umgebungsvariablen, nur beim Start gelesen)
soll der Admin z.B. die Frist fürs automatische Auschecken ohne Neustart/Server-Zugriff
anpassen können."""

from __future__ import annotations

from app.config import get_settings
from app.models import Setting

_KEY_AUTO_CHECKOUT_HOURS = "auto_checkout_hours"

settings = get_settings()


def get_auto_checkout_hours(db) -> int:
    """0 (oder negativ) bedeutet: automatisches Auschecken ist deaktiviert."""
    row = db.get(Setting, _KEY_AUTO_CHECKOUT_HOURS)
    if row is None:
        return settings.auto_checkout_default_hours
    try:
        return int(row.value)
    except ValueError:
        return settings.auto_checkout_default_hours


def set_auto_checkout_hours(db, hours: int) -> None:
    row = db.get(Setting, _KEY_AUTO_CHECKOUT_HOURS)
    if row is None:
        db.add(Setting(key=_KEY_AUTO_CHECKOUT_HOURS, value=str(hours)))
    else:
        row.value = str(hours)
    db.commit()
