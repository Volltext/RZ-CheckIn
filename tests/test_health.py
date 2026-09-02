"""/health und /health/agent/{agent_id} -- Grundlage der PRTG-Überwachung."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.config import get_settings
from tests.factories import make_agent


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_agent_health_unknown_agent_returns_404(client):
    response = client.get("/health/agent/does-not-exist")
    assert response.status_code == 404


def test_agent_health_unknown_status_before_first_heartbeat(client, db):
    make_agent(db, agent_id="kiosk1")
    response = client.get("/health/agent/kiosk1")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unknown"
    assert body["last_seen"] is None


def test_agent_health_online_after_recent_heartbeat(client, db):
    agent, _ = make_agent(db, agent_id="kiosk1")
    agent.last_seen = datetime.now(timezone.utc)
    db.commit()

    response = client.get("/health/agent/kiosk1")
    assert response.status_code == 200
    assert response.json()["status"] == "online"


def test_agent_health_offline_after_stale_heartbeat(client, db):
    settings = get_settings()
    agent, _ = make_agent(db, agent_id="kiosk1")
    stale = datetime.now(timezone.utc) - timedelta(seconds=settings.agent_offline_threshold_seconds + 30)
    agent.last_seen = stale
    db.commit()

    response = client.get("/health/agent/kiosk1")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offline"
    # Immer HTTP 200, auch offline -- der PRTG-Sensor liest den Alarm aus dem Feld
    # "status" bzw. "seconds_since_last_heartbeat", nicht aus dem HTTP-Statuscode.
