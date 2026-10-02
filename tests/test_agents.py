"""Agenten (Technikräume): Anlegen, Entfernen per Soft-Delete, Auswirkungen auf
Auth/Raumauswahl -- analog zu tests/test_visitors.py für Besucherprofile."""

from __future__ import annotations

from app.models import Agent
from app.services.agents import delete_agent
from tests.factories import make_admin, make_agent


def _login(client, db):
    make_admin(db, username="admin", password="testpass123")
    client.post("/admin/login", data={"username": "admin", "password": "testpass123"})


def test_delete_agent_soft_deletes_and_keeps_row(db):
    agent, _ = make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")

    delete_agent(db, agent.agent_id)

    reloaded = db.get(Agent, "kiosk1")
    assert reloaded is not None
    assert reloaded.geloescht_am is not None
    assert reloaded.bezeichnung == "Serverraum A"


def test_delete_agent_unknown_id_is_a_noop(db):
    delete_agent(db, "nicht-vorhanden")  # darf nicht crashen


def test_agent_anlegen_rejects_reusing_deleted_agent_id(client, db):
    """Verhindert, dass eine entfernte Agent-ID erneut vergeben wird -- sonst würden
    bestehende Log-Einträge plötzlich auf einen anderen (neuen) Raum zeigen."""
    _login(client, db)
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    client.post("/admin/agenten/kiosk1/loeschen", follow_redirects=False)

    response = client.post(
        "/admin/agenten/anlegen", data={"agent_id": "kiosk1", "bezeichnung": "Anderer Raum"}
    )
    assert response.status_code == 409


def test_deleted_agents_api_key_no_longer_authenticates(client, db):
    agent, api_key = make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    delete_agent(db, agent.agent_id)

    response = client.post(
        "/api/checkin/rfid",
        json={"agent_id": "kiosk1", "uid": "AABBCCDD"},
        headers={"X-Agent-Key": api_key},
    )
    assert response.status_code == 401


def test_deleted_agent_not_offered_as_room_choice_at_kiosk(client, db):
    make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    make_agent(db, agent_id="kiosk2", bezeichnung="Serverraum B")
    delete_agent(db, "kiosk1")

    response = client.get("/kiosk/besucher")
    # Nur noch ein aktiver Raum -- kein Raumwahl-Bildschirm mehr nötig, Serverraum A
    # taucht nicht mehr auf.
    assert "In welchem Raum" not in response.text
    assert "Serverraum A" not in response.text


def _scan(client, api_key, ip):
    return client.post(
        "/api/checkin/rfid",
        json={"agent_id": "x", "uid": "AABBCCDD"},
        headers={"X-Agent-Key": api_key, "X-Forwarded-For": ip},
    )


def test_besucher_maske_uebernimmt_raum_per_agent_ip(client, db):
    _, key1 = make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    make_agent(db, agent_id="kiosk2", bezeichnung="Serverraum B")
    # Ohne bekannte IP: Raumauswahl
    assert "In welchem Raum?" in client.get("/kiosk/besucher", headers={"X-Forwarded-For": "10.0.0.5"}).text

    _scan(client, key1, "10.0.0.5")
    text = client.get("/kiosk/besucher", headers={"X-Forwarded-For": "10.0.0.5"}).text
    assert "In welchem Raum?" not in text
    assert 'name="raum" value="kiosk1"' in text


def test_agent_ip_wird_bei_dhcp_wechsel_nachgezogen(client, db):
    _, key1 = make_agent(db, agent_id="kiosk1", bezeichnung="Serverraum A")
    make_agent(db, agent_id="kiosk2", bezeichnung="Serverraum B")
    _scan(client, key1, "10.0.0.5")
    _scan(client, key1, "10.0.0.9")
    assert "In welchem Raum?" in client.get("/kiosk/besucher", headers={"X-Forwarded-For": "10.0.0.5"}).text
    assert "In welchem Raum?" not in client.get("/kiosk/besucher", headers={"X-Forwarded-For": "10.0.0.9"}).text
