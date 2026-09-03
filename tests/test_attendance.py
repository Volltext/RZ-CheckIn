"""Toggle-Logik für RFID-Scans: Check-in <-> Check-out, unbekannte/inaktive Karten,
Entprellung."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import CheckLog, UnknownScan
from app.services.attendance import (
    checkin_visitor,
    count_present_employees,
    is_present,
    list_present,
    record_rfid_scan,
    run_auto_checkout,
)
from app.services.settings import set_auto_checkout_hours
from tests.factories import make_employee, make_visitor


def test_first_scan_checks_in(db):
    employee = make_employee(db, rfid_uid="AABBCCDD")
    outcome = record_rfid_scan(db, uid="AABBCCDD")
    assert outcome.result == "checkin"
    assert is_present(db, "employee", employee.id) is True


def test_second_scan_checks_out(db):
    employee = make_employee(db, rfid_uid="AABBCCDD")
    t0 = datetime.now(timezone.utc)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=t0)
    t1 = t0 + timedelta(seconds=30)
    outcome = record_rfid_scan(db, uid="AABBCCDD", timestamp=t1)
    assert outcome.result == "checkout"
    assert is_present(db, "employee", employee.id) is False


def test_toggle_sequence_checkin_checkout_checkin(db):
    make_employee(db, rfid_uid="AABBCCDD")
    t = datetime.now(timezone.utc)
    results = []
    for i in range(3):
        outcome = record_rfid_scan(db, uid="AABBCCDD", timestamp=t + timedelta(seconds=30 * i))
        results.append(outcome.result)
    assert results == ["checkin", "checkout", "checkin"]


def test_unknown_card_is_recorded_and_no_log_entry(db):
    outcome = record_rfid_scan(db, uid="DEADBEEF")
    assert outcome.result == "unknown_card"
    assert list_present(db) == []
    unknown = db.get(UnknownScan, "DEADBEEF")
    assert unknown is not None
    assert unknown.anzahl == 1

    # Zweiter Scan derselben unbekannten Karte erhöht den Zähler, statt einen zweiten
    # Eintrag anzulegen.
    record_rfid_scan(db, uid="DEADBEEF")
    db.refresh(unknown)
    assert unknown.anzahl == 2


def test_inactive_employee_card_is_rejected(db):
    employee = make_employee(db, rfid_uid="AABBCCDD", aktiv=False)
    outcome = record_rfid_scan(db, uid="AABBCCDD")
    assert outcome.result == "card_inactive"
    assert is_present(db, "employee", employee.id) is False


def test_debounce_ignores_repeated_scan_within_window(db):
    employee = make_employee(db, rfid_uid="AABBCCDD")
    t0 = datetime.now(timezone.utc)
    first = record_rfid_scan(db, uid="AABBCCDD", timestamp=t0)
    assert first.result == "checkin"

    # Innerhalb des Entprellungsfensters (Standard 5s) -> ignoriert, kein Checkout.
    second = record_rfid_scan(db, uid="AABBCCDD", timestamp=t0 + timedelta(seconds=1))
    assert second.result == "ignored"
    assert is_present(db, "employee", employee.id) is True  # Zustand blieb "eingecheckt"

    # Nach Ablauf der Entprellung togglet der nächste Scan wieder normal.
    third = record_rfid_scan(db, uid="AABBCCDD", timestamp=t0 + timedelta(seconds=6))
    assert third.result == "checkout"


def test_present_employees_have_no_name_only_count(db):
    make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD")

    present = list_present(db)
    assert len(present) == 1
    assert present[0].name is None  # keine Namen für Mitarbeiter, siehe PresentPerson
    assert count_present_employees(db) == 1


def test_auto_checkout_disabled_by_default_zero_hours(db):
    make_employee(db, rfid_uid="AABBCCDD")
    t0 = datetime.now(timezone.utc) - timedelta(days=5)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=t0)

    set_auto_checkout_hours(db, 0)
    anzahl = run_auto_checkout(db)
    assert anzahl == 0
    assert count_present_employees(db) == 1


def test_auto_checkout_checks_out_after_configured_hours(db):
    employee = make_employee(db, rfid_uid="AABBCCDD")
    visitor = make_visitor(db)
    t0 = datetime.now(timezone.utc) - timedelta(hours=15)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=t0)
    checkin_visitor(db, visitor_id=visitor.id)

    set_auto_checkout_hours(db, 12)
    anzahl = run_auto_checkout(db)
    assert anzahl == 1  # nur der Mitarbeiter überschreitet die Frist
    assert is_present(db, "employee", employee.id) is False
    assert is_present(db, "visitor", visitor.id) is True

    entry = (
        db.query(CheckLog)
        .filter_by(person_type="employee", person_id=employee.id, action="checkout")
        .one()
    )
    assert entry.source == "auto"
    assert entry.operator == "System (automatisch)"


def test_auto_checkout_leaves_recent_checkins_alone(db):
    employee = make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD", timestamp=datetime.now(timezone.utc) - timedelta(hours=1))

    set_auto_checkout_hours(db, 12)
    anzahl = run_auto_checkout(db)
    assert anzahl == 0
    assert is_present(db, "employee", employee.id) is True
