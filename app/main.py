"""FastAPI-App-Zusammenbau: Router registrieren, DB beim Start initialisieren, ersten
Admin-User anlegen (aus RZ_ADMIN_USER/RZ_ADMIN_PASSWORD, oder automatisch mit einem
zufälligen Passwort, damit ein frisch gestarteter Container ohne weitere Konfiguration
sofort nutzbar ist -- siehe Containerfile/docker-entrypoint.sh)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app import __version__
from app.config import get_settings
from app.db import init_db, session_scope
from app.models import AdminUser
from app.routers import admin, api_agent, api_public, kiosk
from app.security import hash_password
from app.services.attendance import run_auto_checkout

settings = get_settings()
LOG = logging.getLogger("rz_checkin")


def _bootstrap_admin() -> None:
    if not settings.admin_password and not settings.admin_auto_bootstrap:
        return

    with session_scope() as db:
        exists = db.scalar(select(AdminUser).limit(1))
        if exists is not None:
            return

        password = settings.admin_password
        generated = not password
        if generated:
            password = secrets.token_urlsafe(12)

        db.add(AdminUser(username=settings.admin_user, password_hash=hash_password(password)))

        if generated:
            # Gut sichtbar in "podman logs" -- ohne RZ_ADMIN_PASSWORD-Umgebungsvariable
            # bekommt der allererste Start einen zufälligen Admin-Zugang, damit der
            # Container ohne weitere Einrichtung sofort nutzbar ist.
            print("=" * 72, flush=True)
            print("RZ-CheckIn: Erststart -- Admin-Zugang wurde automatisch angelegt:", flush=True)
            print(f"  Benutzername: {settings.admin_user}", flush=True)
            print(f"  Passwort:     {password}", flush=True)
            print("  Bitte nach der ersten Anmeldung unter /admin/passwort aendern!", flush=True)
            print("=" * 72, flush=True)


async def _auto_checkout_loop() -> None:
    """Prüft periodisch, ob jemand die im Admin-Bereich eingestellte Frist fürs
    automatische Auschecken überschritten hat (siehe app/services/attendance.py und
    app/services/settings.py). Läuft als Hintergrund-Task im selben Prozess -- bewusst
    kein externer Cronjob, damit ein frisch gestarteter Container ohne weitere
    Einrichtung funktioniert (siehe "Super-easy Deployment" im README)."""
    while True:
        try:
            with session_scope() as db:
                anzahl = await asyncio.to_thread(run_auto_checkout, db)
            if anzahl:
                LOG.info("Automatisches Auschecken: %s Person(en) ausgecheckt.", anzahl)
        except Exception:  # noqa: BLE001 - ein Fehler hier darf den Server nicht beenden
            LOG.exception("Fehler beim automatischen Auschecken")
        await asyncio.sleep(settings.auto_checkout_check_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _bootstrap_admin()
    task = asyncio.create_task(_auto_checkout_loop())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title=settings.site_title, version=__version__, lifespan=lifespan)

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

app.include_router(api_public.health_router)
app.include_router(api_agent.router)
app.include_router(api_public.router)
app.include_router(admin.router)
app.include_router(kiosk.router)
