"""Öffentliche/allgemeine API-Endpunkte: Live-Status, Besucherverwaltung am Kiosk,
Health-Checks für PRTG. Kein Login nötig (steht am Kiosk-PC vor Ort, siehe Konzept 3.3)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.config import get_settings
from app.db import get_db
from app.models import Agent, Visitor
from app.schemas import (
    AgentHealthResponse,
    CheckoutRequest,
    HealthResponse,
    PresenceEntry,
    VisitorCheckinRequest,
    VisitorCreate,
    VisitorOut,
)
from app.services.attendance import checkin_visitor, checkout_person, list_present

router = APIRouter(prefix="/api", tags=["public"])
health_router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/status", response_model=list[PresenceEntry])
def get_status(db: Session = Depends(get_db)) -> list[PresenceEntry]:
    present = list_present(db)
    return [
        PresenceEntry(
            person_type=p.person_type,
            person_id=p.person_id,
            name=p.name,
            firma=p.firma,
            checkin_zeit=p.checkin_zeit,
        )
        for p in present
    ]


@router.get("/visitors/search", response_model=list[VisitorOut])
def search_visitors(q: str = Query(min_length=1), db: Session = Depends(get_db)) -> list[VisitorOut]:
    like = f"%{q.strip()}%"
    stmt = (
        select(Visitor)
        .where(Visitor.geloescht_am.is_(None))
        .where(
            (Visitor.vorname.ilike(like))
            | (Visitor.nachname.ilike(like))
            | (Visitor.telefonnummer.ilike(like))
            | ((Visitor.vorname + " " + Visitor.nachname).ilike(like))
        )
        .order_by(Visitor.nachname, Visitor.vorname)
        .limit(20)
    )
    return list(db.scalars(stmt))


@router.post("/visitors", response_model=VisitorOut, status_code=status.HTTP_201_CREATED)
def create_visitor(payload: VisitorCreate, db: Session = Depends(get_db)) -> VisitorOut:
    visitor = Visitor(
        vorname=payload.vorname.strip(),
        nachname=payload.nachname.strip(),
        firma=(payload.firma or "").strip() or None,
        telefonnummer=(payload.telefonnummer or "").strip() or None,
    )
    db.add(visitor)
    db.commit()
    db.refresh(visitor)
    return visitor


@router.post("/checkin/visitor", response_model=PresenceEntry)
def checkin_visitor_endpoint(
    payload: VisitorCheckinRequest, db: Session = Depends(get_db)
) -> PresenceEntry:
    visitor = db.get(Visitor, payload.visitor_id)
    if visitor is None or visitor.geloescht_am is not None:
        raise HTTPException(status_code=404, detail="Besucherprofil nicht gefunden")
    try:
        entry = checkin_visitor(db, visitor_id=visitor.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PresenceEntry(
        person_type="visitor",
        person_id=visitor.id,
        name=visitor.voller_name,
        firma=visitor.firma,
        checkin_zeit=entry.timestamp,
    )


@router.post("/checkout", status_code=status.HTTP_204_NO_CONTENT)
def checkout_endpoint(payload: CheckoutRequest, db: Session = Depends(get_db)) -> None:
    try:
        checkout_person(
            db,
            person_type=payload.person_type,
            person_id=payload.person_id,
            operator=payload.operator,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@health_router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    try:
        db.execute(select(1))
        database_status = "ok"
    except Exception:  # pragma: no cover - defensiver Health-Check
        database_status = "error"
    return HealthResponse(status="ok", database=database_status, version=__version__)


@health_router.get("/health/agent/{agent_id}", response_model=AgentHealthResponse)
def agent_health(agent_id: str, db: Session = Depends(get_db)) -> AgentHealthResponse:
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Unbekannter Agent")

    if agent.last_seen is None:
        return AgentHealthResponse(
            agent_id=agent.agent_id, status="unknown", last_seen=None, seconds_since_last_heartbeat=None
        )

    last_seen = agent.last_seen
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    seconds_since = (datetime.now(timezone.utc) - last_seen).total_seconds()
    agent_status = "online" if seconds_since <= settings.agent_offline_threshold_seconds else "offline"
    return AgentHealthResponse(
        agent_id=agent.agent_id,
        status=agent_status,
        last_seen=agent.last_seen,
        seconds_since_last_heartbeat=seconds_since,
    )
