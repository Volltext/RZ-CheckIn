"""Engine/Session-Setup sowie der Append-only-Schutz für `checklog`.

Der Append-only-Schutz besteht aus zwei SQLite-Triggern (siehe `_APPEND_ONLY_SQL`), die
UPDATE grundsätzlich verbieten und DELETE nur zulassen, solange die Hilfstabelle
`retention_window` einen Eintrag enthält. Nur der Wartungsjob (app/services/retention.py)
öffnet dieses Fenster für die Dauer einer einzigen Transaktion. Die Anwendung selbst führt
nie ein DELETE oder UPDATE auf `checklog` aus.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()

# check_same_thread=False: FastAPI kann Requests aus verschiedenen Threads bedienen;
# wir serialisieren Schreibzugriffe ohnehin über WAL + kurze Transaktionen.
engine = create_engine(
    f"sqlite:///{settings.database_file}",
    connect_args={"check_same_thread": False},
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


_APPEND_ONLY_SQL = """
CREATE TABLE IF NOT EXISTS retention_window (
    opened_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS checklog_no_update
BEFORE UPDATE ON checklog
BEGIN
    SELECT RAISE(ABORT, 'checklog ist append-only: UPDATE ist nicht erlaubt');
END;

CREATE TRIGGER IF NOT EXISTS checklog_no_delete
BEFORE DELETE ON checklog
WHEN NOT EXISTS (SELECT 1 FROM retention_window)
BEGIN
    SELECT RAISE(ABORT, 'checklog ist append-only: DELETE nur durch den Wartungsjob');
END;
"""


def init_db() -> None:
    """Legt Tabellen (falls nötig) und den Append-only-Schutz an. Idempotent."""
    from app import models  # noqa: F401  (Modelle für create_all registrieren)
    from app.models import Base

    Base.metadata.create_all(bind=engine)

    # Die Trigger-Bodies enthalten selbst Semikolons (BEGIN ... ; ... ; END;), daher
    # reicht ein einzelnes text()-execute() nicht (SQLite/DB-API führt pro Aufruf nur
    # ein Statement aus). executescript() auf der rohen DBAPI-Verbindung parst das
    # komplette Skript korrekt inkl. Trigger-Bodies.
    raw_connection = engine.raw_connection()
    try:
        raw_connection.executescript(_APPEND_ONLY_SQL)
        raw_connection.commit()
    finally:
        raw_connection.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI-Dependency: eine Session pro Request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Für Nutzung außerhalb von Requests (CLI, Wartungsjobs)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
