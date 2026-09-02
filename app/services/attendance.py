"""Kernlogik: RFID-Toggle, Besucher-Ein-/Auschecken, abgeleiteter Anwesenheitsstatus.

Der aktuelle Status ("wer ist drin") wird nie in einer eigenen Tabelle gepflegt, sondern
immer aus dem letzten checklog-Eintrag pro Person abgeleitet — das Log bleibt so die
einzige Quelle der Wahrheit (siehe Konzept Abschnitt 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CheckLog, Employee, UnknownScan, Visitor

settings = get_settings()

ScanResult = str  # "checkin" | "checkout" | "unknown_card" | "card_inactive" | "ignored"


@dataclass
class RfidScanOutcome:
    result: ScanResult
    name: str | None = None
    action_timestamp: datetime | None = None


@dataclass
class PresentPerson:
    person_type: str
    person_id: str
    name: str
    firma: str | None
    checkin_zeit: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_last_log_entry(db: Session, person_type: str, person_id: str) -> CheckLog | None:
    return db.scalar(
        select(CheckLog)
        .where(CheckLog.person_type == person_type, CheckLog.person_id == person_id)
        .order_by(CheckLog.timestamp.desc(), CheckLog.id.desc())
        .limit(1)
    )


def is_present(db: Session, person_type: str, person_id: str) -> bool:
    last = get_last_log_entry(db, person_type, person_id)
    return last is not None and last.action == "checkin"


# Window-Function-Query: letzter Eintrag pro Person, gefiltert auf "checkin" -> aktuell
# anwesend. Läuft direkt gegen SQLite (>= 3.25, window functions).
_PRESENT_QUERY = text(
    """
    SELECT person_type, person_id, timestamp
    FROM (
        SELECT
            person_type,
            person_id,
            action,
            timestamp,
            ROW_NUMBER() OVER (
                PARTITION BY person_type, person_id
                ORDER BY timestamp DESC, id DESC
            ) AS rn
        FROM checklog
    )
    WHERE rn = 1 AND action = 'checkin'
    ORDER BY timestamp ASC
    """
)


def list_present(db: Session) -> list[PresentPerson]:
    rows = db.execute(_PRESENT_QUERY).all()
    if not rows:
        return []

    employee_ids = [r.person_id for r in rows if r.person_type == "employee"]
    visitor_ids = [r.person_id for r in rows if r.person_type == "visitor"]

    employees = {}
    if employee_ids:
        employees = {e.id: e for e in db.scalars(select(Employee).where(Employee.id.in_(employee_ids)))}
    visitors = {}
    if visitor_ids:
        visitors = {v.id: v for v in db.scalars(select(Visitor).where(Visitor.id.in_(visitor_ids)))}

    result: list[PresentPerson] = []
    for row in rows:
        checkin_zeit = row.timestamp
        if isinstance(checkin_zeit, str):
            checkin_zeit = datetime.fromisoformat(checkin_zeit)
        if row.person_type == "employee":
            employee = employees.get(row.person_id)
            if employee is None:
                continue
            result.append(
                PresentPerson(
                    person_type="employee",
                    person_id=row.person_id,
                    name=employee.voller_name,
                    firma=None,
                    checkin_zeit=checkin_zeit,
                )
            )
        else:
            visitor = visitors.get(row.person_id)
            if visitor is None:
                # Profil wurde gelöscht, während die Person noch eingecheckt war (sollte
                # der Admin-UI-Fluss verhindern) — sicherheitshalber trotzdem anzeigen.
                continue
            result.append(
                PresentPerson(
                    person_type="visitor",
                    person_id=row.person_id,
                    name=visitor.voller_name,
                    firma=visitor.firma,
                    checkin_zeit=checkin_zeit,
                )
            )
    result.sort(key=lambda p: p.checkin_zeit)
    return result


def _record_unknown_scan(db: Session, uid: str, seen_at: datetime) -> None:
    scan = db.get(UnknownScan, uid)
    if scan is None:
        scan = UnknownScan(uid=uid, zuletzt_gesehen=seen_at, anzahl=1)
        db.add(scan)
    else:
        scan.zuletzt_gesehen = seen_at
        scan.anzahl += 1


def record_rfid_scan(
    db: Session, *, uid: str, timestamp: datetime | None = None
) -> RfidScanOutcome:
    """Verarbeitet einen RFID-Scan: togglet Checkin/Checkout für bekannte, aktive
    Mitarbeiter; merkt sich unbekannte UIDs zur späteren Zuordnung im Admin-Bereich."""

    event_time = timestamp or _now()

    employee = db.scalar(select(Employee).where(Employee.rfid_uid == uid))
    if employee is None:
        _record_unknown_scan(db, uid, event_time)
        db.commit()
        return RfidScanOutcome(result="unknown_card")

    if not employee.aktiv:
        return RfidScanOutcome(result="card_inactive", name=employee.voller_name)

    last = get_last_log_entry(db, "employee", employee.id)
    if last is not None:
        last_ts = last.timestamp
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        if event_time - last_ts < timedelta(seconds=settings.scan_debounce_seconds):
            return RfidScanOutcome(result="ignored", name=employee.voller_name)

    action = "checkout" if (last is not None and last.action == "checkin") else "checkin"
    entry = CheckLog(
        person_type="employee",
        person_id=employee.id,
        action=action,
        source="rfid",
        timestamp=event_time,
    )
    db.add(entry)
    db.commit()
    return RfidScanOutcome(result=action, name=employee.voller_name, action_timestamp=event_time)


def checkin_visitor(db: Session, *, visitor_id: str) -> CheckLog:
    if is_present(db, "visitor", visitor_id):
        raise ValueError("Besucher ist bereits eingecheckt")
    entry = CheckLog(person_type="visitor", person_id=visitor_id, action="checkin", source="manual")
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def checkout_person(
    db: Session, *, person_type: str, person_id: str, operator: str | None = None
) -> CheckLog:
    if not is_present(db, person_type, person_id):
        raise ValueError("Person ist nicht eingecheckt")
    # RFID-Toggles laufen über record_rfid_scan(); diese Funktion bedient ausschließlich
    # manuelle Auscheck-Aktionen (Kiosk-Button für Externe, ggf. Admin-Eingriff).
    entry = CheckLog(
        person_type=person_type,
        person_id=person_id,
        action="checkout",
        source="manual",
        operator=operator,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
