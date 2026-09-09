#!/usr/bin/env python3
"""Reader-Agent für den Kiosk-PC: liest Karten-UIDs vom RFID-Leser (nfcpy) und meldet sie
ans RZ-CheckIn-Backend. Läuft NICHT im Server-Container, sondern direkt auf dem
Windows-Kiosk-PC (siehe Konzept Abschnitt 8.2).

Referenzhardware ist der **NFC-Kartenleser USB ACR122U-A9 (RFID)**, siehe
agent/README.md für den genauen Windows-Treiber-/Konfigurationsablauf (`reader =
usb:072f:2200`). nfcpy unterstützt daneben auch PN532-Boards (UART/USB); für die
gibt es ebenfalls Hinweise in agent/README.md.

Dieses Modul enthält ausschließlich die Kernlogik (Reader-Loop, Heartbeat, Offline-
Puffer) und lässt sich sowohl als reines Kommandozeilenprogramm (siehe `main()` unten)
als auch eingebettet aus der Systray-Anwendung (agent/tray_app.py) verwenden -- letztere
bündelt sich per PyInstaller zu einer einzelnen .exe mit Icon im Infobereich und einer
kleinen Einstellungen-GUI, praktisch für den Autostart auf dem Kiosk-PC.

Aufgaben:
  - Karten-UID lesen -> POST /api/checkin/rfid
  - regelmäßiger Heartbeat -> POST /api/agent/heartbeat (Grundlage für die
    PRTG-Überwachung über GET /health/agent/{agent_id})
  - Offline-Puffer: Scans, die wegen einer Verbindungsstörung nicht sofort ankommen,
    werden lokal in einer JSONL-Datei zwischengespeichert und mit exponentiellem
    Backoff nachgesendet, jeweils mit dem ursprünglichen Scan-Zeitstempel.

Nutzung:
  python reader_agent.py --config agent.ini
  python reader_agent.py --config agent.ini --simulate-uid AABBCCDD --once   # ohne Hardware

Siehe agent/agent.ini.example für die Konfiguration und agent/README.md für die
Installation auf dem Windows-Kiosk-PC (COM-Port, nssm-Dienst).
"""

from __future__ import annotations

import argparse
import configparser
import errno
import json
import logging
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests

LOG = logging.getLogger("reader_agent")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass
class AgentConfig:
    server_url: str
    agent_id: str
    api_key: str
    reader: str = "usb"
    heartbeat_interval: float = 30.0
    spool_flush_interval: float = 15.0
    # Solange dieselbe Karte am Leser liegt (bzw. so lange nach dem Wegnehmen), wird sie
    # nicht erneut gemeldet. Eine ANDERE Karte wird immer sofort gemeldet.
    scan_cooldown: float = 1.0
    # Pause zwischen zwei Suchdurchläufen des Readers. Klein halten (der Leser soll sich
    # "sofort" anfühlen), aber nicht 0 -- sonst läuft der USB-Bus dauerhaft am Anschlag.
    poll_interval: float = 0.2
    # Kurzer Piepton/rote LED am ACR122U als Rückmeldung für die Person am Kiosk.
    beep_on_scan: bool = True
    ca_bundle: str | None = None
    verify_tls: bool = True
    spool_path: str = "agent_spool.jsonl"
    log_path: str | None = "reader_agent.log"
    # Manche Reader (v.a. der ACR122U über libusbK) verabschieden sich nach einem
    # USB-Aussetzer dauerhaft und kommen durch bloßes Neuöffnen nicht mehr zurück -- erst
    # ein echter USB-Reset (oder physisches Aus-/Einstecken) hilft. reset_after_failures
    # ist die Anzahl aufeinanderfolgender Fehlversuche, bevor der Agent von sich aus einen
    # USB-Reset probiert (siehe _try_usb_reset); 0 = deaktiviert.
    reset_after_failures: int = 3
    # Reicht der (weiche) USB-Reset nicht aus, kann hier ein Shell-Befehl hinterlegt
    # werden, der stattdessen läuft (z.B. ein devcon/pnputil-Aufruf unter Windows, der das
    # Gerät im Geräte-Manager deaktiviert und wieder aktiviert -- siehe agent/README.md).
    # Läuft NACH dem erfolglosen USB-Reset, nicht statt dessen. None/leer = kein Hard-Reset.
    reset_command: str | None = None

    @classmethod
    def from_file(cls, path: str) -> "AgentConfig":
        parser = configparser.ConfigParser()
        if not parser.read(path, encoding="utf-8"):
            raise FileNotFoundError(f"Konfigurationsdatei nicht gefunden: {path}")
        section = parser["agent"] if parser.has_section("agent") else parser[parser.default_section]

        def get(key: str, default=None, cast: Callable = str):
            env_value = os.environ.get(f"RZ_AGENT_{key.upper()}")
            if env_value is not None:
                return cast(env_value)
            if key in section:
                return cast(section[key])
            return default

        def as_bool(value) -> bool:
            text = str(value).strip().lower()
            if text in _TRUE_VALUES:
                return True
            if text in _FALSE_VALUES:
                return False
            raise ValueError(f"Ungültiger Wahrheitswert: {value!r}")

        server_url = get("server_url")
        agent_id = get("agent_id")
        api_key = get("api_key")
        if not server_url or not agent_id or not api_key:
            raise ValueError("server_url, agent_id und api_key müssen gesetzt sein (agent.ini oder RZ_AGENT_*)")
        if not server_url.startswith(("http://", "https://")):
            raise ValueError(
                f"server_url muss mit http:// oder https:// beginnen (aktuell: {server_url!r}) — "
                "sonst schlägt jede Anfrage mit 'No connection adapters were found' fehl"
            )

        reader = get("reader", "usb")
        reader_scheme = reader.split(":", 1)[0]
        if reader_scheme not in ("usb", "tty", "com", "udp"):
            raise ValueError(
                f"reader hat kein gültiges nfcpy-Format (aktuell: {reader!r}) — erwartet wird z.B. "
                "'usb:072f:2200' für den ACR122U (nur der Pfad, ohne Gerätename davor)"
            )

        return cls(
            server_url=server_url,
            agent_id=agent_id,
            api_key=api_key,
            reader=reader,
            heartbeat_interval=get("heartbeat_interval", 30.0, float),
            spool_flush_interval=get("spool_flush_interval", 15.0, float),
            scan_cooldown=get("scan_cooldown", 1.0, float),
            poll_interval=get("poll_interval", 0.2, float),
            beep_on_scan=get("beep_on_scan", True, as_bool),
            ca_bundle=get("ca_bundle", None),
            verify_tls=get("verify_tls", True, as_bool),
            spool_path=get("spool_path", "agent_spool.jsonl"),
            log_path=get("log_path", "reader_agent.log"),
            reset_after_failures=get("reset_after_failures", 3, int),
            reset_command=get("reset_command", None),
        )

    @property
    def verify(self) -> bool | str:
        return self.ca_bundle if self.ca_bundle else self.verify_tls


class Spool:
    """Persistenter JSONL-Puffer für Scans, die nicht sofort übermittelt werden konnten.

    Jede Zeile: {"uid", "timestamp" (Scan-Zeitpunkt), "attempts", "next_retry"}.
    flush() schreibt die Datei bei jedem Aufruf komplett neu (atomar über eine
    Tempdatei) — bei den hier erwarteten Größenordnungen (einzelne bis wenige hundert
    gepufferte Scans während eines Verbindungsausfalls) unkritisch.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def add(self, uid: str, timestamp: datetime) -> None:
        entry = {
            "uid": uid,
            "timestamp": timestamp.isoformat(),
            "attempts": 0,
            "next_retry": timestamp.isoformat(),
        }
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        entries = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    LOG.warning("Beschädigte Spool-Zeile ignoriert: %r", line)
        return entries

    def _write_all(self, entries: list[dict]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        tmp_path.replace(self.path)

    def __len__(self) -> int:
        return len(self._read_all())

    def flush(self, sender: Callable[[str, datetime], bool]) -> None:
        """Versucht alle fälligen Einträge zu senden. `sender(uid, timestamp) -> bool`
        muss True liefern, wenn der Eintrag als erledigt gelten soll."""
        with self._lock:
            entries = self._read_all()
            if not entries:
                return
            now = datetime.now(timezone.utc)
            remaining = []
            for entry in entries:
                next_retry = datetime.fromisoformat(entry["next_retry"])
                if next_retry > now:
                    remaining.append(entry)
                    continue
                ok = sender(entry["uid"], datetime.fromisoformat(entry["timestamp"]))
                if ok:
                    LOG.info("Nachgereichter Scan gesendet: %s (%s)", entry["uid"], entry["timestamp"])
                    continue
                entry["attempts"] += 1
                backoff_seconds = min(2**entry["attempts"], 300)  # max. 5 Minuten
                entry["next_retry"] = (now + timedelta(seconds=backoff_seconds)).isoformat()
                remaining.append(entry)
            self._write_all(remaining)


def send_scan(config: AgentConfig, uid: str, timestamp: datetime) -> bool:
    url = f"{config.server_url.rstrip('/')}/api/checkin/rfid"
    payload = {"agent_id": config.agent_id, "uid": uid, "timestamp": timestamp.isoformat()}
    headers = {"X-Agent-Key": config.api_key}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5, verify=config.verify)
    except requests.RequestException as exc:
        LOG.warning("Scan konnte nicht gesendet werden (%s): %s", uid, exc)
        return False

    if response.status_code == 200:
        data = response.json()
        ergebnis = data.get("result")
        if ergebnis == "rejected":
            # Der Server hat die Karte anhand der im Admin-Bereich hinterlegten UID-Muster
            # abgelehnt (Kiosk zeigt "Bitte Dienstausweis vorhalten"). Kein Fehler des
            # Agenten -- aber als Warnung hilfreich, wenn jemand fragt, warum eine Karte
            # nicht funktioniert.
            LOG.warning("Scan %s abgelehnt: UID passt auf keines der zugelassenen Muster", uid)
        else:
            LOG.info("Scan %s: %s", uid, ergebnis)
        return True

    LOG.warning("Server antwortete mit %s für Scan %s: %s", response.status_code, uid, response.text[:200])
    # Ein 4xx (z.B. ungültiger Agent-Key) wird durch Wiederholen nicht besser -> als
    # "erledigt" werten, damit der Spool nicht unbegrenzt wächst; der Fehler steht im Log.
    return response.status_code < 500


def send_heartbeat(config: AgentConfig) -> bool:
    url = f"{config.server_url.rstrip('/')}/api/agent/heartbeat"
    headers = {"X-Agent-Key": config.api_key}
    try:
        response = requests.post(
            url, json={"agent_id": config.agent_id}, headers=headers, timeout=5, verify=config.verify
        )
    except requests.RequestException as exc:
        LOG.warning("Heartbeat fehlgeschlagen: %s", exc)
        return False
    if response.status_code != 200:
        LOG.warning("Heartbeat: Server antwortete mit %s", response.status_code)
        return False
    return True


def handle_scan(config: AgentConfig, spool: Spool, uid: str, timestamp: datetime | None = None) -> None:
    ts = timestamp or datetime.now(timezone.utc)
    uid = uid.strip().upper()
    LOG.info("Scan erkannt: %s", uid)
    if not send_scan(config, uid, ts):
        LOG.info("Verbindung gestört — Scan wird im Offline-Puffer zwischengespeichert: %s", uid)
        spool.add(uid, ts)


class BackgroundLoop(threading.Thread):
    """Führt `func` alle `interval` Sekunden aus, bis `stop_event` gesetzt wird."""

    def __init__(self, interval: float, func: Callable[[], None], stop_event: threading.Event, name: str):
        super().__init__(daemon=True, name=name)
        self.interval = interval
        self.func = func
        self.stop_event = stop_event

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.func()
            except Exception:  # noqa: BLE001 - ein Fehler im Hintergrund-Task darf den Agenten nicht beenden
                LOG.exception("Fehler in Hintergrund-Task %s", self.name)
            self.stop_event.wait(self.interval)


# Technologien, nach denen der Reader sucht -- dieselbe Auswahl, die nfcpy im
# Reader/Writer-Modus verwendet: Typ A (MIFARE Classic/Ultralight/DESFire), Typ B und
# FeliCa. Mehr Technologien = längerer Suchdurchlauf, weniger = nicht erkannte Karten.
_SENSE_TARGETS = ("106A", "106B", "212F")

# Sekunden zwischen zwei Verbindungsversuchen, solange der Reader nicht erreichbar ist.
_RECONNECT_DELAY = 5.0

# Sekunden Ruhe, nachdem eine bereits stehende Verbindung gestört wurde -- dem Gerät
# soll kurz Zeit bleiben, bevor es sofort wieder geöffnet wird.
_RECOVER_DELAY = 1.0

# So viele Lesefehler IN FOLGE toleriert der Agent an einem bereits geöffneten Reader,
# bevor er die Verbindung verwirft und den Reader neu öffnet. Einzelne Fehler sind im
# Normalbetrieb unvermeidbar (Karte wird mitten im Lesevorgang weggezogen) und dürfen
# nicht sofort zu einem Neuaufbau führen -- genau das war die Ursache dafür, dass der
# Leser nach dem ersten Scan nicht mehr ansprechbar war.
_SENSE_ERROR_TOLERANCE = 3

# Fehlercodes, bei denen das Gerät wirklich weg ist (Kabel gezogen, Treiber entladen,
# von einem anderen Prozess belegt) -- da hilft kein Weiterpollen, nur ein Neuaufbau.
_DEVICE_GONE_ERRNOS = {errno.ENODEV, errno.ENXIO, errno.EACCES, errno.EBUSY, errno.EPIPE}

# PC/SC-Escape-Kommandos des ACR122U ("Bi-Color LED and Buzzer Control" im ACR122U-API-
# Dokument): 200 ms Summer mit roter LED bzw. zurück in den Ruhezustand (grün, Summer aus).
_ACR122_BEEP_APDU = "FF00400D0402000101"
_ACR122_LED_DEFAULT_APDU = "FF00400E0400000000"


def _parse_usb_vid_pid(reader: str) -> tuple[int, int] | None:
    """Extrahiert Vendor/Product-ID aus einem nfcpy-Pfad wie 'usb:072f:2200'. Liefert
    None für Pfade ohne feste IDs (z.B. nur 'usb', 'tty:...', 'usb:001:027' -- Letzteres
    ist ein Bus:Device-Pfad, keine Vendor:Product-ID, ändert sich bei jedem Neustecken)."""
    parts = reader.split(":")
    if len(parts) != 3 or parts[0] != "usb":
        return None
    try:
        return int(parts[1], 16), int(parts[2], 16)
    except ValueError:
        return None


def _pyusb_backend():
    """Liefert pyusb explizit die passende libusb-Bibliothek aus dem PyPI-Paket `libusb`
    (siehe agent/requirements.txt), statt sich auf pyusbs eigene Systemsuche zu
    verlassen. Genau die Systemsuche ist unter Windows das eigentliche Problem: Zadig
    installiert per libusbK/WinUSB nur den Kernel-Treiber, aber keine `libusb-1.0.dll`
    irgendwo im PATH -- pyusb meldet dann 'No backend available', obwohl das Gerät
    treiberseitig längst nutzbar wäre. Das `libusb`-Paket bringt für jede Plattform
    (inkl. Windows x86/x64/arm64) die passende vorkompilierte Bibliothek gleich mit.
    Gibt None zurück, wenn das Paket fehlt -- pyusb sucht dann wie bisher selbst."""
    try:
        import libusb
        import usb.backend.libusb1

        return usb.backend.libusb1.get_backend(find_library=lambda _: libusb.dll._name)
    except Exception as exc:  # noqa: BLE001 - Fallback auf pyusbs eigene Suche
        LOG.debug("Bundled libusb-Backend nicht verfügbar (%s), nutze pyusb-Systemsuche", exc)
        return None


def _usb_reset_libusb1(vid: int, pid: int) -> bool:
    """USB-Reset über libusb1/usb1 -- also über genau die Bibliothek, die auch nfcpy
    selbst für den USB-Zugriff nutzt (nfc/clf/transport.py: `import usb1 as libusb`).
    Sie ist damit garantiert installiert und spricht das Gerät über denselben Treiber an,
    unter dem der Reader ohnehin schon läuft. pyusb (siehe _usb_reset_pyusb) ist dagegen
    eine komplett andere Bibliothek mit eigener DLL-Suche, die unter Windows regelmäßig
    mit 'No backend available' aussteigt."""
    import usb1

    with usb1.USBContext() as context:
        handle = context.openByVendorIDAndProductID(vid, pid, skip_on_error=True)
        if handle is None:
            return False
        try:
            handle.resetDevice()
        finally:
            handle.close()
        return True


def _usb_reset_pyusb(vid: int, pid: int) -> bool:
    """Rückfallebene über pyusb, falls usb1 den Reset nicht durchbekommt."""
    import usb.core  # pyusb

    device = usb.core.find(idVendor=vid, idProduct=pid, backend=_pyusb_backend())
    if device is None:
        return False
    device.reset()
    return True


def _try_usb_reset(reader: str) -> bool:
    """Best-effort USB-Reset des Readers, BEVOR nfcpy es erneut versucht. Manche Reader
    (siehe reset_after_failures-Kommentar bei AgentConfig) reagieren auf ein simples
    Neuöffnen nicht mehr, lassen sich aber per USB-Reset-Kommando (dieselbe Art Reset,
    die auch beim Aus-/Einstecken passiert) ohne physischen Eingriff wieder aufwecken.
    Der Reset räumt nebenbei auch liegen gebliebene Daten in den USB-Endpunkten weg.
    Gibt True zurück, wenn ein Reset ausgeführt wurde (nicht: ob er geholfen hat -- das
    zeigt erst der nächste Verbindungsversuch).

    Der Reader muss dafür geschlossen sein: solange nfcpy den Handle hält, lässt sich das
    Gerät nicht zurücksetzen."""
    vid_pid = _parse_usb_vid_pid(reader)
    if vid_pid is None:
        LOG.debug("USB-Reset übersprungen: %s enthält keine Vendor:Product-ID", reader)
        return False
    for reset in (_usb_reset_libusb1, _usb_reset_pyusb):
        try:
            if reset(*vid_pid):
                LOG.info("USB-Reset für %s ausgeführt, warte auf Neuanmeldung des Geräts", reader)
                return True
            LOG.debug("USB-Reset über %s: Gerät %s nicht auffindbar", reset.__name__, reader)
        except Exception as exc:  # noqa: BLE001 - Reset ist best-effort, darf die Schleife nie beenden
            LOG.debug("USB-Reset über %s fehlgeschlagen: %s", reset.__name__, exc)
    LOG.warning("USB-Reset für %s nicht möglich (Gerät nicht gefunden oder Reset nicht unterstützt)", reader)
    return False


def _try_reset_command(command: str) -> None:
    """Führt den konfigurierten Hard-Reset-Befehl aus (z.B. devcon/pnputil unter
    Windows, siehe agent/README.md). Läuft nur, wenn _try_usb_reset nicht ausgereicht
    hat -- Fehler landen im Log, dürfen die Reader-Schleife aber nie beenden."""
    import subprocess  # lokaler Import: nur gebraucht, wenn reset_command gesetzt ist

    LOG.info("Führe reset_command aus: %s", command)
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            LOG.warning(
                "reset_command endete mit Exit-Code %s: %s", result.returncode, result.stderr.strip()[:500]
            )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("reset_command konnte nicht ausgeführt werden: %s", exc)


def _target_identifier(target) -> str | None:
    """UID eines gefundenen, aber noch NICHT aktivierten Targets als Hex-String.

    Es sind exakt dieselben Bytes, die nfcpy nach der Kartenaktivierung als
    `nfc.tag.Tag.identifier` liefern würde (vgl. nfc/tag/tt2.py, tt4.py, tt3.py):
    NFCID1 bei Typ A, PUPI bei Typ B, IDm bei FeliCa. Bereits vergebene Karten behalten
    damit ihre UID, die Datenbank muss nicht angefasst werden.

    None bedeutet "kein Ausweis": entweder ein Smartphone im Peer-to-Peer-Modus (das
    filtert nfcpy in seiner Standard-'on-discover'-Funktion genauso weg) oder eine
    Antwort ohne verwertbare Kennung."""
    sel_res = getattr(target, "sel_res", None)
    if sel_res and sel_res[0] & 0x40:
        return None  # NFC-DEP-fähiges Gerät (Smartphone), keine Karte

    sensf_res = getattr(target, "sensf_res", None)
    if sensf_res and bytes(sensf_res[1:3]) == b"\x01\xFE":
        return None  # FeliCa-Kennung, die für Peer-to-Peer reserviert ist

    sdd_res = getattr(target, "sdd_res", None)
    if sdd_res:
        return bytes(sdd_res).hex().upper()  # Typ A: NFCID1
    sensb_res = getattr(target, "sensb_res", None)
    if sensb_res and len(sensb_res) >= 5:
        return bytes(sensb_res[1:5]).hex().upper()  # Typ B: PUPI
    if sensf_res and len(sensf_res) >= 9:
        return bytes(sensf_res[1:9]).hex().upper()  # FeliCa: IDm
    return None


def _beep(clf) -> None:
    """Kurze Rückmeldung am Reader (Summer + rote LED), danach zurück auf grün.

    Bewusst NICHT über nfcpys eingebautes 'beep-on-connect': nfcpy räumt dem ACR122U
    dort nur 400 ms Antwortzeit für einen 300 ms langen Piepton ein
    (nfc/clf/acr122.py::set_buzzer_and_led_to_active). Wird dieses knappe Zeitfenster
    überschritten, wirft nfcpy einen IOError -- die Antwort des Readers kommt aber
    trotzdem und bleibt im USB-Endpunkt liegen. Der nächste Lesevorgang holt sich dann
    diese alte Antwort ab, die Verbindung ist ab da aus dem Tritt, und selbst ein
    Neuöffnen scheitert ('failed to retrieve ACR122U version string'). Genau diese
    Kette hat den Leser nach dem ersten Scan lahmgelegt.

    Hier deshalb dasselbe Kommando mit großzügigem Zeitfenster. Schlägt es trotzdem
    fehl, ist das kein Grund, den bereits gemeldeten Scan oder die Verbindung zu
    verwerfen -- ein stiller Reader ist harmlos, die Erkennung läuft weiter."""
    chipset = getattr(getattr(clf, "device", None), "chipset", None)
    ccid_xfr_block = getattr(chipset, "ccid_xfr_block", None)
    if ccid_xfr_block is None:
        return  # kein ACR122U (z.B. PN532-Board am COM-Port) -- der hat keinen Summer
    try:
        ccid_xfr_block(bytearray.fromhex(_ACR122_BEEP_APDU), timeout=1.0)
        ccid_xfr_block(bytearray.fromhex(_ACR122_LED_DEFAULT_APDU), timeout=1.0)
    except Exception as exc:  # noqa: BLE001 - akustische Rückmeldung ist reines Beiwerk
        LOG.debug("Summer/LED-Kommando am Reader fehlgeschlagen: %s", exc)


def _close_reader(clf) -> None:
    """Schließt den Reader und gibt den USB-Handle in JEDEM Fall frei.

    nfcpy schickt beim Schließen erst noch ein Kommando an den ACR122U (LED zurück auf
    grün, nfc/clf/acr122.py::Chipset.close). Antwortet das Gerät darauf nicht -- also
    genau dann, wenn wir wegen einer Störung schließen --, fliegt dort ein IOError, den
    nfcpy in ContactlessFrontend.close() stillschweigend schluckt. Das anschließende
    `transport.close()` läuft dann nie: der USB-Handle bleibt belegt und jeder weitere
    Öffnungsversuch scheitert. Deshalb merken wir uns den Transport vorher und schließen
    ihn hinterher selbst nach (nfc.clf.transport.USB.close() ist idempotent)."""
    if clf is None:
        return
    device = getattr(clf, "device", None)
    chipset = getattr(device, "chipset", None)
    transport = getattr(chipset, "transport", None) or getattr(device, "transport", None)
    try:
        clf.close()
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Fehler beim Schließen des Readers: %s", exc)
    if transport is not None:
        try:
            transport.close()
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Fehler beim Freigeben des USB-Handles: %s", exc)


def _is_device_gone(exc: BaseException) -> bool:
    """True, wenn der Fehler bedeutet, dass das Gerät selbst weg/blockiert ist -- dann
    ist Weiterpollen zwecklos und die Verbindung muss neu aufgebaut werden."""
    return isinstance(exc, OSError) and exc.errno in _DEVICE_GONE_ERRNOS


def run_reader_loop(
    config: AgentConfig,
    spool: Spool,
    stop_event: threading.Event,
    *,
    on_uid: Callable[[str, datetime], None] | None = None,
) -> None:
    """Endlosschleife über nfcpy: Karten-UIDs lesen und melden.

    Zwei Dinge sind hier für den Dauerbetrieb am Kiosk entscheidend:

    1. **Der Reader wird einmal geöffnet und bleibt offen.** Pro Karte wird nur neu
       gesucht, nicht neu verbunden. Die frühere Variante hatte
       `with nfc.ContactlessFrontend(...) as clf: clf.connect(...)` INNERHALB der
       Schleife -- und da `clf.connect()` nach jeder erkannten Karte zurückkehrt, wurde
       der Reader nach jedem einzelnen Scan geschlossen und wieder geöffnet. Beim
       ACR122U geht das regelmäßig schief: wird mitten in einer laufenden Kartensitzung
       geschlossen, bleiben Antwortdaten im USB-Endpunkt liegen; das nächste Öffnen
       liest sie als vermeintliche Versionsantwort des Readers und scheitert
       ('failed to retrieve ACR122U version string' -> [Errno 19] No such device).
       Ab da war der Leser bis zum physischen Aus-/Einstecken tot.

    2. **Es wird nur bis zur Kartenerkennung gegangen** (`clf.sense`), die Karte selbst
       wird nicht protokollseitig aktiviert (`clf.connect`/`nfc.tag.activate`). Für den
       Check-in wird ausschließlich die UID gebraucht, und die steht bereits in der
       Antwort auf das Suchkommando (siehe _target_identifier). Die Aktivierung ist bei
       DESFire-Karten der fehleranfälligste Teil (daher die Warnung
       'does not support fsd 256' im Log) und entfällt damit komplett -- ebenso die
       anschließende Anwesenheitsprüfung, die die Karte im Sekundentakt weiter
       anspricht.

    Reader-Aussetzer (Kabel ab, PC im Standby, ...) führen weiterhin zu einem
    Reconnect-Versuch statt zum Absturz des Agenten. Hilft ein einfaches Neuöffnen nach
    mehreren Versuchen in Folge nicht (siehe AgentConfig.reset_after_failures), wird
    zusätzlich ein USB-Reset (und optional ein konfigurierter Hard-Reset-Befehl)
    versucht, damit der Agent sich ohne manuelles Aus-/Einstecken selbst erholt.

    `on_uid` bekommt jede erkannte UID samt Erkennungszeitpunkt. Ohne Angabe wird der
    Scan direkt hier verschickt; AgentRuntime reicht stattdessen eine Warteschlange
    herein, damit eine langsame Serververbindung das Einlesen nicht ausbremst."""
    import nfc
    import nfc.clf

    def report_directly(uid: str, timestamp: datetime) -> None:
        handle_scan(config, spool, uid, timestamp)

    report = on_uid or report_directly

    consecutive_failures = 0

    def _handle_failure(exc: Exception) -> None:
        nonlocal consecutive_failures
        consecutive_failures += 1
        LOG.warning(
            "Reader nicht erreichbar (%s) — neuer Versuch in %ss [%d/%d bis Reset-Versuch]: %s",
            config.reader,
            int(_RECONNECT_DELAY),
            consecutive_failures,
            config.reset_after_failures,
            exc,
        )
        if config.reset_after_failures > 0 and consecutive_failures >= config.reset_after_failures:
            consecutive_failures = 0
            _try_usb_reset(config.reader)
            if config.reset_command:
                # Dem Gerät nach dem (weichen) USB-Reset kurz Zeit zum Neuanmelden geben,
                # bevor zusätzlich der deutlich invasivere Hard-Reset-Befehl greift.
                stop_event.wait(_RECONNECT_DELAY)
                _try_reset_command(config.reset_command)
            # Nach einem Reset braucht das Gerät etwas länger zum Neuanmelden als die
            # normalen Sekunden zwischen zwei Versuchen.
            stop_event.wait(_RECONNECT_DELAY)
        else:
            stop_event.wait(_RECONNECT_DELAY)

    # Einmal angelegt und über alle Suchdurchläufe hinweg wiederverwendet -- genau so
    # macht es nfcpy in seiner eigenen Reader/Writer-Schleife auch.
    targets = [nfc.clf.RemoteTarget(brty) for brty in _SENSE_TARGETS]

    while not stop_event.is_set():
        try:
            clf = nfc.ContactlessFrontend(config.reader)
        except OSError as exc:
            _handle_failure(exc)
            continue
        except Exception as exc:  # noqa: BLE001
            LOG.exception("Unerwarteter Fehler beim Öffnen des Readers")
            _handle_failure(exc)
            continue

        LOG.info("Reader verbunden: %s", config.reader)
        consecutive_failures = 0
        # UID der zuletzt gesehenen Karte samt Zeitpunkt -- damit eine liegen bleibende
        # Karte nicht im Sekundentakt erneut gemeldet wird, eine andere Karte aber ohne
        # Verzögerung durchkommt.
        last_uid: str | None = None
        last_seen = 0.0
        sense_errors = 0

        try:
            while not stop_event.is_set():
                try:
                    target = clf.sense(*targets, iterations=1)
                except Exception as exc:  # noqa: BLE001 - Einzelfehler sind normal, s.u.
                    sense_errors += 1
                    if _is_device_gone(exc) or sense_errors > _SENSE_ERROR_TOLERANCE:
                        raise
                    # Typischer Fall: die Karte wird mitten im Lesevorgang weggezogen.
                    # Das ist kein Grund, die Verbindung zum Reader wegzuwerfen.
                    LOG.debug(
                        "Lesefehler %d/%d wird übergangen: %s", sense_errors, _SENSE_ERROR_TOLERANCE, exc
                    )
                    stop_event.wait(config.poll_interval)
                    continue

                sense_errors = 0
                now = time.monotonic()
                uid = _target_identifier(target) if target is not None else None

                if uid is None:
                    # Nichts (Verwertbares) im Feld. Ist das Feld lange genug frei, darf
                    # dieselbe Karte beim erneuten Auflegen wieder gemeldet werden.
                    if last_uid is not None and now - last_seen >= config.scan_cooldown:
                        last_uid = None
                elif uid == last_uid and now - last_seen < config.scan_cooldown:
                    last_seen = now  # Karte liegt noch/wieder auf: Sperre verlängern
                else:
                    last_uid, last_seen = uid, now
                    report(uid, datetime.now(timezone.utc))
                    if config.beep_on_scan:
                        _beep(clf)

                stop_event.wait(config.poll_interval)
        except Exception as exc:  # noqa: BLE001 - der Agent darf hier nie aussteigen
            LOG.warning("Reader-Verbindung gestört (%s) — wird neu aufgebaut: %s", config.reader, exc)
        finally:
            _close_reader(clf)
            # Dem Gerät nach einer gestörten Sitzung kurz Zeit geben, bevor neu geöffnet
            # wird (kehrt sofort zurück, wenn der Agent gerade beendet wird).
            stop_event.wait(_RECOVER_DELAY)


class AgentRuntime:
    """Bündelt Reader-Loop, Heartbeat und Offline-Spool-Flush zu einem startbaren/
    stoppbaren Objekt. `main()` (reines Kommandozeilenprogramm) nutzt das genauso wie
    agent/tray_app.py (Systray + Einstellungen-GUI) -- die eigentliche Agent-Logik lebt
    an genau einer Stelle, die GUI ist nur eine dünne Hülle drumherum."""

    def __init__(self, config: AgentConfig, *, on_status: Callable[[str], None] | None = None):
        self.config = config
        self.spool = Spool(Path(config.spool_path))
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        # Erkannte Scans wandern vom Reader-Thread über diese Warteschlange zum
        # Sende-Thread -- siehe _enqueue_scan.
        self._scan_queue: "queue.Queue[tuple[str, datetime]]" = queue.Queue()
        # on_status(status): "online" | "offline" -- z.B. für ein Ampel-Icon im Systray.
        self._on_status = on_status or (lambda status: None)

    def _flush_spool(self) -> None:
        self.spool.flush(lambda uid, ts: send_scan(self.config, uid, ts))

    def _heartbeat_tick(self) -> None:
        self._on_status("online" if send_heartbeat(self.config) else "offline")

    def _enqueue_scan(self, uid: str, timestamp: datetime) -> None:
        """Wird aus dem Reader-Thread aufgerufen und reiht den Scan nur ein; verschickt
        wird er im Sende-Thread. So hält eine langsame oder gestörte Serververbindung
        (bis zu 5s Timeout pro Versuch) den Reader nicht davon ab, sofort den nächsten
        Ausweis einzulesen."""
        self._scan_queue.put((uid, timestamp))

    def _dispatch_scan(self, uid: str, timestamp: datetime) -> None:
        try:
            handle_scan(self.config, self.spool, uid, timestamp)
        except Exception:  # noqa: BLE001 - ein Fehler hier darf den Sende-Thread nicht beenden
            LOG.exception("Scan %s konnte nicht verarbeitet werden", uid)

    def _sender_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                uid, timestamp = self._scan_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._dispatch_scan(uid, timestamp)
        # Beim Beenden noch wartende Scans nicht verlieren -- handle_scan legt sie
        # notfalls im Offline-Puffer ab, von wo sie beim nächsten Start rausgehen.
        while True:
            try:
                uid, timestamp = self._scan_queue.get_nowait()
            except queue.Empty:
                return
            self._dispatch_scan(uid, timestamp)

    def _reader_loop(self) -> None:
        run_reader_loop(self.config, self.spool, self._stop_event, on_uid=self._enqueue_scan)

    def start(self) -> None:
        if self._threads:
            return  # bereits gestartet
        self._stop_event.clear()
        heartbeat_thread = BackgroundLoop(
            self.config.heartbeat_interval, self._heartbeat_tick, self._stop_event, "heartbeat"
        )
        spool_thread = BackgroundLoop(
            self.config.spool_flush_interval, self._flush_spool, self._stop_event, "spool-flush"
        )
        sender_thread = threading.Thread(target=self._sender_loop, name="scan-sender", daemon=True)
        reader_thread = threading.Thread(target=self._reader_loop, name="reader", daemon=True)
        heartbeat_thread.start()
        spool_thread.start()
        sender_thread.start()
        reader_thread.start()
        self._threads = [heartbeat_thread, spool_thread, sender_thread, reader_thread]
        LOG.info(
            "Agent gestartet: agent_id=%s server=%s reader=%s",
            self.config.agent_id,
            self.config.server_url,
            self.config.reader,
        )

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads = []

    def simulate_scan(self, uid: str) -> None:
        handle_scan(self.config, self.spool, uid)
        self._flush_spool()


def _setup_logging(config: AgentConfig, verbose: bool) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if config.log_path:
        handlers.append(logging.FileHandler(config.log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="agent.ini", help="Pfad zur agent.ini")
    parser.add_argument(
        "--simulate-uid", metavar="UID", help="Statt Hardware: einen Scan mit dieser UID auslösen (Test ohne Reader)"
    )
    parser.add_argument("--once", action="store_true", help="Mit --simulate-uid: nur einen Scan senden und beenden")
    parser.add_argument("--verbose", action="store_true", help="Debug-Logging")
    args = parser.parse_args(argv)

    config = AgentConfig.from_file(args.config)
    _setup_logging(config, args.verbose)

    if args.simulate_uid:
        # Simulation braucht keine Reader-Hardware -- eigener, einfacherer Ablauf statt
        # über AgentRuntime.start() (das würde zusätzlich den echten Reader-Thread starten).
        spool = Spool(Path(config.spool_path))
        handle_scan(config, spool, args.simulate_uid)
        spool.flush(lambda uid, ts: send_scan(config, uid, ts))
        if args.once:
            return 0
        # Ohne --once: Heartbeat/Spool-Flush weiterlaufen lassen, um z.B. das Nachsenden
        # bei einem simulierten Verbindungsabbruch zu beobachten.
        stop_event = threading.Event()
        heartbeat_thread = BackgroundLoop(config.heartbeat_interval, lambda: send_heartbeat(config), stop_event, "heartbeat")
        spool_thread = BackgroundLoop(config.spool_flush_interval, lambda: spool.flush(lambda uid, ts: send_scan(config, uid, ts)), stop_event, "spool-flush")
        heartbeat_thread.start()
        spool_thread.start()
        LOG.info("Simulierter Scan gesendet, Heartbeat/Spool-Flush laufen weiter (Strg+C zum Beenden)")
        try:
            stop_event.wait()
        except KeyboardInterrupt:
            LOG.info("Beende auf Benutzerwunsch (Strg+C)")
        finally:
            stop_event.set()
            heartbeat_thread.join(timeout=2)
            spool_thread.join(timeout=2)
        return 0

    runtime = AgentRuntime(config)
    runtime.start()
    try:
        threading.Event().wait()  # bis Strg+C -- die eigentliche Arbeit läuft in den Hintergrund-Threads
    except KeyboardInterrupt:
        LOG.info("Beende auf Benutzerwunsch (Strg+C)")
    finally:
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
