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
import json
import logging
import os
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
    scan_cooldown: float = 1.0  # Mindestabstand zwischen zwei Reads derselben Karte
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
        LOG.info("Scan %s: %s (%s)", uid, data.get("result"), data.get("name"))
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


def _try_usb_reset(reader: str) -> bool:
    """Best-effort USB-Reset des Readers über pyusb, BEVOR nfcpy es erneut versucht.
    Manche Reader (siehe reset_after_failures-Kommentar bei AgentConfig) reagieren auf
    ein simples Neuöffnen nicht mehr, lassen sich aber per USB-Reset-Kommando (dieselbe
    Art Reset, die auch beim Aus-/Einstecken passiert) ohne physischen Eingriff wieder
    aufwecken. Gibt True zurück, wenn ein Reset versucht wurde (nicht: ob er geholfen
    hat -- das zeigt erst der nächste Verbindungsversuch)."""
    vid_pid = _parse_usb_vid_pid(reader)
    if vid_pid is None:
        return False
    try:
        import usb.core  # pyusb -- Abhängigkeit von nfcpy, hier direkt genutzt
    except ImportError:
        LOG.warning("USB-Reset übersprungen: pyusb nicht verfügbar")
        return False
    try:
        device = usb.core.find(idVendor=vid_pid[0], idProduct=vid_pid[1], backend=_pyusb_backend())
        if device is None:
            LOG.warning("USB-Reset übersprungen: Gerät %s aktuell nicht auffindbar", reader)
            return False
        device.reset()
        LOG.info("USB-Reset für %s ausgeführt, warte auf Neuanmeldung des Geräts", reader)
        return True
    except Exception as exc:  # noqa: BLE001 - Reset ist best-effort, darf die Schleife nie beenden
        LOG.warning("USB-Reset für %s fehlgeschlagen: %s", reader, exc)
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


def run_reader_loop(config: AgentConfig, spool: Spool, stop_event: threading.Event) -> None:
    """Endlosschleife über nfcpy. Reader-Aussetzer (Kabel ab, PC im Standby, ...) führen
    zu einem Reconnect-Versuch statt zum Absturz des Agenten. Hilft ein einfaches
    Neuöffnen nach mehreren Versuchen in Folge nicht (siehe AgentConfig.reset_after_
    failures), wird zusätzlich ein USB-Reset (und optional ein konfigurierter
    Hard-Reset-Befehl) versucht, damit der Agent sich ohne manuelles Aus-/Einstecken
    selbst erholen kann."""
    import nfc  # lokaler Import: --simulate-uid soll ohne diese Abhängigkeit laufen

    def on_connect(tag) -> bool:
        uid = tag.identifier.hex().upper()
        handle_scan(config, spool, uid)
        time.sleep(config.scan_cooldown)
        return True  # True = weiter auf die nächste Karte warten

    consecutive_failures = 0

    def _handle_failure(exc: Exception) -> None:
        nonlocal consecutive_failures
        consecutive_failures += 1
        LOG.warning(
            "Reader nicht erreichbar (%s) — neuer Versuch in 5s [%d/%d bis Reset-Versuch]: %s",
            config.reader,
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
                stop_event.wait(5)
                _try_reset_command(config.reset_command)
            # Nach einem Reset braucht das Gerät etwas länger zum Neuanmelden als die
            # normalen 5s zwischen Versuchen.
            stop_event.wait(5)
        else:
            stop_event.wait(5)

    while not stop_event.is_set():
        try:
            with nfc.ContactlessFrontend(config.reader) as clf:
                LOG.info("Reader verbunden: %s", config.reader)
                consecutive_failures = 0
                clf.connect(rdwr={"on-connect": on_connect}, terminate=stop_event.is_set)
        except OSError as exc:
            _handle_failure(exc)
        except Exception as exc:  # noqa: BLE001
            LOG.exception("Unerwarteter Fehler in der Reader-Schleife")
            _handle_failure(exc)


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
        # on_status(status): "online" | "offline" -- z.B. für ein Ampel-Icon im Systray.
        self._on_status = on_status or (lambda status: None)

    def _flush_spool(self) -> None:
        self.spool.flush(lambda uid, ts: send_scan(self.config, uid, ts))

    def _heartbeat_tick(self) -> None:
        self._on_status("online" if send_heartbeat(self.config) else "offline")

    def _reader_loop(self) -> None:
        run_reader_loop(self.config, self.spool, self._stop_event)

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
        reader_thread = threading.Thread(target=self._reader_loop, name="reader", daemon=True)
        heartbeat_thread.start()
        spool_thread.start()
        reader_thread.start()
        self._threads = [heartbeat_thread, spool_thread, reader_thread]
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
