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
from sqlalchemy.pool import NullPool

from app.config import get_settings

settings = get_settings()

# check_same_thread=False: FastAPI kann Requests aus verschiedenen Threads bedienen;
# wir serialisieren Schreibzugriffe ohnehin über WAL + kurze Transaktionen.
#
# poolclass=NullPool ist bewusst gewählt: mit dem SQLAlchemy-Standardpool (QueuePool)
# hält der lange laufende Server-Prozess wiederverwendete Verbindungen, die unter
# bestimmten Bedingungen einen veralteten WAL-Lesestand behalten -- Schreibzugriffe aus
# einem ANDEREN Prozess (z.B. `podman exec ... python -m app.cli create-agent` oder der
# tägliche `purge`-Wartungsjob, siehe deploy/rz-checkin-retention.service) waren dadurch
# beobachtbar erst nach einem Server-Neustart sichtbar. NullPool öffnet für jede Session
# eine frische SQLite-Verbindung (für eine lokale Datei praktisch kostenlos) und sieht
# damit immer den zuletzt committeten Stand.
engine = create_engine(
    f"sqlite:///{settings.database_file}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
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


def _legacy_columns(cursor, table: str) -> set[str]:
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _table_exists(cursor, table: str) -> bool:
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def _rename_away_if_outdated(cursor) -> set[str]:
    """Erkennt Tabellen aus einer älteren Version des Datenmodells und benennt sie um,
    BEVOR `create_all()` läuft -- `create_all()` legt Tabellen nur an, wenn sie noch
    nicht existieren, ändert aber nie Spalten einer bestehenden Tabelle. Umbenennen lässt
    `create_all()` die Tabelle frisch mit dem aktuellen Schema anlegen; die Daten werden
    danach in `_migrate_renamed_tables()` übernommen (mit Spaltenanpassung).

    Gibt die Namen der umbenannten Tabellen zurück (ohne "_legacy"-Suffix).
    """
    renamed: set[str] = set()

    if _table_exists(cursor, "employees") and "vorname" in _legacy_columns(cursor, "employees"):
        # Altes Schema speicherte Vor-/Nachname -- laut Fachvorgabe darf das nicht mehr
        # gespeichert werden. Die Namen werden beim Migrieren bewusst NICHT übernommen.
        cursor.execute("ALTER TABLE employees RENAME TO employees_legacy")
        renamed.add("employees")

    if _table_exists(cursor, "checklog"):
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='checklog'")
        row = cursor.fetchone()
        if row and "'auto'" not in row[0]:
            # Alte CHECK-Constraint kannte source='auto' (automatisches Auschecken) noch
            # nicht. SQLite kann CHECK-Constraints nicht per ALTER TABLE ändern, daher
            # ebenfalls Tabelle neu anlegen und Daten unverändert übernehmen.
            cursor.execute("ALTER TABLE checklog RENAME TO checklog_legacy")
            renamed.add("checklog")

    return renamed


def _migrate_renamed_tables(cursor, renamed: set[str]) -> None:
    if "employees" in renamed:
        cursor.execute(
            "INSERT INTO employees (id, rfid_uid, aktiv, erstellt_am) "
            "SELECT id, rfid_uid, aktiv, erstellt_am FROM employees_legacy"
        )
        cursor.execute("DROP TABLE employees_legacy")

    if "checklog" in renamed:
        cursor.execute(
            "INSERT INTO checklog (id, person_type, person_id, action, source, timestamp, operator) "
            "SELECT id, person_type, person_id, action, source, timestamp, operator FROM checklog_legacy"
        )
        cursor.execute("DROP TABLE checklog_legacy")


def init_db() -> None:
    """Legt Tabellen (falls nötig) und den Append-only-Schutz an. Idempotent.

    Migriert außerdem bestehende Datenbanken auf das aktuelle Schema (siehe
    `_rename_away_if_outdated`) -- es gibt bewusst kein separates Migrationswerkzeug
    (Alembic o.ä.), da die Schemaänderungen selten und einfach genug sind, um beim
    normalen Start automatisch zu laufen."""
    from app import models  # noqa: F401  (Modelle für create_all registrieren)
    from app.models import Base

    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        renamed = _rename_away_if_outdated(cursor)
        raw_connection.commit()
    finally:
        raw_connection.close()

    Base.metadata.create_all(bind=engine)

    raw_connection = engine.raw_connection()
    try:
        cursor = raw_connection.cursor()
        if renamed:
            _migrate_renamed_tables(cursor, renamed)
            raw_connection.commit()

        # Die Trigger-Bodies enthalten selbst Semikolons (BEGIN ... ; ... ; END;), daher
        # reicht ein einzelnes text()-execute() nicht (SQLite/DB-API führt pro Aufruf nur
        # ein Statement aus). executescript() auf der rohen DBAPI-Verbindung parst das
        # komplette Skript korrekt inkl. Trigger-Bodies.
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
