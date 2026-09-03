"""CSV-Export des Logs für einen Zeitraum (Admin-Bereich, Konzept 3.4)."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, CheckLog, Employee, Visitor

_COLUMNS = [
    "timestamp",
    "person_type",
    "name",
    "firma",
    "raum",
    "action",
    "source",
    "operator",
]


def export_checklog_csv(db: Session, *, von: datetime | None, bis: datetime | None) -> Iterator[str]:
    query = select(CheckLog).order_by(CheckLog.timestamp.asc())
    if von is not None:
        query = query.where(CheckLog.timestamp >= von)
    if bis is not None:
        query = query.where(CheckLog.timestamp <= bis)

    employees = {e.id: e for e in db.scalars(select(Employee))}
    visitors = {v.id: v for v in db.scalars(select(Visitor))}
    # Nur eine Anzeige-Auflösung, kein FK -- siehe app/models.py::CheckLog.raum und
    # app/routers/admin.py::_log_eintraege für dieselbe Logik in der Log-Ansicht.
    agenten = {a.agent_id: a.bezeichnung for a in db.scalars(select(Agent))}

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(_COLUMNS)
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for entry in db.scalars(query):
        if entry.person_type == "employee":
            person = employees.get(entry.person_id)
            # Kein Name -- Mitarbeiter werden ausschließlich über die Dienstausweisnummer
            # geführt (siehe app/models.py::Employee).
            name = (person.rfid_uid or "(ohne Kartennummer)") if person else "(gelöschter Mitarbeiter-Eintrag)"
            firma = ""
        else:
            person = visitors.get(entry.person_id)
            name = person.voller_name if person else "(gelöschtes Profil)"
            firma = person.firma if person else ""

        if entry.raum is None:
            raum = ""
        else:
            raum = agenten.get(entry.raum, "(gelöschter Raum)")

        writer.writerow(
            [
                entry.timestamp.isoformat(),
                entry.person_type,
                name,
                firma or "",
                raum,
                entry.action,
                entry.source,
                entry.operator or "",
            ]
        )
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
