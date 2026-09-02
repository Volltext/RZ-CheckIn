"""Kleine Hilfsfunktionen, um Testdaten anzulegen, ohne die HTTP-Schicht zu benutzen."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Agent, AdminUser, Employee, Visitor
from app.security import generate_agent_api_key, hash_agent_api_key, hash_password


def make_employee(
    db: Session, *, vorname: str = "Max", nachname: str = "Mustermann", rfid_uid: str | None = None, aktiv: bool = True
) -> Employee:
    employee = Employee(vorname=vorname, nachname=nachname, rfid_uid=rfid_uid, aktiv=aktiv)
    db.add(employee)
    db.commit()
    db.refresh(employee)
    return employee


def make_visitor(
    db: Session,
    *,
    vorname: str = "Erika",
    nachname: str = "Extern",
    firma: str | None = "ACME GmbH",
    telefonnummer: str | None = "0123456789",
) -> Visitor:
    visitor = Visitor(vorname=vorname, nachname=nachname, firma=firma, telefonnummer=telefonnummer)
    db.add(visitor)
    db.commit()
    db.refresh(visitor)
    return visitor


def make_agent(db: Session, *, agent_id: str = "kiosk1", bezeichnung: str = "Test-Kiosk") -> tuple[Agent, str]:
    api_key = generate_agent_api_key()
    agent = Agent(agent_id=agent_id, bezeichnung=bezeichnung, api_key_hash=hash_agent_api_key(api_key))
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return agent, api_key


def make_admin(db: Session, *, username: str = "admin", password: str = "testpass123") -> AdminUser:
    admin = AdminUser(username=username, password_hash=hash_password(password))
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin
