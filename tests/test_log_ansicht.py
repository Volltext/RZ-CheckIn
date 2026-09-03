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


def test_deleting_agent_does_not_touch_existing_log_entries(client, db):
    """Löscht der Admin einen Agenten (Raum), bleiben bestehende Log-Einträge mit dessen
    Raumreferenz unverändert erhalten -- CheckLog.raum ist bewusst kein FK auf agents,
    siehe app/models.py::CheckLog. Nur die Anzeige zeigt danach einen Platzhalter."""
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
    # Der Log-Eintrag existiert weiterhin (Check-in taucht noch auf), nur der Raumname
    # kann nicht mehr aufgelöst werden.
    assert "Check-in" in after.text
    assert "(gelöschter Raum)" in after.text


def test_deleting_visitor_keeps_log_entries_with_placeholder(client, db):
    """DSGVO-Löschung eines Besucherprofils entfernt nur die visitors-Zeile -- die
    Log-Einträge (Check-in/Check-out) bleiben vollständig erhalten, siehe
    app/services/visitors.py::delete_visitor. Die Log-Ansicht zeigt dafür statt des
    Namens einen Platzhalter, das Protokoll selbst bleibt unangetastet."""
    _login(client, db)
    visitor = make_visitor(db, vorname="Erika", nachname="Testfrau")
    checkin_visitor(db, visitor_id=visitor.id)
    checkout_person(db, person_type="visitor", person_id=visitor.id)

    delete_visitor(db, visitor.id)

    response = client.get("/admin/log")
    assert response.status_code == 200
    assert "Erika Testfrau" not in response.text
    assert "(gelöschtes Profil)" in response.text
    assert response.text.count("Check-in") + response.text.count("Check-out") >= 1
