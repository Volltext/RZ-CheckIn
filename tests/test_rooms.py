"""Technikraum-Split: Raumzuordnung über den scannenden Agenten (Mitarbeiter) bzw. eine
explizite Auswahl am Kiosk (externe Besucher), Split-Ansicht auf der Kiosk-Startseite."""

from __future__ import annotations

from app.models import CheckLog
from app.services.attendance import (
    checkin_visitor,
    presence_by_room,
    record_rfid_scan,
)
from tests.factories import make_agent, make_employee, make_visitor


def test_record_rfid_scan_stores_room_on_checklog(db):
    employee = make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD", raum="kiosk1")

    entry = db.query(CheckLog).filter_by(person_type="employee", person_id=employee.id).one()
    assert entry.raum == "kiosk1"


def test_checkin_visitor_stores_room_on_checklog(db):
    visitor = make_visitor(db)
    checkin_visitor(db, visitor_id=visitor.id, raum="kiosk2")

    entry = db.query(CheckLog).filter_by(person_type="visitor", person_id=visitor.id).one()
    assert entry.raum == "kiosk2"


def test_presence_by_room_groups_by_room_key(db):
    make_agent(db, agent_id="kiosk1", bezeichnung="Raum 1")
    make_agent(db, agent_id="kiosk2", bezeichnung="Raum 2")
    e1 = make_employee(db, rfid_uid="AABBCCDD")
    e2 = make_employee(db, rfid_uid="11223344")
    visitor = make_visitor(db)

    record_rfid_scan(db, uid="AABBCCDD", raum="kiosk1")
    record_rfid_scan(db, uid="11223344", raum="kiosk2")
    checkin_visitor(db, visitor_id=visitor.id, raum="kiosk1")

    grouped = presence_by_room(db)
    assert {p.person_id for p in grouped["kiosk1"]} == {e1.id, visitor.id}
    assert {p.person_id for p in grouped["kiosk2"]} == {e2.id}


def test_presence_by_room_groups_missing_room_under_none(db):
    employee = make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD")  # kein raum angegeben

    grouped = presence_by_room(db)
    assert grouped[None][0].person_id == employee.id


def test_dashboard_shows_two_rooms_side_by_side(client, db):
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    make_agent(db, agent_id="kiosk2", bezeichnung="Serverraum B")
    make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD", raum="kiosk1")

    response = client.get("/")
    assert response.status_code == 200
    assert "Serverraum A" in response.text
    assert "Serverraum B" in response.text
    # Nur der Raum mit der Person zeigt einen aktiven Punkt/Zähler > 0.
    assert response.text.index("Serverraum A") < response.text.index("Serverraum B")


def test_dashboard_visitor_appears_only_in_its_own_room(client, db):
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    make_agent(db, agent_id="kiosk2", bezeichnung="Serverraum B")
    visitor = make_visitor(db, vorname="Erika", nachname="Testfrau")
    checkin_visitor(db, visitor_id=visitor.id, raum="kiosk2")

    response = client.get("/")
    room_a = response.text.split("Serverraum A")[1].split("Serverraum B")[0]
    room_b = response.text.split("Serverraum B")[1]
    assert "Erika Testfrau" not in room_a
    assert "Erika Testfrau" in room_b


def test_dashboard_with_no_agents_shows_hint_and_no_crash(client, db):
    response = client.get("/")
    assert response.status_code == 200


def test_besucher_seite_shows_room_picker_when_multiple_agents(client, db):
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    make_agent(db, agent_id="kiosk2", bezeichnung="Serverraum B")

    response = client.get("/kiosk/besucher")
    assert "In welchem Raum" in response.text
    assert "Serverraum A" in response.text
    assert "Serverraum B" in response.text


def test_besucher_seite_skips_picker_with_single_agent(client, db):
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")

    response = client.get("/kiosk/besucher")
    assert "In welchem Raum" not in response.text
    assert "Neues Besucherprofil anlegen" in response.text


def test_besucher_seite_skips_picker_with_no_agents(client, db):
    response = client.get("/kiosk/besucher")
    assert "In welchem Raum" not in response.text
    assert "Neues Besucherprofil anlegen" in response.text


def test_besucher_anlegen_with_room_checks_in_to_that_room(client, db):
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    make_agent(db, agent_id="kiosk2", bezeichnung="Serverraum B")

    response = client.post(
        "/kiosk/besucher/anlegen",
        data={
            "vorname": "Neu",
            "nachname": "Angelegt",
            "firma": "",
            "telefonnummer": "",
            "raum": "kiosk2",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    entry = db.query(CheckLog).filter_by(person_type="visitor", action="checkin").one()
    assert entry.raum == "kiosk2"


def test_besucher_anlegen_with_unknown_room_is_ignored(client, db):
    response = client.post(
        "/kiosk/besucher/anlegen",
        data={
            "vorname": "Neu",
            "nachname": "Angelegt",
            "firma": "",
            "telefonnummer": "",
            "raum": "nicht-vorhanden",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    entry = db.query(CheckLog).filter_by(person_type="visitor", action="checkin").one()
    assert entry.raum is None


def test_besucher_einchecken_stores_selected_room(client, db):
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    visitor = make_visitor(db)

    response = client.post(
        "/kiosk/besucher/einchecken",
        data={"visitor_id": visitor.id, "raum": "kiosk1"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    entry = db.query(CheckLog).filter_by(person_type="visitor", person_id=visitor.id, action="checkin").one()
    assert entry.raum == "kiosk1"


def test_rfid_checkin_via_api_tags_checklog_with_agent_room(client, db):
    _, api_key = make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")

    response = client.post(
        "/api/checkin/rfid",
        json={"agent_id": "kiosk1", "uid": "AABBCCDD"},
        headers={"X-Agent-Key": api_key},
    )
    assert response.status_code == 200
    assert response.json()["result"] == "unknown_card"

    # Danach direkt am Kiosk registrieren -- die Raumzuordnung kommt aus dem Scan-Event
    # (siehe app/services/feedback.py::ScanFeedbackEvent.agent_id).
    reg = client.post(
        "/kiosk/mitarbeiter/registrieren",
        data={"rfid_uid": "AABBCCDD", "raum": "kiosk1"},
        follow_redirects=False,
    )
    assert reg.status_code == 303

    entry = db.query(CheckLog).filter_by(person_type="employee", action="checkin").one()
    assert entry.raum == "kiosk1"


def test_mitarbeiter_registrieren_with_unknown_room_is_ignored(client, db):
    response = client.post(
        "/kiosk/mitarbeiter/registrieren",
        data={"rfid_uid": "AABBCCDD", "raum": "kein-agent"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    entry = db.query(CheckLog).filter_by(person_type="employee", action="checkin").one()
    assert entry.raum is None
