"""CSV-Export des Logs (Admin-Bereich)."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from app.services.attendance import record_rfid_scan
from tests.factories import make_admin, make_employee


def _login(client, db):
    make_admin(db, username="admin", password="testpass123")
    client.post("/admin/login", data={"username": "admin", "password": "testpass123"})


def test_export_requires_admin_session(client):
    response = client.get("/admin/log/export.csv", follow_redirects=False)
    assert response.status_code == 303


def test_export_contains_expected_columns_and_rows(client, db):
    _login(client, db)
    employee = make_employee(db, vorname="Max", nachname="Mustermann", rfid_uid="AABBCCDD")
    t0 = datetime.now(timezone.utc)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=t0)

    response = client.get("/admin/log/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

    reader = csv.reader(io.StringIO(response.text), delimiter=";")
    rows = list(reader)
    assert rows[0] == ["timestamp", "person_type", "name", "firma", "action", "source", "operator"]
    data_rows = rows[1:]
    assert len(data_rows) == 1
    assert data_rows[0][1] == "employee"
    assert data_rows[0][2] == employee.voller_name
    assert data_rows[0][4] == "checkin"
    assert data_rows[0][5] == "rfid"


def test_export_date_filter_excludes_out_of_range_entries(client, db):
    _login(client, db)
    make_employee(db, vorname="Max", nachname="Mustermann", rfid_uid="AABBCCDD")
    old_timestamp = datetime.now(timezone.utc) - timedelta(days=10)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=old_timestamp)

    von = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    response = client.get("/admin/log/export.csv", params={"von": von})
    reader = csv.reader(io.StringIO(response.text), delimiter=";")
    rows = list(reader)
    assert len(rows) == 1  # nur die Kopfzeile, der alte Eintrag liegt außerhalb des Filters
