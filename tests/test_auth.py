"""Auth-Grenzen: Agent-Endpunkte brauchen einen gültigen X-Agent-Key, /admin/* braucht
eine gültige Session."""

from __future__ import annotations

from tests.factories import make_admin, make_agent


def test_rfid_checkin_without_key_is_rejected(client):
    response = client.post("/api/checkin/rfid", json={"agent_id": "kiosk1", "uid": "AABBCCDD"})
    assert response.status_code == 401


def test_rfid_checkin_with_wrong_key_is_rejected(client, db):
    make_agent(db, agent_id="kiosk1")
    response = client.post(
        "/api/checkin/rfid",
        json={"agent_id": "kiosk1", "uid": "AABBCCDD"},
        headers={"X-Agent-Key": "definitiv-falsch"},
    )
    assert response.status_code == 401


def test_rfid_checkin_with_correct_key_is_accepted(client, db):
    _, api_key = make_agent(db, agent_id="kiosk1")
    response = client.post(
        "/api/checkin/rfid",
        json={"agent_id": "kiosk1", "uid": "AABBCCDD"},
        headers={"X-Agent-Key": api_key},
    )
    assert response.status_code == 200
    assert response.json()["result"] == "unknown_card"


def test_heartbeat_requires_agent_key(client):
    response = client.post("/api/agent/heartbeat", json={"agent_id": "kiosk1"})
    assert response.status_code == 401


def test_admin_pages_redirect_to_login_without_session(client):
    response = client.get("/admin/mitarbeiter", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_admin_login_wrong_password_rejected(client, db):
    make_admin(db, username="admin", password="richtig123")
    response = client.post("/admin/login", data={"username": "admin", "password": "falsch"})
    assert response.status_code == 401


def test_admin_login_success_grants_access(client, db):
    make_admin(db, username="admin", password="richtig123")
    login = client.post(
        "/admin/login", data={"username": "admin", "password": "richtig123"}, follow_redirects=False
    )
    assert login.status_code == 303
    assert "rz_admin_session" in login.cookies

    response = client.get("/admin/mitarbeiter")
    assert response.status_code == 200
