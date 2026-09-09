"""Admin-Ansicht der aktuell eingecheckten Mitarbeiter-UIDs: kein Register, nur die
Möglichkeit, manuell auszuchecken (Ersatz für die entfernte Kiosk-Möglichkeit)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.attendance import is_present, record_rfid_scan
from tests.factories import make_admin


def _login(client, db):
    make_admin(db, username="admin", password="testpass123")
    client.post("/admin/login", data={"username": "admin", "password": "testpass123"})


def test_mitarbeiter_liste_shows_present_uids(client, db):
    _login(client, db)
    record_rfid_scan(db, uid="AABBCCDD")
    response = client.get("/admin/mitarbeiter")
    assert response.status_code == 200
    assert "AABBCCDD" in response.text


def test_mitarbeiter_liste_hides_checked_out_uids(client, db):
    _login(client, db)
    t0 = datetime.now(timezone.utc)
    record_rfid_scan(db, uid="AABBCCDD", timestamp=t0)
    # Außerhalb des Entprellungsfensters, sonst würde der zweite Scan ignoriert statt
    # auszuchecken (siehe app/services/attendance.py::record_rfid_scan).
    record_rfid_scan(db, uid="AABBCCDD", timestamp=t0 + timedelta(seconds=30))
    response = client.get("/admin/mitarbeiter")
    assert response.status_code == 200
    assert "AABBCCDD" not in response.text


def test_admin_can_checkout_present_employee(client, db):
    _login(client, db)
    record_rfid_scan(db, uid="AABBCCDD")
    assert is_present(db, "employee", "AABBCCDD") is True

    response = client.post("/admin/mitarbeiter/AABBCCDD/auschecken", follow_redirects=False)
    assert response.status_code == 303
    assert is_present(db, "employee", "AABBCCDD") is False


def test_admin_checkout_not_present_is_a_no_op(client, db):
    _login(client, db)
    response = client.post("/admin/mitarbeiter/AABBCCDD/auschecken", follow_redirects=False)
    assert response.status_code == 303
