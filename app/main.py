"""FastAPI-App-Zusammenbau: Router registrieren, DB beim Start initialisieren, optional
einen ersten Admin-User aus RZ_ADMIN_USER/RZ_ADMIN_PASSWORD anlegen."""

from __future__ import annotations

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

settings = get_settings()


def _bootstrap_admin() -> None:
    if not settings.admin_password:
        return
    with session_scope() as db:
        exists = db.scalar(select(AdminUser).limit(1))
        if exists is not None:
            return
        db.add(AdminUser(username=settings.admin_user, password_hash=hash_password(settings.admin_password)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _bootstrap_admin()
    yield


app = FastAPI(title=settings.site_title, version=__version__, lifespan=lifespan)

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

app.include_router(api_public.health_router)
app.include_router(api_agent.router)
app.include_router(api_public.router)
app.include_router(admin.router)
app.include_router(kiosk.router)
