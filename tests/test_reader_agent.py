"""Tests für die Reader-Schleife des Agenten (agent/reader_agent.py).

nfcpy und echte Hardware sind hier nicht vorhanden -- stattdessen wird ein Mini-`nfc`-
Modul in sys.modules gelegt, das sich wie ein ACR122U verhält (inkl. der Fehlerbilder,
die den Leser im Feld nach dem ersten Scan lahmgelegt haben).
"""

from __future__ import annotations

import errno
import sys
import threading
import types
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))


class FakeTarget:
    """Minimale Nachbildung von nfc.clf.RemoteTarget."""

    def __init__(self, brty: str, **kwargs):
        self.brty = brty
        self.sens_res = kwargs.get("sens_res")
        self.sel_res = kwargs.get("sel_res")
        self.sdd_res = kwargs.get("sdd_res")
        self.sensb_res = kwargs.get("sensb_res")
        self.sensf_res = kwargs.get("sensf_res")
        self.atr_req = None
        self.sel_req = None


class FakeTransport:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class FakeChipset:
    def __init__(self, transport, close_raises: bool):
        self.transport = transport
        self._close_raises = close_raises
        self.beeps = 0

    def ccid_xfr_block(self, data, timeout=0.1):
        self.beeps += 1
        return bytearray(10)

    def close(self):
        if self._close_raises:
            # Genau das macht nfcpy: erst noch ein Kommando an den Reader schicken (LED
            # aus) -- und genau das scheitert, wenn der Reader gerade klemmt.
            raise OSError(errno.EIO, "Input/output error")
        self.transport.close()
        self.transport = None


class FakeDevice:
    def __init__(self, transport, close_raises: bool):
        self.chipset = FakeChipset(transport, close_raises)

    def close(self):
        self.chipset.close()
        self.chipset = None


class FakeFrontend:
    """Verhält sich wie nfc.ContactlessFrontend, liefert aber eine vorgegebene Folge von
    Sense-Ergebnissen: ein FakeTarget, None (nichts im Feld) oder eine Exception."""

    instances: list["FakeFrontend"] = []
    script: list = []
    # Position im Skript: bewusst klassenweit, damit ein Neuaufbau der Verbindung das
    # Skript fortsetzt statt es von vorne abzuspielen.
    pos = 0
    open_error: Exception | None = None
    close_raises = False

    def __init__(self, path):
        if FakeFrontend.open_error is not None:
            raise FakeFrontend.open_error
        self.path = path
        self.transport = FakeTransport()
        self.device = FakeDevice(self.transport, FakeFrontend.close_raises)
        # Eigene Referenz, weil clf.close() self.device auf None setzt.
        self.chipset = self.device.chipset
        self.closed = 0
        FakeFrontend.instances.append(self)

    def sense(self, *targets, **options):
        step = FakeFrontend.script[FakeFrontend.pos] if FakeFrontend.pos < len(FakeFrontend.script) else None
        FakeFrontend.pos += 1
        if isinstance(step, BaseException):
            raise step
        return step

    def close(self):
        self.closed += 1
        if self.device is not None:
            self.device.close()
            self.device = None


@pytest.fixture(autouse=True)
def fake_nfc(monkeypatch):
    nfc_clf = types.ModuleType("nfc.clf")
    nfc_clf.RemoteTarget = FakeTarget
    nfc = types.ModuleType("nfc")
    nfc.clf = nfc_clf
    nfc.ContactlessFrontend = FakeFrontend
    monkeypatch.setitem(sys.modules, "nfc", nfc)
    monkeypatch.setitem(sys.modules, "nfc.clf", nfc_clf)
    FakeFrontend.instances = []
    FakeFrontend.script = []
    FakeFrontend.pos = 0
    FakeFrontend.open_error = None
    FakeFrontend.close_raises = False
    yield


@pytest.fixture
def agent_config(tmp_path):
    import reader_agent

    return reader_agent.AgentConfig(
        server_url="http://localhost:8000",
        agent_id="kiosk1",
        api_key="k",
        reader="usb:072f:2200",
        scan_cooldown=1.0,
        poll_interval=0.0,
        spool_path=str(tmp_path / "spool.jsonl"),
    )


def run_loop(config, stop_after: int):
    """Lässt die Reader-Schleife laufen, bis `stop_after` UIDs gemeldet wurden oder das
    Skript abgearbeitet ist, und liefert die gemeldeten UIDs."""
    import reader_agent

    stop_event = threading.Event()
    seen: list[str] = []
    total_steps = len(FakeFrontend.script)

    def on_uid(uid: str, timestamp: datetime) -> None:
        seen.append(uid)
        if len(seen) >= stop_after:
            stop_event.set()

    # Sicherheitsnetz: sobald das Skript durch ist, endet die Schleife auf jeden Fall.
    original_sense = FakeFrontend.sense

    def sense(self, *targets, **options):
        if FakeFrontend.pos >= total_steps:
            stop_event.set()
        return original_sense(self, *targets, **options)

    FakeFrontend.sense = sense
    try:
        reader_agent.run_reader_loop(config, reader_agent.Spool(Path(config.spool_path)), stop_event, on_uid=on_uid)
    finally:
        FakeFrontend.sense = original_sense
    return seen


def card(uid_hex: str) -> FakeTarget:
    return FakeTarget("106A", sdd_res=bytes.fromhex(uid_hex), sel_res=b"\x20")


def test_reader_bleibt_ueber_mehrere_karten_hinweg_offen(agent_config):
    """Der eigentliche Fehler: früher wurde der Reader nach JEDEM Scan geschlossen und
    neu geöffnet -- beim ACR122U war er danach tot. Jetzt bleibt eine Verbindung stehen."""
    FakeFrontend.script = [card("044D4F62786F80"), None, card("0411223344556677"), None]

    seen = run_loop(agent_config, stop_after=2)

    assert seen == ["044D4F62786F80", "0411223344556677"]
    assert len(FakeFrontend.instances) == 1, "Reader darf zwischen zwei Karten nicht neu geöffnet werden"
    assert FakeFrontend.instances[0].closed == 1, "am Ende wird sauber geschlossen"


def test_liegende_karte_wird_nur_einmal_gemeldet(agent_config):
    FakeFrontend.script = [card("AABBCCDD")] * 5

    seen = run_loop(agent_config, stop_after=99)

    assert seen == ["AABBCCDD"]


def test_andere_karte_wird_sofort_gemeldet(agent_config):
    """Kein Warten auf den Cooldown, wenn die nächste Person ihren Ausweis auflegt."""
    FakeFrontend.script = [card("AABBCCDD"), card("11223344")]

    seen = run_loop(agent_config, stop_after=2)

    assert seen == ["AABBCCDD", "11223344"]


def test_gleiche_karte_nach_abgelaufenem_cooldown_wieder(agent_config):
    agent_config.scan_cooldown = 0.0  # Feld einmal frei = Sperre sofort aufgehoben
    FakeFrontend.script = [card("AABBCCDD"), None, card("AABBCCDD")]

    seen = run_loop(agent_config, stop_after=2)

    assert seen == ["AABBCCDD", "AABBCCDD"]


def test_einzelne_lesefehler_werfen_die_verbindung_nicht_weg(agent_config):
    """Eine mitten im Lesevorgang weggezogene Karte erzeugt einen EIO -- das ist normal
    und darf nicht dazu führen, dass der Reader neu verbunden wird."""
    FakeFrontend.script = [
        OSError(errno.EIO, "Input/output error"),
        OSError(errno.ETIMEDOUT, "Unknown error"),
        card("AABBCCDD"),
    ]

    seen = run_loop(agent_config, stop_after=1)

    assert seen == ["AABBCCDD"]
    assert len(FakeFrontend.instances) == 1


def test_dauerhafte_lesefehler_fuehren_zum_neuaufbau(agent_config, monkeypatch):
    import reader_agent

    monkeypatch.setattr(reader_agent, "_RECOVER_DELAY", 0.0)
    FakeFrontend.script = [OSError(errno.EIO, "Input/output error")] * (
        reader_agent._SENSE_ERROR_TOLERANCE + 1
    )

    run_loop(agent_config, stop_after=99)

    assert len(FakeFrontend.instances) == 2, "nach zu vielen Fehlern in Folge wird neu verbunden"
    assert FakeFrontend.instances[0].closed == 1


def test_fehlendes_geraet_fuehrt_sofort_zum_neuaufbau(agent_config, monkeypatch):
    """ENODEV heißt: das Gerät ist wirklich weg -- da hilft kein Weiterpollen."""
    import reader_agent

    monkeypatch.setattr(reader_agent, "_RECOVER_DELAY", 0.0)
    FakeFrontend.script = [OSError(errno.ENODEV, "No such device")]

    run_loop(agent_config, stop_after=99)

    assert FakeFrontend.instances[0].closed == 1
    assert len(FakeFrontend.instances) == 2


def test_usb_handle_wird_auch_bei_fehlerhaftem_schliessen_freigegeben(agent_config):
    """nfcpy schluckt den Fehler beim Schließen und lässt den USB-Handle offen -- danach
    scheitert jedes Neuöffnen. _close_reader gibt den Transport in jedem Fall frei."""
    import reader_agent

    FakeFrontend.close_raises = True
    FakeFrontend.script = [card("AABBCCDD")]

    run_loop(agent_config, stop_after=1)

    transport = FakeFrontend.instances[0].transport
    assert transport.closed >= 1, "USB-Handle muss auch nach einem Fehler beim Schließen freigegeben werden"


def test_beep_nutzt_grosszuegiges_zeitfenster(agent_config):
    import reader_agent

    FakeFrontend.script = [card("AABBCCDD")]
    run_loop(agent_config, stop_after=1)

    # Summer an + LED zurück auf grün
    assert FakeFrontend.instances[0].chipset.beeps == 2


def test_beep_kann_abgeschaltet_werden(agent_config):
    agent_config.beep_on_scan = False
    FakeFrontend.script = [card("AABBCCDD")]

    run_loop(agent_config, stop_after=1)

    assert FakeFrontend.instances[0].chipset.beeps == 0


@pytest.mark.parametrize(
    "target, expected",
    [
        (FakeTarget("106A", sdd_res=bytes.fromhex("044D4F62786F80"), sel_res=b"\x20"), "044D4F62786F80"),
        (FakeTarget("106B", sensb_res=bytes.fromhex("50E5DD3DC9")), "E5DD3DC9"),
        (FakeTarget("212F", sensf_res=bytes.fromhex("0101010601B00ADE0B")), "01010601B00ADE0B"),
        # Smartphone im Peer-to-Peer-Modus -- kein Ausweis.
        (FakeTarget("106A", sdd_res=bytes.fromhex("08112233"), sel_res=b"\x60"), None),
        (FakeTarget("212F", sensf_res=bytes.fromhex("0101FE0601B00ADE0B")), None),
        (FakeTarget("106A"), None),
    ],
)
def test_uid_aus_dem_suchergebnis(target, expected):
    """Die UID kommt ohne Kartenaktivierung direkt aus der Suchantwort -- und ist
    byte-identisch mit dem, was nfcpy als Tag.identifier liefern würde."""
    import reader_agent

    assert reader_agent._target_identifier(target) == expected


def test_reader_wird_bei_nicht_erreichbarem_geraet_erneut_versucht(agent_config, monkeypatch):
    import reader_agent

    monkeypatch.setattr(reader_agent, "_RECONNECT_DELAY", 0.0)
    agent_config.reset_after_failures = 2
    resets: list[str] = []
    monkeypatch.setattr(reader_agent, "_try_usb_reset", lambda reader: resets.append(reader) or True)

    FakeFrontend.open_error = OSError(errno.ENODEV, "No such device")
    stop_event = threading.Event()
    attempts = {"n": 0}

    original_init = FakeFrontend.__init__

    def counting_init(self, path):
        attempts["n"] += 1
        if attempts["n"] >= 2:
            stop_event.set()
        original_init(self, path)

    monkeypatch.setattr(FakeFrontend, "__init__", counting_init)
    reader_agent.run_reader_loop(
        agent_config, reader_agent.Spool(Path(agent_config.spool_path)), stop_event
    )

    assert attempts["n"] == 2
    assert resets == [agent_config.reader], "nach reset_after_failures Versuchen wird der USB-Reset ausgelöst"
