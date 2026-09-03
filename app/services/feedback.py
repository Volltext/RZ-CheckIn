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
# Die Selbstregistrierung (unbekannte Karte -> Namen eintragen) braucht spürbar länger
# als ein kurzes "eingecheckt"-Banner: die Person muss den Vor-/Nachnamen erst eintippen.
_TTL_SECONDS_UNKNOWN_CARD = 90

_lock = threading.Lock()
_events: deque["ScanFeedbackEvent"] = deque(maxlen=_MAX_EVENTS)


@dataclass
class ScanFeedbackEvent:
    result: str
    name: str | None
    occurred_at: datetime
    uid: str | None = None


def push_event(result: str, name: str | None, *, uid: str | None = None) -> None:
    with _lock:
        _events.append(
            ScanFeedbackEvent(result=result, name=name, occurred_at=datetime.now(timezone.utc), uid=uid)
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
