"""Entfernen von Reader-Agenten (Technikräumen) aus dem aktiven Betrieb.

Wie bei Besucherprofilen (siehe app/services/visitors.py) muss das Log lückenlos und mit
Klarnamen lesbar bleiben -- auch für Räume, deren Agent im Admin-Bereich entfernt wurde.
Deshalb ist dies bewusst KEIN Hard-Delete: die `agents`-Zeile bleibt bestehen, nur
`geloescht_am` wird gesetzt (macht zugleich den API-Key ungültig, siehe
app/security.py::require_agent). Kiosk-Raumauswahl und Admin-Agentenliste blenden
Agenten mit gesetztem `geloescht_am` aus; Log-Ansicht und CSV-Export lösen den Raumnamen
weiterhin über die Agent-Zeile auf (siehe app/routers/admin.py::_log_eintraege und
app/services/export.py) und zeigen ihn also unverändert an.

Die Zeile bleibt auch deshalb stehen, damit `agent_id` -- eine vom Admin frei gewählte,
also potenziell wiederverwendbare Kennung -- nach dem Entfernen nicht erneut vergeben
werden kann (siehe app/routers/admin.py::agent_anlegen); sonst würde eine neu angelegte
Agent-ID bestehende Log-Einträge auf einen ganz anderen (neuen) Raum umdeuten."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def delete_agent(db: Session, agent_id: str) -> None:
    agent = db.get(Agent, agent_id)
    if agent is None or agent.geloescht_am is not None:
        return
    agent.geloescht_am = _now()
    db.commit()


def client_ip(request) -> str | None:  # noqa: ANN001
    """IP des Clients. Hinter einem Reverse-Proxy (deploy/nginx-rz-checkin.conf, hängt
    die gesehene Client-IP per `$proxy_add_x_forwarded_for` ANS ENDE von X-Forwarded-For
    an) ist der letzte Eintrag die vom Proxy selbst beobachtete -- frühere Einträge kann
    der Client fälschen. Ohne Proxy gilt die TCP-Peer-Adresse."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff.strip():
        return xff.split(",")[-1].strip() or None
    return request.client.host if request.client else None


def remember_agent_ip(db, agent, request) -> None:  # noqa: ANN001
    """Merkt sich die aktuelle IP des Agent-PCs (Scan/Heartbeat) für die automatische
    Raumzuordnung des Kiosk-Browsers; ändert sich die DHCP-Adresse, wird sie beim
    nächsten Kontakt automatisch nachgezogen."""
    ip = client_ip(request)
    if not ip:
        return
    agent.letzte_ip = ip
    agent.ip_gesehen_am = datetime.now(timezone.utc)
    db.commit()


def agent_for_ip(db, ip: str | None):  # noqa: ANN001
    """Aktiver Agent, der zuletzt von dieser IP gesehen wurde (neuester gewinnt, falls
    eine DHCP-Adresse inzwischen an ein anderes Gerät ging)."""
    if not ip:
        return None
    return db.scalars(
        select(Agent)
        .where(Agent.letzte_ip == ip, Agent.geloescht_am.is_(None))
        .order_by(Agent.ip_gesehen_am.desc())
    ).first()
