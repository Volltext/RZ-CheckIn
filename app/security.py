"""Passwort-Hashing, Admin-Session-Cookies, Agent-API-Key-Prüfung."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import Agent, AdminUser

settings = get_settings()

# Argon2 für Admin-Passwörter (langsam, für Menschen gedacht).
_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

# API-Keys für Agenten werden nur als SHA-256-Hash gespeichert; der Klartext-Key wird
# dem Admin beim Anlegen einmalig angezeigt und ist danach nicht mehr abrufbar.
# SHA-256 statt Argon2, weil der Key selbst schon hochentropisch (32 Byte, zufällig)
# ist und pro Request geprüft werden muss (Argon2 wäre hier unnötig langsam).


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd_context.verify(password, password_hash)


def generate_agent_api_key() -> str:
    return secrets.token_urlsafe(32)


def hash_agent_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def verify_agent_api_key(api_key: str, api_key_hash: str) -> bool:
    return hmac.compare_digest(hash_agent_api_key(api_key), api_key_hash)


# --- Admin-Session-Cookie -------------------------------------------------

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="rz-checkin-admin-session")


def create_session_token(admin_user_id: str) -> str:
    return _serializer.dumps({"admin_user_id": admin_user_id})


def read_session_token(token: str) -> str | None:
    try:
        data = _serializer.loads(token, max_age=settings.session_max_age_seconds)
    except BadSignature:
        return None
    return data.get("admin_user_id")


def get_current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    token = request.cookies.get(settings.session_cookie_name)
    admin_user_id = read_session_token(token) if token else None
    if not admin_user_id:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    admin = db.get(AdminUser, admin_user_id)
    if admin is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    return admin


def check_admin_ip_allowlist(request: Request) -> None:
    allowlist = settings.admin_ip_allowlist_entries
    if not allowlist:
        return
    client_ip = request.client.host if request.client else None
    if client_ip not in allowlist:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="IP nicht zugelassen")


# --- Agent-Auth ------------------------------------------------------------


def require_agent(request: Request, db: Session = Depends(get_db)) -> Agent:
    api_key = request.headers.get("X-Agent-Key")
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-Agent-Key fehlt")

    for agent in db.scalars(select(Agent)):
        if verify_agent_api_key(api_key, agent.api_key_hash):
            return agent

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültiger Agent-API-Key")
