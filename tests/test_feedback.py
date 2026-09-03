"""Scan-Feedback-Puffer: UID-Durchreichung für unbekannte Karten, unterschiedliche TTL
für die Selbstregistrierung (braucht länger als ein kurzes Checkin-Banner)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from app.services import feedback


def test_latest_event_returns_none_when_empty():
    feedback._events.clear()
    assert feedback.latest_event() is None


def test_push_event_carries_uid_for_unknown_card():
    feedback._events.clear()
    feedback.push_event("unknown_card", None, uid="AABBCCDD")
    event = feedback.latest_event()
    assert event is not None
    assert event.uid == "AABBCCDD"


def test_checkin_event_expires_after_short_ttl():
    feedback._events.clear()
    feedback.push_event("checkin", "Max Mustermann")
    event = feedback._events[-1]

    with patch("app.services.feedback.datetime") as mock_dt:
        mock_dt.now.return_value = event.occurred_at + timedelta(seconds=feedback._TTL_SECONDS + 1)
        assert feedback.latest_event() is None


def test_unknown_card_event_survives_longer_for_registration_form():
    feedback._events.clear()
    feedback.push_event("unknown_card", None, uid="AABBCCDD")
    event = feedback._events[-1]

    with patch("app.services.feedback.datetime") as mock_dt:
        # Länger als die normale TTL, aber innerhalb des Registrierungs-Fensters.
        mock_dt.now.return_value = event.occurred_at + timedelta(seconds=feedback._TTL_SECONDS + 5)
        assert feedback.latest_event() is not None

        mock_dt.now.return_value = event.occurred_at + timedelta(seconds=feedback._TTL_SECONDS_UNKNOWN_CARD + 1)
        assert feedback.latest_event() is None
