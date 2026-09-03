"""Admin-Einstellungen: automatisches Auschecken nach konfigurierbarer Stundenzahl."""

from __future__ import annotations

from app.services.settings import get_auto_checkout_hours, set_auto_checkout_hours
from tests.factories import make_admin


def _login(client, db):
    make_admin(db, username="admin", password="testpass123")
    client.post("/admin/login", data={"username": "admin", "password": "testpass123"})


def test_default_auto_checkout_hours_uses_config_default(db):
    from app.config import get_settings

    assert get_auto_checkout_hours(db) == get_settings().auto_checkout_default_hours


def test_set_and_get_auto_checkout_hours(db):
    set_auto_checkout_hours(db, 6)
    assert get_auto_checkout_hours(db) == 6

    set_auto_checkout_hours(db, 24)
    assert get_auto_checkout_hours(db) == 24


def test_einstellungen_page_requires_login(client):
    response = client.get("/admin/einstellungen", follow_redirects=False)
    assert response.status_code == 303


def test_einstellungen_form_updates_value(client, db):
    _login(client, db)
    response = client.post(
        "/admin/einstellungen", data={"auto_checkout_stunden": "8"}, follow_redirects=False
    )
    assert response.status_code == 200
    assert get_auto_checkout_hours(db) == 8


def test_einstellungen_rejects_negative_value(client, db):
    _login(client, db)
    response = client.post("/admin/einstellungen", data={"auto_checkout_stunden": "-1"})
    assert response.status_code == 400
