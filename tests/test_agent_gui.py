"""Tests für die plattformabhängigen Teile des Agenten: Pfadauflösung (agent_paths.py)
und die Wahl der Betriebsart der GUI-Variante (tray_app.py).

Beides ist bewusst ohne Hardware, ohne Desktop-Umgebung und ohne installierte
GUI-Pakete testbar -- genau darum wandern die Prüfungen in eigene Funktionen mit
übergebbaren Ergebnissen.
"""

from __future__ import annotations

import stat
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import agent_paths  # noqa: E402
import tray_app  # noqa: E402


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    return None


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    return None


# --------------------------------------------------------------------------------------
# agent_paths: Wo liegt die agent.ini?
# --------------------------------------------------------------------------------------


def test_explizite_angabe_gewinnt(tmp_path, monkeypatch):
    monkeypatch.setenv(agent_paths.CONFIG_ENV_VAR, str(tmp_path / "aus-umgebung.ini"))
    assert agent_paths.resolve_config_path(tmp_path / "ausdruecklich.ini") == tmp_path / "ausdruecklich.ini"


def test_umgebungsvariable_vor_suchpfaden(tmp_path, monkeypatch):
    vorhanden = tmp_path / "agent.ini"
    vorhanden.write_text("[agent]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(agent_paths.CONFIG_ENV_VAR, "/etc/rz-checkin-agent/aus-umgebung.ini")
    assert agent_paths.resolve_config_path() == Path("/etc/rz-checkin-agent/aus-umgebung.ini")


def test_arbeitsverzeichnis_wird_zuerst_gefunden(tmp_path, monkeypatch):
    """Der bisherige Windows-Aufbau (agent.ini neben dem Programm) muss unverändert
    weiter funktionieren."""
    monkeypatch.delenv(agent_paths.CONFIG_ENV_VAR, raising=False)
    (tmp_path / "agent.ini").write_text("[agent]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert agent_paths.resolve_config_path() == tmp_path / "agent.ini"


def test_ohne_treffer_wird_der_standardpfad_geliefert(tmp_path, monkeypatch, linux):
    monkeypatch.delenv(agent_paths.CONFIG_ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)
    erwartet = tmp_path / "config" / agent_paths.APP_DIR_NAME / "agent.ini"
    assert agent_paths.resolve_config_path() == erwartet
    assert not erwartet.exists()  # angelegt wird erst beim Speichern


def test_suchpfade_enthalten_unter_linux_etc(linux):
    pfade = agent_paths.config_search_paths()
    assert agent_paths.SYSTEM_CONFIG_PATH in pfade
    # Arbeitsverzeichnis vor benutzerbezogener Konfiguration vor /etc
    assert pfade.index(Path.cwd() / "agent.ini") < pfade.index(agent_paths.SYSTEM_CONFIG_PATH)


def test_suchpfade_unter_windows_ohne_etc(windows, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    pfade = agent_paths.config_search_paths()
    assert agent_paths.SYSTEM_CONFIG_PATH not in pfade


def test_standardpfad_windows_liegt_beim_programm(windows, monkeypatch, tmp_path):
    monkeypatch.setattr(agent_paths, "program_dir", lambda: tmp_path)
    assert agent_paths.default_config_path() == tmp_path / "agent.ini"


# --------------------------------------------------------------------------------------
# agent_paths: Wohin schreibt der Agent Log und Offline-Puffer?
# --------------------------------------------------------------------------------------


class FakeConfig:
    def __init__(self, spool_path="agent_spool.jsonl", log_path="reader_agent.log"):
        self.spool_path = spool_path
        self.log_path = log_path


def test_relative_pfade_landen_neben_der_konfiguration(tmp_path):
    config = FakeConfig()
    agent_paths.apply_data_paths(config, tmp_path / "agent.ini")
    assert config.spool_path == str(tmp_path / "agent_spool.jsonl")
    assert config.log_path == str(tmp_path / "reader_agent.log")


def test_absolute_pfade_bleiben_unveraendert(tmp_path):
    config = FakeConfig(spool_path=str(tmp_path / "woanders.jsonl"), log_path=str(tmp_path / "woanders.log"))
    agent_paths.apply_data_paths(config, tmp_path / "agent.ini")
    assert config.spool_path == str(tmp_path / "woanders.jsonl")


def test_nicht_beschreibbarer_konfigordner_weicht_aus(tmp_path, monkeypatch, linux):
    """Der systemd-Dienst liest /etc/rz-checkin-agent/agent.ini -- dort darf er nicht
    schreiben, Log und Puffer müssen deshalb ins Datenverzeichnis ausweichen."""
    etc = tmp_path / "etc"
    etc.mkdir()
    etc.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x: lesen ja, schreiben nein
    monkeypatch.setattr(agent_paths, "_is_writable_dir", lambda path: path != etc)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    config = FakeConfig()
    agent_paths.apply_data_paths(config, etc / "agent.ini")

    daten = tmp_path / "state" / agent_paths.APP_DIR_NAME
    assert config.log_path == str(daten / "reader_agent.log")
    assert daten.is_dir()  # wurde angelegt


# --------------------------------------------------------------------------------------
# tray_app: Welche Oberfläche wird gestartet?
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, erwartet",
    [
        # Kein Desktop (SSH, systemd-Dienst) -> ohne Oberfläche weiterlaufen
        (dict(display=False, tray=True, tk=True), "headless"),
        # Windows-Normalfall bzw. KDE/XFCE: Symbol im Infobereich
        (dict(display=True, tray=True, tk=True), "tray"),
        # GNOME/Wayland ohne AppIndicator: kein Tray, aber tkinter -> Fenster
        (dict(display=True, tray=False, tk=True), "window"),
        # Weder Tray noch tkinter (python3-tk fehlt) -> lieber laufen als abbrechen
        (dict(display=True, tray=False, tk=False), "headless"),
        # --headless schlägt alles
        (dict(force_headless=True, display=True, tray=True, tk=True), "headless"),
        # --window erzwingt das Fenster trotz vorhandenem Tray
        (dict(force_window=True, display=True, tray=True, tk=True), "window"),
        # ... aber nicht, wenn tkinter fehlt
        (dict(force_window=True, display=True, tray=True, tk=False), "headless"),
    ],
)
def test_betriebsart(kwargs, erwartet):
    assert tray_app.choose_ui_mode(**kwargs) == erwartet


class FakePystray:
    """Ersatz für das pystray-Modul: `Icon` kommt je nach Backend aus einem anderen
    Modul, und genau daran erkennt tray_app, ob ein Systray nutzbar ist."""

    def __init__(self, icon_module: str):
        self.Icon = type("Icon", (), {})
        self.Icon.__module__ = icon_module


@pytest.mark.parametrize(
    "icon_module, erwartet",
    [
        ("pystray._xorg", "xorg"),      # Linux mit python-xlib
        ("pystray._appindicator", "appindicator"),
        ("pystray._win32", "win32"),
        # PYSTRAY_BACKEND=dummy reicht nur die abstrakte Basisklasse durch -- damit lässt
        # sich kein Symbol anzeigen, also kein nutzbarer Tray.
        ("pystray._base", None),
        ("pystray._dummy", None),
    ],
)
def test_tray_backend_erkennung(monkeypatch, icon_module, erwartet):
    monkeypatch.setitem(sys.modules, "pystray", FakePystray(icon_module))
    assert tray_app.tray_backend() == erwartet


def test_tray_backend_ohne_pystray(monkeypatch):
    """Ist pystray gar nicht installiert, scheitert schon der Import."""
    monkeypatch.setitem(sys.modules, "pystray", None)  # löst beim import einen Fehler aus
    assert tray_app.tray_backend() is None


def test_tray_backend_bei_kaputtem_gtk(monkeypatch):
    """Realer Linux-Fall: PyGObject ist da, die GTK-Typbibliothek fehlt -- pystray wirft
    dann beim Backend-Import eine ValueError ("Namespace Gtk not available") statt eines
    ImportError. Das darf den Start nicht abbrechen, sondern muss zum Fenster führen."""

    class KaputtesPystray:
        @property
        def Icon(self):
            raise ValueError("Namespace Gtk not available")

    monkeypatch.setitem(sys.modules, "pystray", KaputtesPystray())
    assert tray_app.tray_backend() is None


@pytest.mark.parametrize(
    "backend, tray_manager, erwartet",
    [
        ("win32", False, True),          # Windows: die Prüfung greift gar nicht
        ("appindicator", False, True),   # DBus-Backend, kein XEmbed nötig
        ("xorg", True, True),            # KDE/XFCE: Tray-Manager läuft
        # GNOME: X11-Backend vorhanden, aber niemand nimmt das Symbol entgegen -- pystray
        # würde still ins Leere zeigen, deshalb lieber das Fenster.
        ("xorg", False, False),
        (None, True, False),             # gar kein Backend
    ],
)
def test_tray_nur_wenn_das_symbol_auch_ankommt(monkeypatch, backend, tray_manager, erwartet):
    monkeypatch.setattr(tray_app, "tray_backend", lambda: backend)
    monkeypatch.setattr(tray_app, "_xorg_tray_manager_present", lambda: tray_manager)
    assert tray_app.tray_available() is erwartet


def test_display_erkennung_unter_linux(monkeypatch, linux):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert tray_app.has_display() is False
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert tray_app.has_display() is True


def test_display_unter_windows_immer_vorhanden(windows):
    assert tray_app.has_display() is True


# --------------------------------------------------------------------------------------
# tray_app: Speichern der Konfiguration aus dem Einstellungen-Fenster
# --------------------------------------------------------------------------------------


def test_speichern_legt_ordner_an_und_ergaenzt_standardwerte(tmp_path, linux):
    ziel = tmp_path / "config" / "rz-checkin-agent" / "agent.ini"
    tray_app._save_raw_config(
        ziel,
        {"server_url": "https://rz.example.org", "agent_id": "kiosk1", "api_key": "geheim", "reader": "usb:072f:2200"},
    )
    gelesen = tray_app._load_raw_config(ziel)
    assert gelesen["server_url"] == "https://rz.example.org"
    assert gelesen["heartbeat_interval"] == "30"  # Standardwert ergänzt
    # Der API-Key steht im Klartext in der Datei -- unter Linux nur für den Besitzer lesbar.
    assert stat.S_IMODE(ziel.stat().st_mode) == 0o600


def test_speichern_laesst_unbekannte_werte_stehen(tmp_path):
    ziel = tmp_path / "agent.ini"
    ziel.write_text("[agent]\nreset_command = /usr/local/bin/usbreset 072f:2200\nagent_id = alt\n", encoding="utf-8")
    tray_app._save_raw_config(ziel, {"agent_id": "neu"})
    gelesen = tray_app._load_raw_config(ziel)
    assert gelesen["agent_id"] == "neu"
    assert gelesen["reset_command"] == "/usr/local/bin/usbreset 072f:2200"


# --------------------------------------------------------------------------------------
# reader_detect: Erkennung angeschlossener Kartenleser fürs Auswahlfeld
# --------------------------------------------------------------------------------------

import reader_detect  # noqa: E402

ACR122U = (0x072F, 0x2200)


def test_angeschlossener_leser_steht_oben_und_ist_als_solcher_erkennbar(monkeypatch):
    monkeypatch.setattr(reader_detect, "usb_device_ids", lambda: [(0x1D6B, 0x0002), ACR122U])
    monkeypatch.setattr(reader_detect, "serial_port_options", list)

    optionen = reader_detect.reader_options()
    assert optionen[0].value == "usb:072f:2200"
    assert optionen[0].detected is True
    assert "angeschlossen" in optionen[0].label and "nicht angeschlossen" not in optionen[0].label
    # Nicht doppelt: der erkannte Leser taucht nicht nochmal in der Liste der bekannten,
    # gerade nicht angeschlossenen Geräte auf.
    assert [o.value for o in optionen].count("usb:072f:2200") == 1
    # Ein USB-Hub (1d6b:0002) ist kein Kartenleser und wird nicht angeboten.
    assert not any(o.value == "usb:1d6b:0002" for o in optionen)


def test_ohne_erkannten_leser_gibt_es_trotzdem_eine_auswahl(monkeypatch):
    monkeypatch.setattr(reader_detect, "usb_device_ids", list)
    monkeypatch.setattr(reader_detect, "serial_port_options", list)

    optionen = reader_detect.reader_options()
    werte = [o.value for o in optionen]
    assert reader_detect.AUTO_VALUE in werte           # nfcpys eigene Suche
    assert "usb:072f:2200" in werte                    # Referenzhardware zum Auswählen
    assert optionen[-1].label == reader_detect.CUSTOM_LABEL
    assert not any(o.detected for o in optionen)


def test_eingetragener_wert_bleibt_erhalten(monkeypatch):
    """Ein Wert aus der agent.ini darf nie verloren gehen, auch wenn das Gerät gerade
    nicht angeschlossen ist oder gar nicht zur Erkennungsliste gehört."""
    monkeypatch.setattr(reader_detect, "usb_device_ids", list)
    monkeypatch.setattr(reader_detect, "serial_port_options", list)

    optionen = reader_detect.reader_options("udp:192.168.0.5:54321")
    assert optionen[0].value == "udp:192.168.0.5:54321"
    # Bekannte Geräte werden auch dann beim Namen genannt, wenn sie gerade fehlen.
    optionen = reader_detect.reader_options("usb:072f:2200")
    assert optionen[0].label.startswith("ACS ACR122U")
    assert "nicht angeschlossen" in optionen[0].label


def test_serielle_schnittstellen_werden_in_nfcpy_pfade_uebersetzt(monkeypatch):
    class FakePort:
        def __init__(self, device, description=""):
            self.device = device
            self.description = description

    fake_list_ports = types.SimpleNamespace(
        comports=lambda: [FakePort("/dev/ttyUSB0", "CP2102 USB to UART"), FakePort("COM3")]
    )
    monkeypatch.setitem(sys.modules, "serial", types.ModuleType("serial"))
    monkeypatch.setitem(sys.modules, "serial.tools", types.ModuleType("serial.tools"))
    monkeypatch.setitem(sys.modules, "serial.tools.list_ports", fake_list_ports)

    werte = [o.value for o in reader_detect.serial_port_options()]
    assert werte == ["tty:USB0:pn532", "tty:COM3:pn532"]


def test_usb_erkennung_ohne_usb1_liefert_leere_liste(monkeypatch):
    """Fehlt die USB-Bibliothek, darf das Fenster trotzdem aufgehen."""
    monkeypatch.setitem(sys.modules, "usb1", None)
    assert reader_detect.usb_device_ids() == []


def test_beschriftung_und_wert_lassen_sich_umrechnen(monkeypatch):
    monkeypatch.setattr(reader_detect, "usb_device_ids", lambda: [ACR122U])
    monkeypatch.setattr(reader_detect, "serial_port_options", list)
    optionen = reader_detect.reader_options()

    beschriftung = reader_detect.label_for_value(optionen, "usb:072f:2200")
    assert reader_detect.value_for_label(optionen, beschriftung) == "usb:072f:2200"
    # Selbst eingetippte Werte gehen unverändert durch (inkl. abgeschnittener Leerzeichen)
    assert reader_detect.value_for_label(optionen, "  usb:1234:5678 ") == "usb:1234:5678"


# --------------------------------------------------------------------------------------
# autostart: Häkchen "beim Anmelden automatisch starten"
# --------------------------------------------------------------------------------------

import autostart  # noqa: E402


@pytest.fixture
def linux_home(tmp_path, monkeypatch, linux):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv(agent_paths.CONFIG_ENV_VAR, raising=False)
    return tmp_path


def test_autostart_linux_ein_und_ausschalten(linux_home):
    ziel = autostart.desktop_file()
    assert autostart.is_enabled() is False

    autostart.enable()
    assert autostart.is_enabled() is True
    inhalt = ziel.read_text(encoding="utf-8")
    assert inhalt.startswith("[Desktop Entry]")
    assert "Exec=" in inhalt and "X-GNOME-Autostart-enabled=true" in inhalt

    autostart.disable()
    assert autostart.is_enabled() is False
    assert not ziel.exists()


def test_autostart_linux_nutzt_denselben_dateinamen_wie_install_sh(linux_home):
    """install.sh legt ~/.config/autostart/rz-checkin-agent.desktop an -- beide Wege
    müssen denselben Eintrag meinen, sonst zeigt die Checkbox etwas anderes an, als
    tatsächlich eingerichtet ist."""
    skript = Path(__file__).resolve().parents[1] / "agent" / "linux" / "install.sh"
    assert f"{autostart.desktop_file().name}" in skript.read_text(encoding="utf-8")


def test_autostart_uebernimmt_die_gewaehlte_konfigurationsdatei(linux_home, monkeypatch):
    """Wurde der Agent mit einer bestimmten agent.ini gestartet, muss der Autostart
    dieselbe benutzen -- sonst startet er beim nächsten Anmelden unkonfiguriert."""
    monkeypatch.setenv(agent_paths.CONFIG_ENV_VAR, "/etc/rz-checkin-agent/agent.ini")
    autostart.enable()
    assert "--config /etc/rz-checkin-agent/agent.ini" in autostart.desktop_file().read_text(encoding="utf-8")


def test_autostart_meldet_fehler_statt_stillschweigend_zu_scheitern(linux_home, monkeypatch):
    def kaputt(*args, **kwargs):
        raise OSError("Dateisystem schreibgeschützt")

    monkeypatch.setattr(Path, "mkdir", kaputt)
    with pytest.raises(autostart.AutostartError):
        autostart.enable()


def test_autostart_auf_macos_nicht_unterstuetzt(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert autostart.supported() is False
    assert autostart.is_enabled() is False
    with pytest.raises(autostart.AutostartError):
        autostart.enable()


class FakeWinreg:
    """Nachbildung der Teile von winreg, die autostart.py nutzt -- damit lässt sich der
    Windows-Weg (HKCU-Run-Schlüssel) auch auf einem Linux-Testrechner prüfen."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.werte: dict[str, str] = {}

    class _Key:
        def __init__(self, store):
            self.store = store

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def OpenKey(self, root, pfad, reserved=0, access=0):  # noqa: N802 - winreg-Schreibweise
        return self._Key(self.werte)

    def CreateKeyEx(self, root, pfad, reserved=0, access=0):  # noqa: N802
        return self._Key(self.werte)

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in key.store:
            raise FileNotFoundError(name)
        return key.store[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, typ, wert):  # noqa: N802
        key.store[name] = wert

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in key.store:
            raise FileNotFoundError(name)
        del key.store[name]


def test_autostart_windows_nutzt_den_run_schluessel(monkeypatch, windows):
    fake = FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.delenv(agent_paths.CONFIG_ENV_VAR, raising=False)

    assert autostart.is_enabled() is False
    autostart.enable()
    assert autostart.is_enabled() is True
    assert "tray_app.py" in fake.werte[autostart.WINDOWS_VALUE_NAME]  # Startbefehl hinterlegt

    autostart.disable()
    assert autostart.is_enabled() is False
    autostart.disable()  # zweimal Ausschalten darf nicht scheitern


def test_autostart_ort_wird_genannt(windows, monkeypatch):
    assert "HKCU" in autostart.location()
    monkeypatch.setattr(sys, "platform", "linux")
    assert autostart.location().endswith("rz-checkin-agent.desktop")
