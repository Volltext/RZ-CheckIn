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
