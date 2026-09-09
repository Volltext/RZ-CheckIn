"""Kiosk-Selbstbedienung: manuelles Auschecken für Externe. Für Mitarbeiter gibt es am
Kiosk keine eigene Checkout-Möglichkeit -- Mitarbeiter checken sich ausschließlich per
RFID-Scan (Toggle, kein Register) ein/aus."""

from __future__ import annotations

from app.services.attendance import checkin_visitor, is_present, record_rfid_scan
from tests.factories import make_visitor


def test_manual_checkout_via_kiosk_for_visitor(client, db):
    visitor = make_visitor(db)
    checkin_visitor(db, visitor_id=visitor.id)

    response = client.post(f"/kiosk/auschecken/visitor/{visitor.id}", follow_redirects=False)
    assert response.status_code == 303
    assert is_present(db, "visitor", visitor.id) is False


def test_manual_checkout_visitor_not_present_is_a_no_op(client, db):
    visitor = make_visitor(db)
    response = client.post(f"/kiosk/auschecken/visitor/{visitor.id}", follow_redirects=False)
    assert response.status_code == 303  # kein Fehler, einfach nichts zu tun


def test_kiosk_has_no_manual_checkout_route_for_employees(client, db):
    record_rfid_scan(db, uid="AABBCCDD")
    assert is_present(db, "employee", "AABBCCDD") is True

    # Es gibt bewusst keine Kiosk-Route mehr, um einzelne Mitarbeiter auszuchecken --
    # keine Namen mehr in der Live-Übersicht, also auch keine Zeile zum Anklicken.
    response = client.post("/kiosk/auschecken/employee/AABBCCDD")
    assert response.status_code == 404
    assert is_present(db, "employee", "AABBCCDD") is True


def test_kiosk_has_no_self_registration_route_for_unknown_cards(client, db):
    # Es gibt keine eigene Registrierungsroute mehr -- der RFID-Scan selbst (POST
    # /api/checkin/rfid) togglet bereits jede beliebige UID direkt.
    response = client.post("/kiosk/mitarbeiter/registrieren", data={"rfid_uid": "AABBCC99"})
    assert response.status_code == 404
