"""Besucher: anlegen, suchen, ein-/auschecken, DSGVO-Löschung."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import CheckLog, Visitor
from app.services.attendance import checkin_visitor, checkout_person, is_present
from app.services.settings import set_besucher_suche_aktiv
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


def test_visitor_search_input_shown_on_besucher_page_by_default(client, db):
    response = client.get("/kiosk/besucher")
    assert "Vorhandenes Profil suchen" in response.text


def test_visitor_search_input_hidden_when_disabled_in_settings(client, db):
    set_besucher_suche_aktiv(db, False)
    response = client.get("/kiosk/besucher")
    assert "Vorhandenes Profil suchen" not in response.text
    assert "Neues Besucherprofil anlegen" in response.text


def test_visitor_search_partial_returns_no_hits_when_disabled(client, db):
    make_visitor(db, vorname="Erika", nachname="Musterfrau", firma="ACME")
    set_besucher_suche_aktiv(db, False)
    response = client.get("/kiosk/besucher/suche-partial", params={"q": "Musterfrau"})
    assert response.status_code == 200
    assert "Erika Musterfrau" not in response.text


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
    visitor = make_visitor(db, vorname="Wird", nachname="Entfernt")
    checkin_visitor(db, visitor_id=visitor.id)
    checkout_person(db, person_type="visitor", person_id=visitor.id)

    log_count_before = db.scalar(
        select(CheckLog.id).where(CheckLog.person_type == "visitor", CheckLog.person_id == visitor.id)
    )
    assert log_count_before is not None

    delete_visitor(db, visitor.id)

    # Soft-Delete: die visitors-Zeile bleibt bestehen (nur geloescht_am gesetzt), damit
    # das Log den Namen weiterhin auflösen kann -- siehe app/services/visitors.py.
    reloaded = db.get(Visitor, visitor.id)
    assert reloaded is not None
    assert reloaded.geloescht_am is not None

    remaining_entries = list(
        db.scalars(select(CheckLog).where(CheckLog.person_type == "visitor", CheckLog.person_id == visitor.id))
    )
    # Das Log bleibt unangetastet -- die Einträge existieren weiterhin.
    assert len(remaining_entries) == 2


def test_delete_visitor_while_present_is_blocked(db):
    visitor = make_visitor(db)
    checkin_visitor(db, visitor_id=visitor.id)
    with pytest.raises(VisitorCurrentlyPresentError):
        delete_visitor(db, visitor.id)

    assert db.get(Visitor, visitor.id) is not None
