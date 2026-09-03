"""Kurzlebiger In-Memory-Puffer für das Scan-Feedback auf dem Kiosk-Bildschirm
("Max Mustermann eingecheckt ✓"). Der Reader-Agent löst den Scan über die API aus, der
Kiosk-Browser pollt separat per htmx — dieser Puffer verbindet beides, ohne dass der
Browser selbst mit dem Reader spricht.

Bewusst im Prozessspeicher (kein DB-Eintrag): es ist reines UI-Feedback mit Sekunden-TTL,
kein Teil des Protokolls. Setzt voraus, dass die Anwendung mit einem einzigen
Worker-Prozess läuft (siehe README/Deployment) — für eine Kiosk-Anwendung mit einem
Client ausreichend.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

_MAX_EVENTS = 5
_TTL_SECONDS = 8
# Die Registrierung einer unbekannten Karte braucht etwas länger als ein kurzes
# "eingecheckt"-Banner, damit Zeit bleibt, den Hinweis zu lesen und den
# Registrieren-Button zu drücken (kein Formular mehr -- siehe kiosk/_feedback.html).
_TTL_SECONDS_UNKNOWN_CARD = 20

_lock = threading.Lock()
_events: deque["ScanFeedbackEvent"] = deque(maxlen=_MAX_EVENTS)


@dataclass
class ScanFeedbackEvent:
    result: str
    name: str | None
    occurred_at: datetime
    uid: str | None = None
    # Agent-ID des scannenden Readers (= Technikraum, siehe app/models.py::Agent).
    # Wird bei der Registrierung einer unbekannten Karte (kiosk/_feedback.html) als
    # Raum für den ersten Checkin-Eintrag mitgeschickt.
    agent_id: str | None = None


def push_event(
    result: str, name: str | None = None, *, uid: str | None = None, agent_id: str | None = None
) -> None:
    with _lock:
        _events.append(
            ScanFeedbackEvent(
                result=result, name=name, occurred_at=datetime.now(timezone.utc), uid=uid, agent_id=agent_id
            )
        )


def latest_event() -> ScanFeedbackEvent | None:
    with _lock:
        if not _events:
            return None
        event = _events[-1]
    ttl = _TTL_SECONDS_UNKNOWN_CARD if event.result == "unknown_card" else _TTL_SECONDS
    age = (datetime.now(timezone.utc) - event.occurred_at).total_seconds()
    if age > ttl:
        return None
    return event
