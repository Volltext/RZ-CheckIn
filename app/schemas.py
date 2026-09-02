"""Pydantic-Schemas für die API. Response-Felder bewusst deutsch/englisch gemischt wie
im Konzept: Statuswerte (checkin/checkout/...) als stabile API-Konstanten auf Englisch,
Anzeige-Felder (Name, Firma, ...) auf Deutsch."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PersonType = Literal["employee", "visitor"]
CheckAction = Literal["checkin", "checkout"]


class RfidScanRequest(BaseModel):
    agent_id: str
    uid: str = Field(min_length=1, max_length=64)
    # Optionaler Zeitstempel für nachgereichte Scans aus dem Offline-Spool des Agenten.
    # Fehlt er, verwendet der Server die Ankunftszeit des Requests.
    timestamp: datetime | None = None


class RfidScanResponse(BaseModel):
    result: Literal["checkin", "checkout", "unknown_card", "card_inactive", "ignored"]
    name: str | None = None
    action_timestamp: datetime | None = None


class HeartbeatRequest(BaseModel):
    agent_id: str


class HeartbeatResponse(BaseModel):
    agent_id: str
    last_seen: datetime


class PresenceEntry(BaseModel):
    person_type: PersonType
    person_id: str
    name: str
    firma: str | None = None
    checkin_zeit: datetime


class VisitorCreate(BaseModel):
    vorname: str = Field(min_length=1, max_length=200)
    nachname: str = Field(min_length=1, max_length=200)
    firma: str | None = Field(default=None, max_length=200)
    telefonnummer: str | None = Field(default=None, max_length=50)


class VisitorOut(BaseModel):
    id: str
    vorname: str
    nachname: str
    firma: str | None
    telefonnummer: str | None

    model_config = {"from_attributes": True}


class VisitorCheckinRequest(BaseModel):
    visitor_id: str


class CheckoutRequest(BaseModel):
    person_type: PersonType
    person_id: str
    operator: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok", "error"]
    version: str


class AgentHealthResponse(BaseModel):
    agent_id: str
    status: Literal["online", "offline", "unknown"]
    last_seen: datetime | None
    seconds_since_last_heartbeat: float | None
