"""UID-Syntax-Whitelist: welche Karten-UIDs überhaupt ein-/auschecken dürfen
(app/services/uid_muster.py, Einstellung unter /admin/einstellungen)."""

from __future__ import annotations

from app.models import CheckLog
from app.services.attendance import record_rfid_scan
from app.services.settings import get_uid_muster, set_uid_muster
from app.services.uid_muster import format_muster, parse_muster, pruefe_muster, uid_passt
from tests.factories import make_admin, make_agent


def _login(client, db):
    make_admin(db, username="admin", password="testpass123")
    client.post("/admin/login", data={"username": "admin", "password": "testpass123"})


# --- Muster-Syntax ------------------------------------------------------------


def test_leere_musterliste_erlaubt_jede_uid():
    assert uid_passt("AABBCCDD", []) is True


def test_x_platzhalter_steht_fuer_genau_ein_zeichen():
    muster = parse_muster("12xxxxxxxxx89")
    assert uid_passt("12ABCDEF12389", muster) is True
    assert uid_passt("1234567890189", muster) is True
    # Eine Stelle zu wenig bzw. zu viel passt nicht.
    assert uid_passt("12ABCDEF1289", muster) is False
    assert uid_passt("12ABCDEF123489", muster) is False
    # Anfang/Ende müssen stimmen.
    assert uid_passt("99ABCDEF12389", muster) is False
    assert uid_passt("12ABCDEF12399", muster) is False


def test_fragezeichen_ist_gleichwertig_zu_x():
    assert uid_passt("12ABCDEF12389", parse_muster("12?????????89")) is True


def test_stern_steht_fuer_beliebig_viele_zeichen():
    muster = parse_muster("04*")
    assert uid_passt("04", muster) is True
    assert uid_passt("04AABBCCDDEE", muster) is True
    assert uid_passt("05AABBCCDDEE", muster) is False


def test_mehrere_muster_wirken_als_oder():
    muster = parse_muster("12xxxxxxxxx89\n04*")
    assert uid_passt("12ABCDEF12389", muster) is True
    assert uid_passt("04AABB", muster) is True
    assert uid_passt("99AABB", muster) is False


def test_vergleich_ignoriert_gross_kleinschreibung():
    assert uid_passt("aabbccdd", parse_muster("AABB*")) is True


def test_muster_ohne_platzhalter_ist_exakter_vergleich():
    muster = parse_muster("AABBCCDD")
    assert uid_passt("AABBCCDD", muster) is True
    assert uid_passt("AABBCCDDEE", muster) is False


def test_parse_muster_trennt_zeilen_kommas_und_semikolons():
    assert parse_muster("12*\n04*, 05*; 06*") == ["12*", "04*", "05*", "06*"]


def test_parse_muster_entfernt_leereintraege_und_dubletten():
    assert parse_muster("  12*  \n\n12*\n") == ["12*"]


def test_format_muster_schreibt_ein_muster_pro_zeile():
    assert format_muster(["12*", "04*"]) == "12*\n04*"


def test_pruefe_muster_meldet_zu_viele_und_zu_lange_muster():
    assert pruefe_muster(["12*", "04*"]) is None
    assert pruefe_muster([f"{i:02d}*" for i in range(21)]) is not None
    assert pruefe_muster(["A" * 65]) is not None
    assert pruefe_muster(["12 34*"]) is not None


# --- Einstellung speichern/lesen ----------------------------------------------


def test_standard_ohne_admin_wert_ist_keine_einschraenkung(db):
    assert get_uid_muster(db) == []


def test_set_und_get_uid_muster(db):
    set_uid_muster(db, parse_muster("12xxxxxxxxx89"))
    assert get_uid_muster(db) == ["12XXXXXXXXX89"]

    # Leere Liste hebt die Einschränkung wieder auf (und fällt nicht auf den
    # Startwert aus der Umgebung zurück).
    set_uid_muster(db, [])
    assert get_uid_muster(db) == []


# --- Wirkung auf den Scan ------------------------------------------------------


def test_scan_einer_passenden_uid_checkt_ein(db):
    set_uid_muster(db, parse_muster("12xxxxxxxxx89"))
    assert record_rfid_scan(db, uid="12ABCDEF12389").result == "checkin"


def test_scan_einer_fremden_uid_wird_abgelehnt(db):
    set_uid_muster(db, parse_muster("12xxxxxxxxx89"))
    outcome = record_rfid_scan(db, uid="AABBCCDD")
    assert outcome.result == "rejected"
    assert outcome.action_timestamp is None


def test_abgelehnter_scan_wird_nicht_protokolliert(db):
    set_uid_muster(db, parse_muster("12xxxxxxxxx89"))
    record_rfid_scan(db, uid="AABBCCDD")
    assert db.query(CheckLog).count() == 0


def test_abgelehnte_uid_wird_nicht_entprellt(db):
    """Der Hinweis am Kiosk soll bei jedem erneuten Vorhalten wieder erscheinen."""
    set_uid_muster(db, parse_muster("12xxxxxxxxx89"))
    assert record_rfid_scan(db, uid="AABBCCDD").result == "rejected"
    assert record_rfid_scan(db, uid="AABBCCDD").result == "rejected"


def test_bereits_eingecheckte_karte_kommt_auch_nach_verschaerftem_muster_wieder_raus(db):
    """Wird das Muster geändert, während jemand eingecheckt ist, bleibt die Person sonst
    dauerhaft "drin" -- deshalb hier bewusst dokumentiert: das automatische Auschecken
    (app/services/attendance.py::run_auto_checkout) räumt solche Fälle auf."""
    assert record_rfid_scan(db, uid="AABBCCDD").result == "checkin"
    set_uid_muster(db, parse_muster("12*"))
    assert record_rfid_scan(db, uid="AABBCCDD").result == "rejected"


# --- API + Kiosk-Feedback -------------------------------------------------------


def test_api_antwortet_mit_rejected_und_zeigt_hinweis_am_kiosk(client, db):
    _, api_key = make_agent(db)
    set_uid_muster(db, parse_muster("12xxxxxxxxx89"))

    response = client.post(
        "/api/checkin/rfid",
        json={"agent_id": "kiosk1", "uid": "AABBCCDD"},
        headers={"X-Agent-Key": api_key},
    )
    assert response.status_code == 200
    assert response.json()["result"] == "rejected"

    feedback = client.get("/kiosk/feedback-partial")
    assert "Bitte Dienstausweis vorhalten" in feedback.text


# --- Admin-Formular ---------------------------------------------------------------


def test_einstellungen_formular_speichert_muster(client, db):
    _login(client, db)
    response = client.post(
        "/admin/einstellungen/uid-muster", data={"uid_muster": "12xxxxxxxxx89\n04*"}, follow_redirects=False
    )
    assert response.status_code == 200
    assert get_uid_muster(db) == ["12XXXXXXXXX89", "04*"]


def test_einstellungen_formular_leert_muster(client, db):
    _login(client, db)
    set_uid_muster(db, parse_muster("12*"))
    response = client.post("/admin/einstellungen/uid-muster", data={"uid_muster": ""})
    assert response.status_code == 200
    assert get_uid_muster(db) == []


def test_einstellungen_formular_lehnt_ungueltiges_muster_ab(client, db):
    _login(client, db)
    response = client.post("/admin/einstellungen/uid-muster", data={"uid_muster": "A" * 65})
    assert response.status_code == 400
    assert get_uid_muster(db) == []


def test_test_uid_zeigt_ob_die_karte_zugelassen_waere(client, db):
    _login(client, db)
    response = client.post(
        "/admin/einstellungen/uid-muster",
        data={"uid_muster": "12xxxxxxxxx89", "test_uid": "12ABCDEF12389"},
    )
    assert "ist zugelassen" in response.text

    response = client.post(
        "/admin/einstellungen/uid-muster",
        data={"uid_muster": "12xxxxxxxxx89", "test_uid": "AABBCCDD"},
    )
    assert "würde abgelehnt" in response.text


def test_einstellungen_seite_zeigt_gespeicherte_muster(client, db):
    _login(client, db)
    set_uid_muster(db, parse_muster("12xxxxxxxxx89"))
    response = client.get("/admin/einstellungen")
    assert "12XXXXXXXXX89" in response.text
