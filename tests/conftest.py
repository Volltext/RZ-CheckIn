"""Pytest-Fixtures. Setzt den DB-Pfad auf eine temporäre Datei, BEVOR app.db importiert
wird (die Engine wird beim Modul-Import gebunden) und liefert für jeden Test eine leere
Datenbank mit frisch angelegtem Schema + Append-only-Trigger."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TEST_DB_DIR = tempfile.mkdtemp(prefix="rz-checkin-tests-")
os.environ.setdefault("RZ_DATABASE_PATH", str(Path(_TEST_DB_DIR) / "test.db"))
os.environ.setdefault("RZ_SESSION_SECRET", "test-only-secret")
os.environ.setdefault("RZ_SCAN_DEBOUNCE_SECONDS", "5")
os.environ.setdefault("RZ_AGENT_OFFLINE_THRESHOLD_SECONDS", "90")
os.environ.setdefault("RZ_RETENTION_DAYS", "730")
os.environ.setdefault("RZ_ADMIN_PASSWORD", "")  # kein automatischer Admin-Bootstrap in Tests

from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_database():
    """Löscht die SQLite-Testdatenbank und legt Schema + Trigger neu an — jeder Test
    startet damit garantiert leer und unabhängig von anderen Tests."""
    engine.dispose()
    db_path = engine.url.database
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()
    init_db()
    yield
    engine.dispose()


@pytest.fixture
def db():
    """Rohe SQLAlchemy-Session für Testdaten-Setup. Tests committen selbst, analog zum
    Muster in den Services (kein impliziter Commit beim Fixture-Teardown, damit
    Änderungen für den TestClient garantiert sichtbar sind, bevor der Test sie braucht)."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
