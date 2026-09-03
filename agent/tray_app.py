#!/usr/bin/env python3
"""Systray-Anwendung für den Reader-Agenten: Icon im Infobereich (grün = online, rot =
Verbindungsstörung), Kontextmenü sowie eine kleine Einstellungen-GUI zum Bearbeiten von
agent.ini, ohne eine Konsole/Texteditor öffnen zu müssen.

Gedacht für den Windows-Autostart auf dem Kiosk-PC: als .exe gebaut (siehe
agent/build_exe.ps1) im Autostart-Ordner oder per Aufgabenplanung starten, fertig -- kein
separater Dienst-Manager (nssm) nötig, wer den lieber weiter nutzen will, kann stattdessen
weiterhin reader_agent.py direkt als Dienst betreiben (siehe agent/README.md).

Die eigentliche Agent-Logik (Reader-Loop, Heartbeat, Offline-Spool) liegt komplett in
reader_agent.py -- dieses Modul ist bewusst nur eine dünne GUI-Hülle drumherum, damit
Kernlogik und Test der Kernlogik unverändert ohne GUI-Abhängigkeiten (pystray/Pillow/
tkinter) funktionieren.

Abhängigkeiten (siehe agent/requirements-tray.txt): pystray, Pillow. tkinter ist Teil der
Standard-Python-Installation unter Windows und braucht keine zusätzliche Installation.
"""

from __future__ import annotations

import configparser
import logging
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from reader_agent import AgentConfig, AgentRuntime, _setup_logging

LOG = logging.getLogger("tray_app")

APP_NAME = "RZ-CheckIn Agent"
DEFAULT_CONFIG_PATH = Path("agent.ini")

_FIELDS = [
    ("server_url", "Server-URL", "https://rz-checkin.intern.example.org"),
    ("agent_id", "Agent-ID", "kiosk1"),
    ("api_key", "API-Key", ""),
    ("reader", "Reader (ACR122U: usb:072f:2200)", "usb:072f:2200"),
]


def _load_raw_config(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path, encoding="utf-8")
    if not parser.has_section("agent"):
        parser.add_section("agent")
    return dict(parser["agent"])


def _save_raw_config(path: Path, values: dict[str, str]) -> None:
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path, encoding="utf-8")
    if not parser.has_section("agent"):
        parser.add_section("agent")
    for key, value in values.items():
        parser["agent"][key] = value
    # Restliche, hier nicht editierte Werte (Intervalle, Pfade, ...) bleiben unangetastet
    # -- ein bereits vorhandener agent.ini wird also nur um die vier Kernfelder ergänzt,
    # nicht komplett überschrieben.
    for key, default in (
        ("heartbeat_interval", "30"),
        ("spool_flush_interval", "15"),
        ("scan_cooldown", "1.0"),
        ("verify_tls", "true"),
        ("spool_path", "agent_spool.jsonl"),
        ("log_path", "reader_agent.log"),
    ):
        parser["agent"].setdefault(key, default)
    with path.open("w", encoding="utf-8") as f:
        parser.write(f)


class SettingsWindow:
    """Modales Tkinter-Fenster zum Bearbeiten der vier wichtigsten agent.ini-Werte.
    Bewusst kein komplettes Einstellungs-Framework -- die restlichen (selten geänderten)
    Werte bleiben in der Datei erhalten und lassen sich bei Bedarf dort von Hand anpassen."""

    def __init__(self, config_path: Path, on_saved):
        self.config_path = config_path
        self.on_saved = on_saved
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} — Einstellungen")
        self.root.resizable(False, False)
        self.entries: dict[str, tk.Entry] = {}

        current = _load_raw_config(config_path)

        frame = ttk.Frame(self.root, padding=16)
        frame.grid()

        for row, (key, label, placeholder) in enumerate(_FIELDS):
            ttk.Label(frame, text=label).grid(column=0, row=row, sticky="w", pady=4)
            show = "*" if key == "api_key" else None
            entry = ttk.Entry(frame, width=42, show=show)
            entry.insert(0, current.get(key, ""))
            entry.grid(column=1, row=row, pady=4, padx=(8, 0))
            self.entries[key] = entry

        hinweis = ttk.Label(
            frame,
            text="API-Key wird beim Anlegen des Agenten im Admin-Bereich einmalig angezeigt.",
            foreground="#666666",
            wraplength=340,
        )
        hinweis.grid(column=0, row=len(_FIELDS), columnspan=2, sticky="w", pady=(4, 12))

        button_frame = ttk.Frame(frame)
        button_frame.grid(column=0, row=len(_FIELDS) + 1, columnspan=2, sticky="e")
        ttk.Button(button_frame, text="Speichern", command=self._save).grid(column=0, row=0, padx=4)
        ttk.Button(button_frame, text="Abbrechen", command=self.root.destroy).grid(column=1, row=0)

    def _save(self) -> None:
        values = {key: entry.get().strip() for key, entry in self.entries.items()}
        missing = [label for key, label, _ in _FIELDS if key != "api_key" and not values[key]]
        if missing:
            messagebox.showerror(APP_NAME, f"Bitte ausfüllen: {', '.join(missing)}")
            return
        # api_key nur überschreiben, wenn tatsächlich etwas eingegeben wurde -- sonst
        # bleibt ein bereits gespeicherter Key erhalten (Feld zeigt ihn maskiert an).
        if not values["api_key"]:
            del values["api_key"]
        _save_raw_config(self.config_path, values)
        self.root.destroy()
        self.on_saved()

    def show(self) -> None:
        self.root.mainloop()


class TrayApplication:
    def __init__(self, config_path: Path = DEFAULT_CONFIG_PATH):
        self.config_path = config_path
        self.runtime: AgentRuntime | None = None
        self._status = "offline"
        self._icon = None  # pystray.Icon, lazy (Import erst in run())

    def _status_callback(self, status: str) -> None:
        self._status = status
        if self._icon is not None:
            self._icon.icon = _make_icon_image(status)
            self._icon.title = f"{APP_NAME} — {'verbunden' if status == 'online' else 'keine Verbindung'}"

    def _start_runtime(self) -> None:
        if not self.config_path.exists():
            LOG.warning("Keine agent.ini gefunden (%s) -- Einstellungen öffnen sich automatisch.", self.config_path)
            self._open_settings()
            return
        try:
            config = AgentConfig.from_file(str(self.config_path))
        except (FileNotFoundError, ValueError) as exc:
            LOG.error("Konfiguration ungültig: %s", exc)
            self._open_settings()
            return
        _setup_logging(config, verbose=False)
        if self.runtime is not None:
            self.runtime.stop()
        self.runtime = AgentRuntime(config, on_status=self._status_callback)
        self.runtime.start()

    def _open_settings(self) -> None:
        # Tkinter braucht den Hauptthread einer eigenen Mainloop -- läuft daher in einem
        # eigenen Thread, damit das Systray-Icon (eigene Eventloop) weiterläuft.
        def _run():
            window = SettingsWindow(self.config_path, on_saved=self._start_runtime)
            window.show()

        threading.Thread(target=_run, name="settings-window", daemon=True).start()

    def _quit(self) -> None:
        if self.runtime is not None:
            self.runtime.stop()
        if self._icon is not None:
            self._icon.stop()

    def run(self) -> None:
        import pystray
        from pystray import MenuItem as Item

        self._start_runtime()

        menu = pystray.Menu(
            Item("Einstellungen …", lambda: self._open_settings()),
            Item("Beenden", lambda: self._quit()),
        )
        self._icon = pystray.Icon(APP_NAME, _make_icon_image(self._status), APP_NAME, menu)
        self._icon.run()


def _make_icon_image(status: str):
    from PIL import Image, ImageDraw

    color = (22, 163, 74) if status == "online" else (148, 163, 184)
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, size - 4, size - 4), fill=color)
    return image


def main() -> int:
    TrayApplication().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
