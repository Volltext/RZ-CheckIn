"""Kiosk-Selbstbedienung: manuelles Auschecken für alle, Selbstregistrierung von
Mitarbeitern mit noch unbekannter Karte."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models import CheckLog, Employee, UnknownScan
from app.services.attendance import checkin_visitor, is_present, record_rfid_scan
from tests.factories import make_employee, make_visitor


def test_manual_checkout_via_kiosk_for_employee(client, db):
    employee = make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD")
    assert is_present(db, "employee", employee.id) is True

    response = client.post(f"/kiosk/auschecken/employee/{employee.id}", follow_redirects=False)
    assert response.status_code == 303
    assert is_present(db, "employee", employee.id) is False

    entry = db.scalar(
        select(CheckLog)
        .where(CheckLog.person_type == "employee", CheckLog.person_id == employee.id)
        .order_by(CheckLog.timestamp.desc())
    )
    assert entry.action == "checkout"
    assert entry.operator == "Kiosk (manuell)"


def test_manual_checkout_via_kiosk_for_visitor(client, db):
    visitor = make_visitor(db)
    checkin_visitor(db, visitor_id=visitor.id)

    response = client.post(f"/kiosk/auschecken/visitor/{visitor.id}", follow_redirects=False)
    assert response.status_code == 303
    assert is_present(db, "visitor", visitor.id) is False


def test_manual_checkout_not_present_is_a_no_op(client, db):
    employee = make_employee(db, rfid_uid="AABBCCDD")
    response = client.post(f"/kiosk/auschecken/employee/{employee.id}", follow_redirects=False)
    assert response.status_code == 303  # kein Fehler, einfach nichts zu tun


def test_manual_checkout_rejects_unknown_person_type(client):
    response = client.post("/kiosk/auschecken/unbekannt/some-id")
    assert response.status_code == 404


def test_self_registration_creates_employee_and_checks_in(client, db):
    db.add(UnknownScan(uid="AABBCC99", anzahl=2, zuletzt_gesehen=datetime.now(timezone.utc)))
    db.commit()

    response = client.post(
        "/kiosk/mitarbeiter/registrieren",
        data={"vorname": "Neu", "nachname": "Registriert", "rfid_uid": "aabbcc99"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    employee = db.scalar(select(Employee).where(Employee.rfid_uid == "AABBCC99"))
    assert employee is not None
    assert employee.vorname == "Neu"
    assert is_present(db, "employee", employee.id) is True

    # Der Eintrag in unknown_scans wurde aufgeräumt.
    assert db.get(UnknownScan, "AABBCC99") is None


def test_self_registration_conflict_does_not_duplicate(client, db):
    existing = make_employee(db, vorname="Erste", nachname="Person", rfid_uid="AABBCC55")

    response = client.post(
        "/kiosk/mitarbeiter/registrieren",
        data={"vorname": "Zweite", "nachname": "Person", "rfid_uid": "AABBCC55"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    employees_with_uid = list(db.scalars(select(Employee).where(Employee.rfid_uid == "AABBCC55")))
    assert len(employees_with_uid) == 1
    assert employees_with_uid[0].id == existing.id
