"""Log-Ansicht im Admin-Bereich (/admin/log): zeigt jetzt auch den Raum je Eintrag."""

from __future__ import annotations

from app.services.attendance import checkin_visitor, checkout_person, record_rfid_scan
from app.services.visitors import delete_visitor
from tests.factories import make_admin, make_agent, make_employee, make_visitor


def _login(client, db):
    make_admin(db, username="admin", password="testpass123")
    client.post("/admin/login", data={"username": "admin", "password": "testpass123"})


def test_log_ansicht_shows_room_name(client, db):
    _login(client, db)
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD", raum="kiosk1")

    response = client.get("/admin/log")
    assert response.status_code == 200
    assert "Serverraum A" in response.text


def test_log_ansicht_shows_placeholder_for_deleted_room(client, db):
    _login(client, db)
    make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD", raum="laengst-geloeschter-agent")

    response = client.get("/admin/log")
    assert response.status_code == 200
    assert "(gelöschter Raum)" in response.text


def test_deleting_agent_keeps_log_entries_with_room_name(client, db):
    """Entfernt der Admin einen Agenten (Raum), bleibt die agents-Zeile als Soft-Delete
    bestehen (siehe app/services/agents.py::delete_agent) -- die Log-Ansicht zeigt den
    Raumnamen deshalb weiterhin an, statt eines Platzhalters. Der Platzhalter
    "(gelöschter Raum)" gilt nur für Raum-IDs, die nie als Agent existiert haben (siehe
    test_log_ansicht_shows_placeholder_for_deleted_room)."""
    _login(client, db)
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD", raum="kiosk1")

    before = client.get("/admin/log")
    assert "Serverraum A" in before.text

    delete_response = client.post("/admin/agenten/kiosk1/loeschen", follow_redirects=False)
    assert delete_response.status_code == 303

    after = client.get("/admin/log")
    assert after.status_code == 200
    assert "Check-in" in after.text
    assert "Serverraum A" in after.text
    assert "(gelöschter Raum)" not in after.text


def test_deleted_agent_no_longer_shown_in_active_agentenliste(client, db):
    """Das Entfernen blendet den Agenten trotzdem aus der aktiven Liste/Raumauswahl
    aus -- Soft-Delete heißt nicht, dass der Raum weiter aktiv nutzbar bleibt."""
    _login(client, db)
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    client.post("/admin/agenten/kiosk1/loeschen", follow_redirects=False)

    response = client.get("/admin/agenten")
    assert response.status_code == 200
    assert "Serverraum A" not in response.text


def test_deleting_visitor_keeps_log_entries_with_name(client, db):
    """Entfernt der Admin ein Besucherprofil aus der Kontaktliste, bleibt die
    visitors-Zeile als Soft-Delete bestehen (siehe app/services/visitors.py::
    delete_visitor) -- die Log-Ansicht zeigt den Namen deshalb weiterhin an, statt eines
    Platzhalters. Der Platzhalter "(gelöschtes Profil)" erscheint erst nach der
    endgültigen DSGVO-Löschung durch die Aufbewahrungsfrist (app/services/retention.py)."""
    _login(client, db)
    visitor = make_visitor(db, vorname="Erika", nachname="Testfrau")
    checkin_visitor(db, visitor_id=visitor.id)
    checkout_person(db, person_type="visitor", person_id=visitor.id)

    delete_visitor(db, visitor.id)

    response = client.get("/admin/log")
    assert response.status_code == 200
    assert "Erika Testfrau" in response.text
    assert "(gelöschtes Profil)" not in response.text
    assert response.text.count("Check-in") + response.text.count("Check-out") >= 1


def test_deleted_visitor_no_longer_shown_in_active_besucherliste(client, db):
    """Das Entfernen blendet das Profil trotzdem aus der aktiven Kontaktliste aus --
    Soft-Delete heißt nicht, dass die Person weiter als aktiver Besucher auftaucht."""
    _login(client, db)
    visitor = make_visitor(db, vorname="Erika", nachname="Testfrau")
    delete_visitor(db, visitor.id)

    response = client.get("/admin/besucher")
    assert response.status_code == 200
    assert "Erika" not in response.text
