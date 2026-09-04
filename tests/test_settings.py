"""Admin-Einstellungen: automatisches Auschecken nach konfigurierbarer Stundenzahl,
Besuchersuche am Kiosk an/aus."""

from __future__ import annotations

from app.services.settings import (
    get_auto_checkout_hours,
    get_besucher_suche_aktiv,
    get_retention_days,
    set_auto_checkout_hours,
    set_besucher_suche_aktiv,
    set_retention_days,
)
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


def test_default_besucher_suche_aktiv_is_true(db):
    assert get_besucher_suche_aktiv(db) is True


def test_set_and_get_besucher_suche_aktiv(db):
    set_besucher_suche_aktiv(db, False)
    assert get_besucher_suche_aktiv(db) is False

    set_besucher_suche_aktiv(db, True)
    assert get_besucher_suche_aktiv(db) is True


def test_einstellungen_besucher_suche_form_disables_it(client, db):
    _login(client, db)
    # Checkbox nicht angehakt -> Feld wird gar nicht mitgeschickt.
    response = client.post("/admin/einstellungen/besucher-suche", data={}, follow_redirects=False)
    assert response.status_code == 200
    assert get_besucher_suche_aktiv(db) is False


def test_einstellungen_besucher_suche_form_enables_it(client, db):
    _login(client, db)
    set_besucher_suche_aktiv(db, False)
    response = client.post(
        "/admin/einstellungen/besucher-suche", data={"aktiv": "true"}, follow_redirects=False
    )
    assert response.status_code == 200
    assert get_besucher_suche_aktiv(db) is True


def test_default_retention_days_uses_config_default(db):
    from app.config import get_settings

    assert get_retention_days(db) == get_settings().retention_days


def test_set_and_get_retention_days(db):
    set_retention_days(db, 30)
    assert get_retention_days(db) == 30

    set_retention_days(db, 1000)
    assert get_retention_days(db) == 1000


def test_einstellungen_aufbewahrung_form_updates_value(client, db):
    _login(client, db)
    response = client.post(
        "/admin/einstellungen/aufbewahrung", data={"aufbewahrung_tage": "365"}, follow_redirects=False
    )
    assert response.status_code == 200
    assert get_retention_days(db) == 365


def test_einstellungen_aufbewahrung_rejects_zero_or_negative(client, db):
    _login(client, db)
    response = client.post("/admin/einstellungen/aufbewahrung", data={"aufbewahrung_tage": "0"})
    assert response.status_code == 400

    response = client.post("/admin/einstellungen/aufbewahrung", data={"aufbewahrung_tage": "-1"})
    assert response.status_code == 400
