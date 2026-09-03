"""Admin-Verwaltung von Dienstausweisen: Anlegen direkt mit Kartennummer (kein Name),
manuelles Auschecken durch den Admin (Ersatz für die entfernte Kiosk-Möglichkeit)."""

from __future__ import annotations

from sqlalchemy import select

from app.models import Employee
from app.services.attendance import is_present, record_rfid_scan
from tests.factories import make_admin, make_employee


def _login(client, db):
    make_admin(db, username="admin", password="testpass123")
    client.post("/admin/login", data={"username": "admin", "password": "testpass123"})


def test_anlegen_creates_employee_with_only_uid(client, db):
    _login(client, db)
    response = client.post(
        "/admin/mitarbeiter/anlegen", data={"rfid_uid": "aabbccdd"}, follow_redirects=False
    )
    assert response.status_code == 303

    employee = db.scalar(select(Employee).where(Employee.rfid_uid == "AABBCCDD"))
    assert employee is not None
    assert employee.aktiv is True


def test_anlegen_rejects_duplicate_uid(client, db):
    _login(client, db)
    make_employee(db, rfid_uid="AABBCCDD")
    response = client.post("/admin/mitarbeiter/anlegen", data={"rfid_uid": "AABBCCDD"})
    assert response.status_code == 409


def test_admin_can_checkout_present_employee(client, db):
    _login(client, db)
    employee = make_employee(db, rfid_uid="AABBCCDD")
    record_rfid_scan(db, uid="AABBCCDD")
    assert is_present(db, "employee", employee.id) is True

    response = client.post(f"/admin/mitarbeiter/{employee.id}/auschecken", follow_redirects=False)
    assert response.status_code == 303
    assert is_present(db, "employee", employee.id) is False


def test_admin_checkout_not_present_is_a_no_op(client, db):
    _login(client, db)
    employee = make_employee(db, rfid_uid="AABBCCDD")
    response = client.post(f"/admin/mitarbeiter/{employee.id}/auschecken", follow_redirects=False)
    assert response.status_code == 303


def test_mitarbeiter_liste_shows_no_names(client, db):
    _login(client, db)
    make_employee(db, rfid_uid="AABBCCDD")
    response = client.get("/admin/mitarbeiter")
    assert response.status_code == 200
    assert "AABBCCDD" in response.text
