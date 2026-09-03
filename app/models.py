"""SQLAlchemy-Modelle. Feldnamen bewusst auf Deutsch, analog zum Konzeptdokument.

Der aktuelle Anwesenheitsstatus wird NICHT hier gespeichert, sondern in
app/services/attendance.py aus `checklog` abgeleitet (letzter Eintrag pro Person).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Employee(Base):
    """Interne Mitarbeiter werden bewusst NUR über ihre Dienstausweisnummer geführt --
    keine Namen, keine sonstige personenbezogene Verknüpfung (Feedback aus der Fachseite:
    das Log soll für interne Mitarbeiter keine Identität speichern, sondern ausschließlich
    die Kartennummer). `rfid_uid` ist damit fachlich die Dienstausweisnummer, technisch
    die vom Reader gelesene Karten-UID -- bei den eingesetzten Dienstausweisen ist beides
    dieselbe Nummer.

    Die eigene `id` bleibt trotzdem bestehen (statt die UID selbst als Primärschlüssel zu
    nutzen), damit ein Kartentausch (verlorene/defekte Karte) möglich ist, ohne die
    Anwesenheitshistorie unter einer neuen Person fortzuführen: der Admin trägt einfach
    eine neue UID auf demselben Eintrag ein."""

    __tablename__ = "employees"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    rfid_uid: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    aktiv: Mapped[bool] = mapped_column(default=True)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Visitor(Base):
    __tablename__ = "visitors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    vorname: Mapped[str] = mapped_column(String(200))
    nachname: Mapped[str] = mapped_column(String(200))
    firma: Mapped[str | None] = mapped_column(String(200), nullable=True)
    telefonnummer: Mapped[str | None] = mapped_column(String(50), nullable=True)
    erstellt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    geloescht_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def voller_name(self) -> str:
        return f"{self.vorname} {self.nachname}".strip()


class CheckLog(Base):
    """Append-only-Protokoll. Kein FK auf employees/visitors (polymorpher Verweis über
    person_type + person_id), stattdessen CHECK-Constraints auf die erlaubten Werte.
    Schreiben ausschließlich über INSERT — siehe app/db.py für den DB-seitigen Schutz.
    """

    __tablename__ = "checklog"
    __table_args__ = (
        CheckConstraint("person_type IN ('employee', 'visitor')", name="ck_checklog_person_type"),
        CheckConstraint("action IN ('checkin', 'checkout')", name="ck_checklog_action"),
        CheckConstraint("source IN ('rfid', 'manual', 'auto')", name="ck_checklog_source"),
        Index("ix_checklog_person", "person_type", "person_id", "timestamp"),
        Index("ix_checklog_timestamp", "timestamp"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    person_type: Mapped[str] = mapped_column(String(20))
    person_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20))
    # Index auf timestamp kommt aus __table_args__ (ix_checklog_timestamp) -- hier kein
    # zusätzliches index=True, sonst legt SQLAlchemy denselben Indexnamen doppelt an.
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    operator: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Technikraum, in dem der Scan/die Aktion stattfand -- als lose Referenz auf
    # Agent.agent_id (kein FK, analog zu person_type/person_id: löscht der Admin den
    # Agenten später, bleibt der Log-Eintrag trotzdem lesbar erhalten, siehe
    # app/services/attendance.py::presence_by_room). Bei Mitarbeitern kommt der Wert vom
    # scannenden Reader-Agenten (ein Agent pro Raum), bei Besuchern aus der
    # Raumauswahl am Kiosk (app/routers/kiosk.py). NULL = keine Raumzuordnung (z. B.
    # Alteinträge vor Einführung dieser Funktion).
    raum: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Bewusst KEIN Namens-Snapshot hier: das Log ist append-only und wird beim Löschen
    # eines Besucherprofils (DSGVO) nicht angefasst ("Log bleibt unangetastet"). Ist die
    # Person zu person_id nicht mehr auffindbar, zeigen Anzeige/Export stattdessen einen
    # generischen Platzhalter ("gelöschtes Profil") — siehe app/services/export.py.


class Agent(Base):
    """Reader-Agenten am Kiosk-PC. `agent_id` ist eine sprechende ID (z.B. 'kiosk1'),
    kein UUID, damit sie in der PRTG-URL und der Agent-Konfiguration lesbar bleibt.

    Da pro Technikraum genau ein Agent existiert, dient der Agent-Eintrag zugleich als
    Raumzuordnung für die Split-Ansicht im Kiosk-Dashboard: `bezeichnung` ist der
    Anzeigename des Raums, `agent_id` der Wert, der auf CheckLog.raum landet (siehe
    app/services/attendance.py::presence_by_room). Es gibt bewusst kein eigenes
    Raum-Modell dafür."""

    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bezeichnung: Mapped[str] = mapped_column(String(200))
    api_key_hash: Mapped[str] = mapped_column(String(200))
    erstellt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    erstellt_am: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UnknownScan(Base):
    """Unbekannte UIDs, die am Reader gescannt wurden, aber keinem Mitarbeiter zugeordnet
    sind. Ermöglicht dem Admin, eine Karte per Klick zuzuordnen, statt die UID manuell
    abzutippen (Konzept 3.1/3.4)."""

    __tablename__ = "unknown_scans"

    uid: Mapped[str] = mapped_column(String(64), primary_key=True)
    zuletzt_gesehen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    anzahl: Mapped[int] = mapped_column(Integer, default=1)


class Setting(Base):
    """Generische Key-Value-Ablage für Einstellungen, die der Admin zur Laufzeit über die
    Oberfläche ändern können soll (im Gegensatz zu app/config.py, das aus Umgebungsvariablen
    beim Start gelesen wird und einen Neustart bräuchte). Aktuell einzig genutzt für
    `auto_checkout_hours`, siehe app/services/settings.py."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500))
