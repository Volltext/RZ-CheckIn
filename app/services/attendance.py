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
    action_timestamp: datetime | None = None


@dataclass
class PresentPerson:
    person_type: str
    person_id: str
    # Für Mitarbeiter bewusst IMMER None -- intern wird ausschließlich über die
    # Dienstausweisnummer geführt, es gibt keine gespeicherten Namen, die man hier
    # anzeigen könnte (siehe app/models.py::Employee). Nur Besucher haben einen Namen.
    name: str | None
    firma: str | None
    checkin_zeit: datetime
    # Technikraum des Checkin-Eintrags (= Agent.agent_id, siehe app/models.py::CheckLog).
    # None, wenn keine Raumzuordnung vorliegt.
    raum: str | None = None


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
    SELECT person_type, person_id, timestamp, raum
    FROM (
        SELECT
            person_type,
            person_id,
            action,
            timestamp,
            raum,
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
                    name=None,  # bewusst kein Name -- siehe PresentPerson-Docstring
                    firma=None,
                    checkin_zeit=checkin_zeit,
                    raum=row.raum,
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
                    raum=row.raum,
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
    db: Session, *, uid: str, timestamp: datetime | None = None, raum: str | None = None
) -> RfidScanOutcome:
    """Verarbeitet einen RFID-Scan: togglet Checkin/Checkout für bekannte, aktive
    Mitarbeiter; merkt sich unbekannte UIDs zur späteren Zuordnung im Admin-Bereich.

    `raum` ist die Agent-ID des scannenden Readers (ein Agent pro Technikraum, siehe
    app/models.py::Agent) und wird unverändert auf den Log-Eintrag übernommen."""

    event_time = timestamp or _now()

    employee = db.scalar(select(Employee).where(Employee.rfid_uid == uid))
    if employee is None:
        _record_unknown_scan(db, uid, event_time)
        db.commit()
        return RfidScanOutcome(result="unknown_card")

    if not employee.aktiv:
        return RfidScanOutcome(result="card_inactive")

    last = get_last_log_entry(db, "employee", employee.id)
    if last is not None:
        last_ts = last.timestamp
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        if event_time - last_ts < timedelta(seconds=settings.scan_debounce_seconds):
            return RfidScanOutcome(result="ignored")

    action = "checkout" if (last is not None and last.action == "checkin") else "checkin"
    entry = CheckLog(
        person_type="employee",
        person_id=employee.id,
        action=action,
        source="rfid",
        timestamp=event_time,
        raum=raum,
    )
    db.add(entry)
    db.commit()
    return RfidScanOutcome(result=action, action_timestamp=event_time)


def checkin_visitor(db: Session, *, visitor_id: str, raum: str | None = None) -> CheckLog:
    """`raum` ist die Agent-ID des am Kiosk gewählten Technikraums (siehe
    app/routers/kiosk.py -- Besucher haben keinen eigenen Reader, deshalb wählt der
    Besucher den Raum manuell statt dass er wie bei Mitarbeitern vom Agenten kommt)."""
    if is_present(db, "visitor", visitor_id):
        raise ValueError("Besucher ist bereits eingecheckt")
    entry = CheckLog(person_type="visitor", person_id=visitor_id, action="checkin", source="manual", raum=raum)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def checkout_person(
    db: Session,
    *,
    person_type: str,
    person_id: str,
    operator: str | None = None,
    source: str = "manual",
) -> CheckLog:
    if not is_present(db, person_type, person_id):
        raise ValueError("Person ist nicht eingecheckt")
    # RFID-Toggles laufen über record_rfid_scan(); diese Funktion bedient manuelle
    # Auscheck-Aktionen (Kiosk-Button für Externe, Admin-Eingriff) sowie -- mit
    # source="auto" -- das automatische Auschecken (siehe run_auto_checkout()).
    entry = CheckLog(
        person_type=person_type,
        person_id=person_id,
        action="checkout",
        source=source,
        operator=operator,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def presence_by_room(db: Session) -> dict[str | None, list[PresentPerson]]:
    """Gruppiert die aktuell anwesenden Personen nach Technikraum (PresentPerson.raum,
    siehe dort) für die Split-Ansicht im Kiosk-Dashboard (app/routers/kiosk.py). Der
    Schlüssel `None` fasst Personen ohne Raumzuordnung zusammen (z. B. Alteinträge von
    vor Einführung dieser Funktion)."""
    result: dict[str | None, list[PresentPerson]] = {}
    for person in list_present(db):
        result.setdefault(person.raum, []).append(person)
    return result


def count_present_employees(db: Session) -> int:
    """Anzahl aktuell anwesender Mitarbeiter für die Live-Übersicht am Kiosk -- dort
    werden keine Namen mehr angezeigt (siehe PresentPerson-Docstring), nur die Anzahl
    (grüne Punkte o.ä.)."""
    return sum(1 for p in list_present(db) if p.person_type == "employee")


def run_auto_checkout(db: Session, *, now: datetime | None = None) -> int:
    """Checkt Personen automatisch aus, deren Check-in länger als die im Admin-Bereich
    eingestellte Frist zurückliegt (Feedback: falls jemand vergisst, sich auszuchecken).
    Betrifft Mitarbeiter wie Besucher gleichermaßen. 0/negative Stunden = deaktiviert.

    Wird periodisch aus einem Hintergrund-Task im Server-Prozess aufgerufen (siehe
    app/main.py), nicht aus einem externen Cronjob -- passend zum "super-easy
    Deployment"-Ansatz des Projekts (ein Container, keine weitere Einrichtung nötig)."""
    from app.services.settings import get_auto_checkout_hours  # lokal: Zirkelimport vermeiden

    hours = get_auto_checkout_hours(db)
    if hours <= 0:
        return 0

    reference = now or _now()
    cutoff = reference - timedelta(hours=hours)

    def _aware(ts: datetime) -> datetime:
        return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)

    betroffen = [p for p in list_present(db) if _aware(p.checkin_zeit) < cutoff]
    for person in betroffen:
        checkout_person(
            db,
            person_type=person.person_type,
            person_id=person.person_id,
            operator="System (automatisch)",
            source="auto",
        )
    return len(betroffen)
