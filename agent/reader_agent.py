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

        return cls(
            server_url=server_url,
            agent_id=agent_id,
            api_key=api_key,
            reader=get("reader", "usb"),
            heartbeat_interval=get("heartbeat_interval", 30.0, float),
            spool_flush_interval=get("spool_flush_interval", 15.0, float),
            scan_cooldown=get("scan_cooldown", 1.0, float),
            ca_bundle=get("ca_bundle", None),
            verify_tls=get("verify_tls", True, as_bool),
            spool_path=get("spool_path", "agent_spool.jsonl"),
            log_path=get("log_path", "reader_agent.log"),
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


def run_reader_loop(config: AgentConfig, spool: Spool, stop_event: threading.Event) -> None:
    """Endlosschleife über nfcpy. Reader-Aussetzer (Kabel ab, PC im Standby, ...) führen
    zu einem Reconnect-Versuch statt zum Absturz des Agenten."""
    import nfc  # lokaler Import: --simulate-uid soll ohne diese Abhängigkeit laufen

    def on_connect(tag) -> bool:
        uid = tag.identifier.hex().upper()
        handle_scan(config, spool, uid)
        time.sleep(config.scan_cooldown)
        return True  # True = weiter auf die nächste Karte warten

    while not stop_event.is_set():
        try:
            with nfc.ContactlessFrontend(config.reader) as clf:
                LOG.info("Reader verbunden: %s", config.reader)
                clf.connect(rdwr={"on-connect": on_connect}, terminate=stop_event.is_set)
        except OSError as exc:
            LOG.warning("Reader nicht erreichbar (%s) — neuer Versuch in 5s: %s", config.reader, exc)
            stop_event.wait(5)
        except Exception:  # noqa: BLE001
            LOG.exception("Unerwarteter Fehler in der Reader-Schleife — neuer Versuch in 5s")
            stop_event.wait(5)


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
