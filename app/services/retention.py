"""Wartungsjob: löscht checklog-Einträge, die älter als die Aufbewahrungsfrist sind
(Konzept 7: hart löschen), sowie Besucherprofile, zu denen danach kein Log-Eintrag mehr
existiert.

Das ist die einzige erlaubte "Lösch"-Operation auf dem sonst append-only Log. Sie läuft
bewusst getrennt von der normalen Anwendungslogik (eigener CLI-Befehl `purge`, siehe
app/cli.py, per systemd-Timer / Podman-Quadlet-Timer täglich angestoßen) und öffnet das
DELETE-Fenster (`retention_window`) nur für die Dauer einer einzigen Transaktion.

Die Frist selbst ist zur Laufzeit im Admin-Bereich änderbar (siehe
app/services/settings.py::get_retention_days), der Wert aus app/config.py dient nur als
Startwert, solange der Admin noch nichts anderes eingestellt hat.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models import CheckLog, Visitor
from app.services.settings import get_retention_days


@dataclass
class RetentionResult:
    cutoff: datetime
    checklog_entries_removed: int
    visitors_removed: int


def _cutoff(db: Session, now: datetime | None = None) -> datetime:
    reference = now or datetime.now(timezone.utc)
    return reference - timedelta(days=get_retention_days(db))


def find_stale_checklog_ids(db: Session, cutoff: datetime) -> list[str]:
    return list(db.scalars(select(CheckLog.id).where(CheckLog.timestamp < cutoff)))


def find_orphan_visitor_ids(db: Session) -> list[str]:
    """Besucherprofile ohne jeden verbleibenden checklog-Eintrag (nach dem Löschen alter
    Einträge). Aktive Besucher (mit mindestens einem Log-Eintrag) bleiben unberührt."""
    subquery = select(CheckLog.person_id).where(CheckLog.person_type == "visitor").distinct()
    return list(db.scalars(select(Visitor.id).where(Visitor.id.notin_(subquery))))


def purge(db: Session, *, now: datetime | None = None, dry_run: bool = False) -> RetentionResult:
    cutoff = _cutoff(db, now)
    stale_ids = find_stale_checklog_ids(db, cutoff)

    if dry_run:
        # Für die Vorschau: welche Besucherprofile wären verwaist, wenn wir jetzt löschen.
        orphan_ids = list(
            db.scalars(
                select(Visitor.id).where(
                    Visitor.id.notin_(
                        select(CheckLog.person_id)
                        .where(CheckLog.person_type == "visitor", CheckLog.timestamp >= cutoff)
                        .distinct()
                    )
                )
            )
        )
        return RetentionResult(
            cutoff=cutoff,
            checklog_entries_removed=len(stale_ids),
            visitors_removed=len(orphan_ids),
        )

    if not stale_ids:
        return RetentionResult(cutoff=cutoff, checklog_entries_removed=0, visitors_removed=0)

    # Eine Transaktion: Fenster öffnen, löschen, Fenster wieder schließen. Der
    # checklog_no_delete-Trigger lässt DELETE nur zu, solange retention_window nicht leer
    # ist (siehe app/db.py).
    db.execute(
        text("INSERT INTO retention_window (opened_at) VALUES (:opened_at)"),
        {"opened_at": datetime.now(timezone.utc).isoformat()},
    )
    db.execute(delete(CheckLog).where(CheckLog.id.in_(stale_ids)))
    orphan_ids = find_orphan_visitor_ids(db)
    if orphan_ids:
        db.execute(delete(Visitor).where(Visitor.id.in_(orphan_ids)))
    db.execute(text("DELETE FROM retention_window"))
    db.commit()

    return RetentionResult(
        cutoff=cutoff,
        checklog_entries_removed=len(stale_ids),
        visitors_removed=len(orphan_ids),
    )
