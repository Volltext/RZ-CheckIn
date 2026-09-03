"""Kiosk-Selbstbedienung: manuelles Auschecken für Externe, Registrierung neuer
Dienstausweise mit noch unbekannter Karte (ohne Namenseingabe -- siehe
app/models.py::Employee)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.models import CheckLog, Employee, UnknownScan
from app.services.attendance import checkin_visitor, is_present, record_rfid_scan
from tests.factories import make_employee, make_visitor


def test_manual_checkout_via_kiosk_for_visitor(client, db):
    visitor = make_visitor(db)
    checkin_visitor(db, visitor_id=visitor.id)

    response = client.post(f"/kiosk/auschecken/visitor/{visitor.id}", follow_redirects=False)
    assert response.status_code == 303
    assert is_present(db, "visitor", visitor.id) is False


def test_manual_checkout_visitor_not_present_is_a_no_op(client, db):
    visitor = make_visitor(db)
    response = client.post(f"/kiosk/auschecken/visitor/{visitor.id}", follow_redirects=False)
    assert response.status_code == 303  # kein Fehler, einfach nichts zu tun


def test_kiosk_has_no_manual_checkout_route_for_employees(client, db):
    employee = make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD")
    assert is_present(db, "employee", employee.id) is True

    # Es gibt bewusst keine Kiosk-Route mehr, um einzelne Mitarbeiter auszuchecken --
    # keine Namen mehr in der Live-Übersicht, also auch keine Zeile zum Anklicken.
    response = client.post(f"/kiosk/auschecken/employee/{employee.id}")
    assert response.status_code == 404
    assert is_present(db, "employee", employee.id) is True


def test_card_registration_creates_employee_without_name_and_checks_in(client, db):
    db.add(UnknownScan(uid="AABBCC99", anzahl=2, zuletzt_gesehen=datetime.now(timezone.utc)))
    db.commit()

    response = client.post(
        "/kiosk/mitarbeiter/registrieren",
        data={"rfid_uid": "aabbcc99"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    employee = db.scalar(select(Employee).where(Employee.rfid_uid == "AABBCC99"))
    assert employee is not None
    assert not hasattr(employee, "vorname")
    assert is_present(db, "employee", employee.id) is True

    # Der Eintrag in unknown_scans wurde aufgeräumt.
    assert db.get(UnknownScan, "AABBCC99") is None


def test_card_registration_conflict_does_not_duplicate(client, db):
    existing = make_employee(db, rfid_uid="AABBCC55")

    response = client.post(
        "/kiosk/mitarbeiter/registrieren",
        data={"rfid_uid": "AABBCC55"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    employees_with_uid = list(db.scalars(select(Employee).where(Employee.rfid_uid == "AABBCC55")))
    assert len(employees_with_uid) == 1
    assert employees_with_uid[0].id == existing.id
