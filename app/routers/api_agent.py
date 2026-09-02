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
    outcome = record_rfid_scan(db, uid=payload.uid, timestamp=payload.timestamp)
    push_event(outcome.result, outcome.name)
    return RfidScanResponse(
        result=outcome.result,
        name=outcome.name,
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
