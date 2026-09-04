"""Automatische Schema-Migration beim Start (app/db.py::init_db): bestehende
Datenbanken aus einer älteren Version werden auf das aktuelle Schema gehoben, ohne
Datenverlust bei den Log-Einträgen (Namen von Mitarbeitern werden dabei bewusst NICHT
übernommen -- genau das ist die fachliche Vorgabe)."""

from __future__ import annotations

import sqlite3

from sqlalchemy import select

from app.db import engine, init_db
from app.models import Agent, CheckLog, Employee


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


def test_legacy_checklog_without_raum_column_gets_it_added_nullable():
    """Bestehende Installationen vor Einführung der Technikraum-Zuordnung (siehe
    app/models.py::CheckLog.raum) haben noch kein `raum`-Feld -- init_db() muss die
    Spalte additiv per ALTER TABLE nachziehen, ohne bestehende Zeilen zu verlieren."""
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
                source VARCHAR(20) NOT NULL CHECK (source IN ('rfid', 'manual', 'auto')),
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
    assert row.raum is None

    # Ein zweiter init_db()-Lauf (z. B. Container-Neustart) darf nicht an einer bereits
    # vorhandenen Spalte scheitern (idempotent).
    init_db()


def test_legacy_agents_table_without_geloescht_am_column_gets_it_added_nullable():
    """Bestehende Installationen vor Einführung des Soft-Deletes für Agenten (siehe
    app/models.py::Agent.geloescht_am) haben noch kein `geloescht_am`-Feld -- init_db()
    muss die Spalte additiv per ALTER TABLE nachziehen, ohne bestehende Zeilen (samt
    api_key_hash) zu verlieren."""
    con = _raw_connection()
    try:
        con.execute("DELETE FROM agents")
        con.execute("DROP TABLE agents")
        con.execute(
            """
            CREATE TABLE agents (
                agent_id VARCHAR(64) PRIMARY KEY,
                bezeichnung VARCHAR(200) NOT NULL,
                api_key_hash VARCHAR(200) NOT NULL,
                erstellt_am DATETIME,
                last_seen DATETIME
            )
            """
        )
        con.execute(
            "INSERT INTO agents (agent_id, bezeichnung, api_key_hash, erstellt_am, last_seen) "
            "VALUES ('kiosk1', 'Serverraum A', 'hash', '2024-01-01 00:00:00+00:00', NULL)"
        )
        con.commit()
    finally:
        con.close()

    init_db()

    with engine.connect() as conn:
        row = conn.execute(select(Agent).where(Agent.agent_id == "kiosk1")).one()
    assert row.bezeichnung == "Serverraum A"
    assert row.api_key_hash == "hash"
    assert row.geloescht_am is None

    # Idempotent -- ein zweiter Lauf darf nicht an der bereits vorhandenen Spalte scheitern.
    init_db()
