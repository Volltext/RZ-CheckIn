"""GET /api/status: gemischte Live-Übersicht aus Mitarbeitern (ohne Namen) und Externen."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.attendance import checkin_visitor, record_rfid_scan
from tests.factories import make_employee, make_visitor


def test_status_lists_only_present_people_sorted_by_checkin_time(client, db):
    employee = make_employee(db, rfid_uid="AABBCCDD")
    other_employee = make_employee(db, rfid_uid="11223344")
    visitor = make_visitor(db, vorname="Extern", nachname="Besucher", firma="Fremdfirma GmbH")

    t0 = datetime.now(timezone.utc)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=t0)
    checkin_visitor(db, visitor_id=visitor.id)
    record_rfid_scan(db, uid="11223344", timestamp=t0 + timedelta(minutes=5))
    # Checkin + Checkout -> darf nicht mehr in der Liste auftauchen.
    record_rfid_scan(db, uid="AABBCCDD", timestamp=t0 + timedelta(minutes=10))

    response = client.get("/api/status")
    assert response.status_code == 200
    entries = response.json()

    ids = [e["person_id"] for e in entries]
    assert employee.id not in ids  # wurde wieder ausgecheckt
    assert visitor.id in ids
    assert other_employee.id in ids

    employee_entries = [e for e in entries if e["person_type"] == "employee"]
    assert all(e["name"] is None for e in employee_entries)  # keine Namen für Mitarbeiter

    visitor_entry = next(e for e in entries if e["person_type"] == "visitor")
    assert visitor_entry["firma"] == "Fremdfirma GmbH"
    assert visitor_entry["name"] == visitor.voller_name

    # Sortierung nach Check-in-Zeit: Besucher (t0) vor other_employee (t0+5min).
    assert entries[0]["person_type"] == "visitor"
    assert entries[1]["person_id"] == other_employee.id
