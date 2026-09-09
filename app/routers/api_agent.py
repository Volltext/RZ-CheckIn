"""Endpunkte, die ausschließlich vom Reader-Agent auf dem Kiosk-PC angesprochen werden.
Auth über den X-Agent-Key-Header (siehe app/security.require_agent)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Agent
from app.schemas import (
    HeartbeatRequest,
    HeartbeatResponse,
    RfidScanRequest,
    RfidScanResponse,
)
from app.security import require_agent
from app.services.attendance import record_rfid_scan
from app.services.feedback import push_event

router = APIRouter(prefix="/api", tags=["agent"])


@router.post("/checkin/rfid", response_model=RfidScanResponse)
def checkin_rfid(
    payload: RfidScanRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(require_agent),
) -> RfidScanResponse:
    # Der Raum wird über den authentifizierten Agenten bestimmt (ein Agent pro
    # Technikraum, siehe app/models.py::Agent), nicht über payload.agent_id -- der Header
    # ist bereits die vertrauenswürdige Quelle für die Agent-Identität (siehe
    # app/security.require_agent).
    outcome = record_rfid_scan(db, uid=payload.uid, timestamp=payload.timestamp, raum=agent.agent_id)
    # Es gibt bewusst keinen Namen im Feedback-Event -- es gibt kein Mitarbeiter-Register,
    # jede Karten-UID togglet direkt (siehe app/services/attendance.py::record_rfid_scan).
    push_event(outcome.result)
    return RfidScanResponse(
        result=outcome.result,
        action_timestamp=outcome.action_timestamp,
    )


@router.post("/agent/heartbeat", response_model=HeartbeatResponse)
def agent_heartbeat(
    payload: HeartbeatRequest,
    db: Session = Depends(get_db),
    agent: Agent = Depends(require_agent),
) -> HeartbeatResponse:
    now = datetime.now(timezone.utc)
    agent.last_seen = now
    db.commit()
    return HeartbeatResponse(agent_id=agent.agent_id, last_seen=now)
