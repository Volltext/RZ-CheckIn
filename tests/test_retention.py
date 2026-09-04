"""Aufbewahrungsfrist: Log-Einträge älter als die konfigurierte Frist werden hart
gelöscht, jüngere bleiben erhalten; Besucherprofile ohne verbleibende Log-Einträge
werden mit entfernt (Konzept 7)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.models import CheckLog, Visitor
from app.services.attendance import checkin_visitor, checkout_person, record_rfid_scan
from app.services.retention import purge
from app.services.settings import set_retention_days
from tests.factories import make_employee, make_visitor


def test_purge_removes_only_entries_older_than_retention(db):
    settings = get_settings()
    employee = make_employee(db, rfid_uid="AABBCCDD")

    old = datetime.now(timezone.utc) - timedelta(days=settings.retention_days + 10)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=old)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=recent)

    result = purge(db)
    assert result.checklog_entries_removed == 1

    remaining = list(
        db.scalars(select(CheckLog).where(CheckLog.person_type == "employee", CheckLog.person_id == employee.id))
    )
    assert len(remaining) == 1
    assert remaining[0].timestamp.replace(tzinfo=timezone.utc) > old.replace(tzinfo=timezone.utc) + timedelta(days=1)


def test_purge_dry_run_does_not_delete(db):
    settings = get_settings()
    make_employee(db, rfid_uid="AABBCCDD")
    old = datetime.now(timezone.utc) - timedelta(days=settings.retention_days + 10)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=old)

    result = purge(db, dry_run=True)
    assert result.checklog_entries_removed == 1

    remaining = db.scalar(select(CheckLog.id))
    assert remaining is not None  # nichts wurde tatsächlich gelöscht


def test_purge_removes_orphaned_visitor_profiles(db):
    settings = get_settings()
    visitor = make_visitor(db)
    visitor_id = visitor.id  # vor dem Commit auslesen, siehe Kommentar unten
    old = datetime.now(timezone.utc) - timedelta(days=settings.retention_days + 10)

    # Direkt per INSERT statt über checkin_visitor()/checkout_person(): das append-only
    # Log erlaubt INSERT jederzeit (nur UPDATE/DELETE sind eingeschränkt), und die
    # Service-Funktionen für Besucher kennen keinen Zeitstempel-Parameter für die
    # Vergangenheit -- hier soll gezielt ein altes Log simuliert werden.
    db.add_all(
        [
            CheckLog(person_type="visitor", person_id=visitor_id, action="checkin", source="manual", timestamp=old),
            CheckLog(
                person_type="visitor",
                person_id=visitor_id,
                action="checkout",
                source="manual",
                timestamp=old + timedelta(minutes=30),
            ),
        ]
    )
    db.commit()

    result = purge(db)
    assert result.checklog_entries_removed == 2
    assert result.visitors_removed == 1
    # visitor_id wurde VOR purge() ausgelesen: purge() löscht per Bulk-DELETE am
    # ORM-Identity-Map vorbei, danach ist das Python-Objekt `visitor` veraltet -- jeder
    # Attributzugriff (auch nur `visitor.id`) würde SQLAlchemy zu einem Refresh-Versuch
    # verleiten, der mit ObjectDeletedError statt einem sauberen None fehlschlägt.
    assert db.scalar(select(Visitor).where(Visitor.id == visitor_id)) is None


def test_purge_keeps_visitor_with_recent_entries(db):
    visitor = make_visitor(db)
    checkin_visitor(db, visitor_id=visitor.id)
    checkout_person(db, person_type="visitor", person_id=visitor.id)

    result = purge(db)
    assert result.visitors_removed == 0
    assert db.get(Visitor, visitor.id) is not None


def test_purge_uses_admin_configured_retention_days(db):
    """Die im Admin-Bereich eingestellte Frist (app/services/settings.py) hat Vorrang vor
    dem Startwert aus app/config.py."""
    make_employee(db, rfid_uid="AABBCCDD")
    ten_days_ago = datetime.now(timezone.utc) - timedelta(days=10)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=ten_days_ago)

    result = purge(db)
    assert result.checklog_entries_removed == 0  # Standardfrist (730 Tage) greift noch nicht

    set_retention_days(db, 5)
    result = purge(db)
    assert result.checklog_entries_removed == 1
