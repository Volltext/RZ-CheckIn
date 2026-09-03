"""Automatische Schema-Migration beim Start (app/db.py::init_db): bestehende
Datenbanken aus einer älteren Version werden auf das aktuelle Schema gehoben, ohne
Datenverlust bei den Log-Einträgen (Namen von Mitarbeitern werden dabei bewusst NICHT
übernommen -- genau das ist die fachliche Vorgabe)."""

from __future__ import annotations

import sqlite3

from sqlalchemy import select

from app.db import engine, init_db
from app.models import CheckLog, Employee


def _raw_connection() -> sqlite3.Connection:
    return sqlite3.connect(engine.url.database)


def test_legacy_employees_table_is_migrated_dropping_names(db):
    con = _raw_connection()
    try:
        con.execute("DELETE FROM employees")
        con.execute("DROP TABLE employees")
        con.execute(
            """
            CREATE TABLE employees (
                id VARCHAR(36) PRIMARY KEY,
                vorname VARCHAR(200) NOT NULL,
                nachname VARCHAR(200) NOT NULL,
                rfid_uid VARCHAR(64) UNIQUE,
                aktiv BOOLEAN,
                erstellt_am DATETIME
            )
            """
        )
        con.execute(
            "INSERT INTO employees (id, vorname, nachname, rfid_uid, aktiv, erstellt_am) "
            "VALUES ('emp-1', 'Max', 'Mustermann', 'AABBCCDD', 1, '2024-01-01 00:00:00+00:00')"
        )
        con.commit()
    finally:
        con.close()

    init_db()

    employee = db.get(Employee, "emp-1")
    assert employee is not None
    assert employee.rfid_uid == "AABBCCDD"
    assert employee.aktiv is True
    assert not hasattr(employee, "vorname")


def test_legacy_checklog_check_constraint_is_migrated_keeping_rows():
    con = _raw_connection()
    try:
        con.execute("DELETE FROM checklog")
        con.execute("DROP TABLE checklog")
        con.execute(
            """
            CREATE TABLE checklog (
                id VARCHAR(36) PRIMARY KEY,
                person_type VARCHAR(20) NOT NULL CHECK (person_type IN ('employee', 'visitor')),
                person_id VARCHAR(36) NOT NULL,
                action VARCHAR(20) NOT NULL CHECK (action IN ('checkin', 'checkout')),
                source VARCHAR(20) NOT NULL CHECK (source IN ('rfid', 'manual')),
                "timestamp" DATETIME,
                operator VARCHAR(200)
            )
            """
        )
        con.execute(
            "INSERT INTO checklog (id, person_type, person_id, action, source, \"timestamp\", operator) "
            "VALUES ('log-1', 'employee', 'emp-1', 'checkin', 'rfid', '2024-01-01 00:00:00+00:00', NULL)"
        )
        con.commit()
    finally:
        con.close()

    init_db()

    with engine.connect() as conn:
        row = conn.execute(select(CheckLog).where(CheckLog.id == "log-1")).one()
    assert row.person_id == "emp-1"
    assert row.source == "rfid"

    # Die neue Constraint erlaubt jetzt auch 'auto' -- ein direkter INSERT darf nicht
    # mehr an der alten Prüfung scheitern.
    con = _raw_connection()
    try:
        con.execute(
            "INSERT INTO checklog (id, person_type, person_id, action, source, \"timestamp\", operator) "
            "VALUES ('log-2', 'employee', 'emp-1', 'checkout', 'auto', '2024-01-02 00:00:00+00:00', 'System (automatisch)')"
        )
        con.commit()
    finally:
        con.close()
