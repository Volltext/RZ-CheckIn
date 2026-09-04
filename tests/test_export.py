"""CSV-Export des Logs (Admin-Bereich)."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from app.services.attendance import record_rfid_scan
from tests.factories import make_admin, make_agent, make_employee


def _login(client, db):
    make_admin(db, username="admin", password="testpass123")
    client.post("/admin/login", data={"username": "admin", "password": "testpass123"})


def test_export_requires_admin_session(client):
    response = client.get("/admin/log/export.csv", follow_redirects=False)
    assert response.status_code == 303


def test_export_contains_expected_columns_and_rows(client, db):
    _login(client, db)
    employee = make_employee(db, rfid_uid="AABBCCDD")
    t0 = datetime.now(timezone.utc)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=t0)

    response = client.get("/admin/log/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")

    reader = csv.reader(io.StringIO(response.text), delimiter=";")
    rows = list(reader)
    assert rows[0] == ["timestamp", "person_type", "name", "firma", "raum", "action", "source", "operator"]
    data_rows = rows[1:]
    assert len(data_rows) == 1
    assert data_rows[0][1] == "employee"
    # Kein Name -- die Dienstausweisnummer ist die einzige gespeicherte Kennung.
    assert data_rows[0][2] == employee.rfid_uid
    assert data_rows[0][4] == ""  # kein Raum angegeben
    assert data_rows[0][5] == "checkin"
    assert data_rows[0][6] == "rfid"


def test_export_resolves_room_name_from_agent(client, db):
    _login(client, db)
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD", raum="kiosk1")

    response = client.get("/admin/log/export.csv")
    reader = csv.reader(io.StringIO(response.text), delimiter=";")
    rows = list(reader)
    assert rows[1][4] == "Serverraum A"


def test_export_shows_placeholder_for_deleted_room(client, db):
    _login(client, db)
    make_employee(db, rfid_uid="AABBCCDD")
    # Scan mit einer Raum-Referenz, für die (mehr) kein Agent existiert -- z. B. weil der
    # Agent zwischenzeitlich gelöscht wurde. Der Log-Eintrag selbst bleibt davon
    # unangetastet (kein FK, siehe app/models.py::CheckLog.raum), nur die Anzeige braucht
    # einen Platzhalter statt eines leeren Felds.
    record_rfid_scan(db, uid="AABBCCDD", raum="laengst-geloeschter-agent")

    response = client.get("/admin/log/export.csv")
    reader = csv.reader(io.StringIO(response.text), delimiter=";")
    rows = list(reader)
    assert rows[1][4] == "(gelöschter Raum)"


def test_export_resolves_room_name_after_agent_deleted(client, db):
    """Entfernt der Admin einen Agenten (Soft-Delete, siehe
    app/services/agents.py::delete_agent), muss der CSV-Export den Raumnamen für
    bestehende Log-Einträge weiterhin auflösen können."""
    _login(client, db)
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD", raum="kiosk1")

    client.post("/admin/agenten/kiosk1/loeschen", follow_redirects=False)

    response = client.get("/admin/log/export.csv")
    reader = csv.reader(io.StringIO(response.text), delimiter=";")
    rows = list(reader)
    assert rows[1][4] == "Serverraum A"


def test_export_date_filter_excludes_out_of_range_entries(client, db):
    _login(client, db)
    make_employee(db, rfid_uid="AABBCCDD")
    old_timestamp = datetime.now(timezone.utc) - timedelta(days=10)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=old_timestamp)

    von = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    response = client.get("/admin/log/export.csv", params={"von": von})
    reader = csv.reader(io.StringIO(response.text), delimiter=";")
    rows = list(reader)
    assert len(rows) == 1  # nur die Kopfzeile, der alte Eintrag liegt außerhalb des Filters
