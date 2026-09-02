"""Besucher: anlegen, suchen, ein-/auschecken, DSGVO-Löschung."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import CheckLog, Visitor
from app.services.attendance import checkin_visitor, checkout_person, is_present
from app.services.visitors import VisitorCurrentlyPresentError, delete_visitor
from tests.factories import make_visitor


def test_checkin_and_checkout_visitor(db):
    visitor = make_visitor(db)
    checkin_visitor(db, visitor_id=visitor.id)
    assert is_present(db, "visitor", visitor.id) is True

    checkout_person(db, person_type="visitor", person_id=visitor.id)
    assert is_present(db, "visitor", visitor.id) is False


def test_checkin_already_checked_in_visitor_raises(db):
    visitor = make_visitor(db)
    checkin_visitor(db, visitor_id=visitor.id)
    with pytest.raises(ValueError):
        checkin_visitor(db, visitor_id=visitor.id)


def test_checkout_not_present_visitor_raises(db):
    visitor = make_visitor(db)
    with pytest.raises(ValueError):
        checkout_person(db, person_type="visitor", person_id=visitor.id)


def test_visitor_search_via_kiosk_endpoint(client, db):
    make_visitor(db, vorname="Erika", nachname="Musterfrau", firma="ACME")
    response = client.get("/kiosk/besucher/suche-partial", params={"q": "Musterfrau"})
    assert response.status_code == 200
    assert "Erika Musterfrau" in response.text


def test_visitor_create_via_kiosk_checks_in(client):
    response = client.post(
        "/kiosk/besucher/anlegen",
        data={"vorname": "Neu", "nachname": "Angelegt", "firma": "Fremdfirma", "telefonnummer": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    status = client.get("/api/status").json()
    names = [p["name"] for p in status]
    assert "Neu Angelegt" in names


def test_delete_visitor_removes_profile_but_keeps_log(db):
    visitor = make_visitor(db, vorname="Wird", nachname="Geloescht")
    checkin_visitor(db, visitor_id=visitor.id)
    checkout_person(db, person_type="visitor", person_id=visitor.id)

    log_count_before = db.scalar(
        select(CheckLog.id).where(CheckLog.person_type == "visitor", CheckLog.person_id == visitor.id)
    )
    assert log_count_before is not None

    delete_visitor(db, visitor.id)

    assert db.get(Visitor, visitor.id) is None
    remaining_entries = list(
        db.scalars(select(CheckLog).where(CheckLog.person_type == "visitor", CheckLog.person_id == visitor.id))
    )
    # Das Log bleibt unangetastet -- die Einträge existieren weiterhin, nur das Profil ist weg.
    assert len(remaining_entries) == 2


def test_delete_visitor_while_present_is_blocked(db):
    visitor = make_visitor(db)
    checkin_visitor(db, visitor_id=visitor.id)
    with pytest.raises(VisitorCurrentlyPresentError):
        delete_visitor(db, visitor.id)

    assert db.get(Visitor, visitor.id) is not None
