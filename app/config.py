"""Zentrale Konfiguration. Alles ist über Umgebungsvariablen (Präfix RZ_) steuerbar,
siehe .env.example. Sinnvolle Defaults für die lokale Entwicklung."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RZ_", env_file=".env", extra="ignore")

    database_path: str = "./data/rz-checkin.db"

    session_secret: str = "dev-only-insecure-secret-change-me"
    session_cookie_name: str = "rz_admin_session"
    session_max_age_seconds: int = 60 * 60 * 8  # 8h Arbeitstag

    admin_user: str = "admin"
    admin_password: str = ""
    # Ist kein RZ_ADMIN_PASSWORD gesetzt, legt der Erststart automatisch einen Admin mit
    # zufälligem Passwort an (in den Logs sichtbar) -- so ist ein frisch gestarteter
    # Container ohne weitere Einrichtung sofort nutzbar. Für Tests und Deployments, die
    # den Admin bewusst separat provisionieren, per RZ_ADMIN_AUTO_BOOTSTRAP=false abschaltbar.
    admin_auto_bootstrap: bool = True

    scan_debounce_seconds: int = 5
    agent_offline_threshold_seconds: int = 90
    retention_days: int = 730

    # Startwert fürs automatische Auschecken (falls jemand das Aus-Scannen vergisst),
    # solange der Admin im Admin-Bereich noch nichts anderes eingestellt hat (siehe
    # app/services/settings.py -- dort ist der Wert danach zur Laufzeit änderbar).
    # 0 = deaktiviert.
    auto_checkout_default_hours: int = 12
    # Wie oft der Hintergrund-Task im Server-Prozess prüft, ob jemand die konfigurierte
    # Frist überschritten hat (siehe app/main.py). Kein Admin-Wert, da rein technisch.
    auto_checkout_check_interval_seconds: int = 300

    admin_ip_allowlist: str = ""

    site_title: str = "Rechenzentrum Check-in"

    @property
    def database_file(self) -> Path:
        path = Path(self.database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def admin_ip_allowlist_entries(self) -> list[str]:
        return [entry.strip() for entry in self.admin_ip_allowlist.split(",") if entry.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
