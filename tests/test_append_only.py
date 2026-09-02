"""DB-seitiger Manipulationsschutz: UPDATE/DELETE auf checklog sind grundsätzlich
verboten, der Wartungsjob darf innerhalb seines eigenen Transaktionsfensters löschen."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db import engine
from app.services.attendance import record_rfid_scan
from app.services.retention import purge
from tests.factories import make_employee


def _raw_connection() -> sqlite3.Connection:
    return sqlite3.connect(engine.url.database)


def test_direct_update_on_checklog_is_blocked(db):
    make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD")

    con = _raw_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("UPDATE checklog SET action = 'checkout'")
    finally:
        con.close()


def test_direct_delete_on_checklog_is_blocked_outside_retention_window(db):
    make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD")

    con = _raw_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("DELETE FROM checklog")
    finally:
        con.close()


def test_retention_job_may_delete_within_its_own_transaction(db):
    make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD")

    far_future = datetime.now(timezone.utc) + timedelta(days=3650)
    result = purge(db, now=far_future)
    assert result.checklog_entries_removed == 1

    remaining = db.execute(text("SELECT COUNT(*) FROM checklog")).scalar_one()
    assert remaining == 0

    # Das Fenster wird nach der Transaktion wieder geschlossen -- ein direktes DELETE
    # danach ist wieder blockiert. Ein BEFORE-DELETE-Trigger feuert nur für Zeilen, die
    # tatsächlich existieren, daher erst wieder einen Eintrag anlegen (INSERT ist immer
    # erlaubt), sonst würde ein DELETE auf der jetzt leeren Tabelle trivial "erfolgreich"
    # (weil wirkungslos) durchlaufen, ohne den Trigger überhaupt auszulösen.
    record_rfid_scan(db, uid="AABBCCDD", timestamp=far_future)
    con = _raw_connection()
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("DELETE FROM checklog")
    finally:
        con.close()
