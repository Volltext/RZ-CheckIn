#!/usr/bin/env python3
"""GUI-Variante des Reader-Agenten für den Kiosk-PC — Windows **und** Linux.

Sie zeigt ein Symbol im Infobereich/Systray (grün = verbunden, grau = Verbindungs-
störung) mit Kontextmenü sowie ein kleines Einstellungen-Fenster für die vier
Kernwerte der `agent.ini`, damit auf dem Kiosk-PC weder Konsole noch Texteditor
gebraucht werden. Gebaut wird daraus eine einzelne Programmdatei (Windows:
`agent/build_exe.ps1` → `RZ-CheckIn-Agent.exe`, Linux: `agent/build_linux.sh` →
`rz-checkin-agent`), die ohne Python-Installation auskommt.

Je nach vorhandener Desktop-Umgebung wählt die App automatisch eine von drei
Betriebsarten (siehe `choose_ui_mode`):

* **tray**     — Symbol im Infobereich, Einstellungen über das Kontextmenü.
                 Der Normalfall unter Windows und auf Linux-Desktops mit Tray
                 (KDE, XFCE, Cinnamon, MATE, GNOME mit AppIndicator-Erweiterung).
* **window**   — Es gibt eine grafische Oberfläche, aber keinen nutzbaren Tray
                 (typisch: GNOME/Wayland ohne AppIndicator-Erweiterung). Statt eines
                 Symbols erscheint ein kleines Fenster mit Statusanzeige und denselben
                 Einstellungen.
* **headless** — Gar keine grafische Oberfläche (Server, SSH, systemd-Dienst): der
                 Agent läuft ohne Oberfläche weiter und protokolliert auf stdout, so
                 dass dieselbe Programmdatei auch als Dienst taugt (`--headless`).

Die eigentliche Agent-Logik (Reader-Loop, Heartbeat, Offline-Spool) liegt komplett in
reader_agent.py -- dieses Modul ist bewusst nur eine dünne Hülle drumherum, damit
Kernlogik und deren Tests ohne GUI-Abhängigkeiten (pystray/Pillow/tkinter) auskommen.
Alle GUI-Importe passieren deshalb erst zur Laufzeit in der jeweiligen Betriebsart und
nicht beim Import des Moduls.

Abhängigkeiten (siehe agent/requirements-tray.txt): pystray, Pillow, unter Linux
zusätzlich python-xlib für den Tray. tkinter (Einstellungen-Fenster) ist unter Windows
Teil der Standard-Python-Installation, unter Linux liefert es das Distributionspaket
`python3-tk` (Debian/Ubuntu) bzw. `python3-tkinter` (RHEL/Fedora).
"""

from __future__ import annotations

import argparse
import configparser
import logging
import os
import signal
import sys
import threading
from pathlib import Path

import agent_paths
from reader_agent import AgentConfig, AgentRuntime, _setup_logging

LOG = logging.getLogger("tray_app")

APP_NAME = "RZ-CheckIn Agent"

# Status des Agenten, wie ihn Symbol und Fenster anzeigen. "online"/"offline" kommen aus
# der AgentRuntime (Heartbeat erfolgreich bzw. nicht), die beiden anderen setzt diese
# Hülle selbst.
STATUS_UNCONFIGURED = "unkonfiguriert"
STATUS_STOPPED = "gestoppt"

_STATUS_TEXTS = {
    "online": "verbunden",
    "offline": "keine Verbindung zum Server",
    STATUS_UNCONFIGURED: "noch nicht eingerichtet",
    STATUS_STOPPED: "gestoppt",
}

# Grün = Heartbeat kommt durch, Grau = alles andere (siehe agent/README.md Abschnitt 7).
_STATUS_COLORS = {
    "online": "#16a34a",
    "offline": "#94a3b8",
    STATUS_UNCONFIGURED: "#94a3b8",
    STATUS_STOPPED: "#94a3b8",
}

_FIELDS = [
    ("server_url", "Server-URL", "https://rz-checkin.intern.example.org"),
    ("agent_id", "Agent-ID", "kiosk1"),
    ("api_key", "API-Key", ""),
    ("reader", "Reader (nur der Wert, z.B. usb:072f:2200 für ACR122U)", "usb:072f:2200"),
]

# Wie oft das Fenster den aktuellen Status abfragt. Die Statusmeldung kommt aus einem
# Hintergrund-Thread der AgentRuntime; Tkinter darf aber nur aus seinem eigenen Thread
# heraus angefasst werden -- deshalb Abholen per Timer statt Zuruf von außen.
_STATUS_POLL_MS = 500


def status_text(status: str) -> str:
    return _STATUS_TEXTS.get(status, status)


def status_color(status: str) -> str:
    return _STATUS_COLORS.get(status, _STATUS_COLORS[STATUS_STOPPED])


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
        ("reset_after_failures", "3"),
        ("reset_command", ""),
    ):
        parser["agent"].setdefault(key, default)
    # Unter Linux liegt die Datei standardmäßig unter ~/.config/rz-checkin-agent/ --
    # dieser Ordner existiert beim ersten Start noch nicht.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        parser.write(f)
    if not agent_paths.is_windows():
        # Der API-Key steht im Klartext in der Datei: unter Linux gleich auf 0600 setzen
        # (unter Windows erbt sie die Rechte des Ordners, siehe agent/README.md).
        try:
            path.chmod(0o600)
        except OSError as exc:  # noqa: BLE001 - z.B. exotische Dateisysteme
            LOG.debug("Dateirechte für %s konnten nicht gesetzt werden: %s", path, exc)


# --------------------------------------------------------------------------------------
# Erkennung der verfügbaren Oberfläche
# --------------------------------------------------------------------------------------


def has_display() -> bool:
    """Gibt es überhaupt eine grafische Oberfläche? Unter Linux hängt das an einem
    gesetzten DISPLAY (X11/XWayland) bzw. WAYLAND_DISPLAY -- per SSH oder aus einem
    systemd-Systemdienst heraus ist beides leer."""
    if agent_paths.is_windows() or agent_paths.is_macos():
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def tray_backend() -> str | None:
    """Name des von pystray gewählten Backends ("win32", "appindicator", "gtk",
    "xorg", ...) oder None, wenn kein nutzbares gefunden wurde.

    pystray sucht sich sein Backend beim Import selbst aus. Findet es keines, scheitert
    schon der Import -- unter Linux der Normalfall, wenn weder AppIndicator/GTK noch
    python-xlib vorhanden sind. Der Import kann dabei auch mit anderen Fehlern als
    ImportError aussteigen: ist z.B. PyGObject installiert, die GTK-Typbibliothek aber
    nicht, wirft pystray beim Backend-Import eine ValueError ("Namespace Gtk not
    available"). Deshalb wird hier jeder Fehler abgefangen -- besser das Fenster
    anbieten, als beim Start mit einem Traceback stehen zu bleiben.

    Zusätzlich wird das Platzhalter-Backend erkannt (PYSTRAY_BACKEND=dummy): es reicht
    lediglich die abstrakte Basisklasse pystray._base durch und kann kein Symbol
    anzeigen."""
    try:
        import pystray

        module = getattr(pystray.Icon, "__module__", "")
    except Exception as exc:  # noqa: BLE001 - fehlendes Paket ODER fehlgeschlagener Backend-Import
        LOG.debug("pystray nicht verfügbar: %s", exc)
        return None
    name = module.rsplit(".", 1)[-1].lstrip("_")
    if name in ("", "dummy", "base"):
        LOG.debug("pystray hat kein nutzbares Backend gefunden (%s)", module or "?")
        return None
    return name


def _xorg_tray_manager_present() -> bool:
    """Gibt es im X-Server überhaupt einen Systray-Manager (Selection
    `_NET_SYSTEM_TRAY_S<n>`)?

    Das X11-Backend von pystray hängt sein Symbol per XEmbed in genau diesen Manager.
    Fehlt er, wartet pystray still darauf, dass einer auftaucht -- die Anwendung läuft
    dann zwar, ist aber komplett unsichtbar. Genau das passiert unter GNOME: der
    klassische Tray wurde dort entfernt, und selbst mit AppIndicator-Erweiterung läuft
    die Anzeige über DBus statt über XEmbed. Also lieber vorher nachsehen und in dem Fall
    das Fenster anbieten."""
    try:
        from Xlib import display as xdisplay

        d = xdisplay.Display()
        try:
            atom = d.intern_atom(f"_NET_SYSTEM_TRAY_S{d.get_default_screen()}")
            owner = d.get_selection_owner(atom)
            return bool(getattr(owner, "id", owner))
        finally:
            d.close()
    except Exception as exc:  # noqa: BLE001 - im Zweifel den Tray versuchen
        LOG.debug("Systray-Manager nicht prüfbar (%s) -- nehme an, es gibt einen", exc)
        return True


def tray_available() -> bool:
    """Lässt sich auf dieser Oberfläche wirklich ein Symbol im Infobereich anzeigen?"""
    backend = tray_backend()
    if backend is None:
        return False
    if backend == "xorg" and not _xorg_tray_manager_present():
        LOG.info("Kein Systray-Manager gefunden (z.B. GNOME) -- die App zeigt ihr Fenster")
        return False
    return True


def tk_available() -> bool:
    """Ist tkinter installiert? Unter Windows immer, unter Linux erst mit dem
    Distributionspaket python3-tk bzw. python3-tkinter."""
    try:
        import tkinter  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        LOG.debug("tkinter nicht verfügbar: %s", exc)
        return False
    return True


def choose_ui_mode(
    *,
    force_headless: bool = False,
    force_window: bool = False,
    display: bool | None = None,
    tray: bool | None = None,
    tk: bool | None = None,
) -> str:
    """Wählt die Betriebsart: "tray", "window" oder "headless".

    Die drei Prüfungen sind als Parameter herausgezogen, damit sich die Auswahl ohne
    Desktop-Umgebung testen lässt."""
    if force_headless:
        return "headless"
    display = has_display() if display is None else display
    if not display:
        return "headless"
    tk = tk_available() if tk is None else tk
    if force_window:
        return "window" if tk else "headless"
    tray = tray_available() if tray is None else tray
    if tray:
        return "tray"
    if tk:
        return "window"
    return "headless"


# --------------------------------------------------------------------------------------
# Gemeinsamer Unterbau: Konfiguration laden, Agent starten/stoppen
# --------------------------------------------------------------------------------------


class AgentApplication:
    """Hält Konfiguration und AgentRuntime. Systray, Fenster und Headless-Modus sind nur
    drei verschiedene Oberflächen auf genau diesem Objekt."""

    def __init__(self, config_path: Path, *, verbose: bool = False):
        self.config_path = config_path
        self.verbose = verbose
        self.runtime: AgentRuntime | None = None
        self.config: AgentConfig | None = None
        self.status = STATUS_STOPPED
        self.last_error: str | None = None
        # Wird gesetzt, solange ein Systray-Symbol auf Statuswechsel reagieren soll.
        self.on_status_change = None

    def _set_status(self, status: str) -> None:
        self.status = status
        callback = self.on_status_change
        if callback is not None:
            callback(status)

    def start(self) -> bool:
        """Lädt die Konfiguration und startet den Agenten. False = Konfiguration fehlt
        oder ist unvollständig (die Oberfläche zeigt dann die Einstellungen)."""
        try:
            config = AgentConfig.from_file(str(self.config_path))
        except FileNotFoundError:
            self.last_error = f"Keine Konfiguration gefunden ({self.config_path})"
            LOG.warning("%s -- Einstellungen öffnen und ausfüllen.", self.last_error)
            self._set_status(STATUS_UNCONFIGURED)
            return False
        except ValueError as exc:
            self.last_error = str(exc)
            LOG.error("Konfiguration ungültig: %s", exc)
            self._set_status(STATUS_UNCONFIGURED)
            return False

        agent_paths.apply_data_paths(config, self.config_path)
        _setup_logging(config, verbose=self.verbose)
        LOG.info("Konfiguration geladen: %s", self.config_path)
        self.stop()
        self.config = config
        self.last_error = None
        self.runtime = AgentRuntime(config, on_status=self._set_status)
        self.runtime.start()
        # Bis zum ersten Heartbeat ist noch nichts bestätigt -- lieber "keine Verbindung"
        # anzeigen als fälschlich grün.
        self._set_status("offline")
        return True

    def stop(self) -> None:
        if self.runtime is not None:
            self.runtime.stop()
            self.runtime = None
        self._set_status(STATUS_STOPPED)

    def log_path(self) -> str | None:
        return self.config.log_path if self.config else None


# --------------------------------------------------------------------------------------
# Fenster (Einstellungen bzw. Hauptfenster ohne Systray)
# --------------------------------------------------------------------------------------


class AgentWindow:
    """Kleines Tkinter-Fenster für die vier wichtigsten agent.ini-Werte.

    mode="dialog": Einstellungen-Fenster, aus dem Systray heraus geöffnet -- Speichern
                   bzw. Abbrechen schließt es wieder.
    mode="main":   Hauptfenster, wenn es keinen Systray gibt (GNOME/Wayland). Zusätzlich
                   mit Statuszeile und "Beenden", weil es sonst keine Bedienoberfläche
                   für den Agenten gäbe.

    Bewusst kein komplettes Einstellungs-Framework -- die restlichen (selten geänderten)
    Werte bleiben in der Datei erhalten und lassen sich bei Bedarf dort von Hand
    anpassen."""

    def __init__(
        self,
        app: AgentApplication,
        *,
        mode: str = "dialog",
        on_saved=None,
        on_quit=None,
    ):
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.mode = mode
        self.on_saved = on_saved or (lambda: None)
        self.on_quit = on_quit or (lambda: None)
        self.entries: dict[str, "tk.Entry"] = {}
        # Kennung des laufenden Status-Timers, damit er beim Schließen abbestellt werden
        # kann (siehe _quit).
        self._status_job: str | None = None

        self.root = tk.Tk()
        titel = APP_NAME if mode == "main" else f"{APP_NAME} — Einstellungen"
        self.root.title(titel)
        self.root.resizable(False, False)

        frame = ttk.Frame(self.root, padding=16)
        frame.grid()
        row = 0

        if mode == "main":
            status_frame = ttk.Frame(frame)
            status_frame.grid(column=0, row=row, columnspan=2, sticky="w", pady=(0, 12))
            self._lamp = tk.Canvas(status_frame, width=14, height=14, highlightthickness=0)
            self._lamp_dot = self._lamp.create_oval(2, 2, 12, 12, fill=status_color(app.status), width=0)
            self._lamp.grid(column=0, row=0, padx=(0, 8))
            self._status_label = ttk.Label(status_frame, text=status_text(app.status))
            self._status_label.grid(column=1, row=0, sticky="w")
            row += 1
        else:
            self._lamp = None
            self._status_label = None

        current = _load_raw_config(app.config_path)
        for key, label, _placeholder in _FIELDS:
            ttk.Label(frame, text=label).grid(column=0, row=row, sticky="w", pady=4)
            show = "*" if key == "api_key" else None
            entry = ttk.Entry(frame, width=42, show=show)
            entry.insert(0, current.get(key, ""))
            entry.grid(column=1, row=row, pady=4, padx=(8, 0))
            self.entries[key] = entry
            row += 1

        ttk.Label(
            frame,
            text="API-Key wird beim Anlegen des Agenten im Admin-Bereich einmalig angezeigt.",
            foreground="#666666",
            wraplength=340,
        ).grid(column=0, row=row, columnspan=2, sticky="w", pady=(4, 4))
        row += 1

        ttk.Label(
            frame,
            text=f"Konfiguration: {app.config_path}",
            foreground="#666666",
            wraplength=460,
        ).grid(column=0, row=row, columnspan=2, sticky="w", pady=(0, 12))
        row += 1

        button_frame = ttk.Frame(frame)
        button_frame.grid(column=0, row=row, columnspan=2, sticky="e")
        if mode == "main":
            ttk.Button(button_frame, text="Speichern & neu starten", command=self._save).grid(
                column=0, row=0, padx=4
            )
            ttk.Button(button_frame, text="Beenden", command=self._quit).grid(column=1, row=0)
            self.root.protocol("WM_DELETE_WINDOW", self._quit)
            self._status_job = self.root.after(_STATUS_POLL_MS, self._refresh_status)
        else:
            ttk.Button(button_frame, text="Speichern", command=self._save).grid(column=0, row=0, padx=4)
            ttk.Button(button_frame, text="Abbrechen", command=self.root.destroy).grid(column=1, row=0)

    def _refresh_status(self) -> None:
        """Holt den Status im GUI-Thread ab (siehe _STATUS_POLL_MS)."""
        # Immer nur EIN eingeplanter Aufruf: sonst hinterlässt ein Aufruf von außen
        # (Statuswechsel sofort anzeigen) einen zweiten Timer, den _quit nicht mehr
        # abbestellen kann.
        if self._status_job is not None:
            self.root.after_cancel(self._status_job)
            self._status_job = None
        if self._status_label is not None:
            self._status_label.config(text=status_text(self.app.status))
            self._lamp.itemconfig(self._lamp_dot, fill=status_color(self.app.status))
        self._status_job = self.root.after(_STATUS_POLL_MS, self._refresh_status)

    def _save(self) -> None:
        from tkinter import messagebox

        values = {key: entry.get().strip() for key, entry in self.entries.items()}
        missing = [label for key, label, _ in _FIELDS if key != "api_key" and not values[key]]
        if missing:
            messagebox.showerror(APP_NAME, f"Bitte ausfüllen: {', '.join(missing)}")
            return
        # api_key nur überschreiben, wenn tatsächlich etwas eingegeben wurde -- sonst
        # bleibt ein bereits gespeicherter Key erhalten (Feld zeigt ihn maskiert an).
        if not values["api_key"]:
            del values["api_key"]
        _save_raw_config(self.app.config_path, values)
        if self.mode == "dialog":
            self.root.destroy()
            self.on_saved()
            return
        # Hauptfenster bleibt offen: Agent mit der neuen Konfiguration neu starten und
        # das Ergebnis direkt in der Statuszeile zeigen.
        self.on_saved()
        if self.app.last_error:
            messagebox.showerror(APP_NAME, self.app.last_error)

    def _quit(self) -> None:
        # Erst den Status-Timer abbestellen: ein noch eingeplanter Aufruf würde nach dem
        # destroy() auf ein nicht mehr existierendes Fenster zugreifen und Tk eine
        # Fehlermeldung ("invalid command name ...") ausgeben lassen.
        if self._status_job is not None:
            self.root.after_cancel(self._status_job)
            self._status_job = None
        self.on_quit()
        self.root.destroy()

    def show(self) -> None:
        self.root.mainloop()


# --------------------------------------------------------------------------------------
# Betriebsarten
# --------------------------------------------------------------------------------------


class TrayApplication:
    """Symbol im Infobereich (Windows) bzw. Systray (Linux) mit Kontextmenü."""

    def __init__(self, app: AgentApplication):
        self.app = app
        self._icon = None  # pystray.Icon, erst in run() erzeugt
        app.on_status_change = self._on_status

    def _on_status(self, status: str) -> None:
        if self._icon is not None:
            self._icon.icon = _make_icon_image(status)
            self._icon.title = f"{APP_NAME} — {status_text(status)}"

    def _start(self) -> None:
        if not self.app.start():
            self._open_settings()

    def _open_settings(self) -> None:
        # Tkinter braucht den Hauptthread einer eigenen Mainloop -- läuft daher in einem
        # eigenen Thread, damit das Systray-Symbol (eigene Eventloop) weiterläuft.
        def _run():
            AgentWindow(self.app, mode="dialog", on_saved=self._start).show()

        threading.Thread(target=_run, name="settings-window", daemon=True).start()

    def _quit(self) -> None:
        self.app.stop()
        if self._icon is not None:
            self._icon.stop()

    def run(self) -> None:
        import pystray
        from pystray import MenuItem as Item

        self._start()

        menu = pystray.Menu(
            Item("Einstellungen …", lambda: self._open_settings()),
            Item("Beenden", lambda: self._quit()),
        )
        self._icon = pystray.Icon(APP_NAME, _make_icon_image(self.app.status), APP_NAME, menu)
        self._icon.run()


def run_window(app: AgentApplication) -> int:
    """Hauptfenster statt Systray -- für Desktops ohne nutzbaren Infobereich."""
    app.start()  # Fehlende Konfiguration ist hier kein Sonderfall: das Fenster zeigt sie
    window = AgentWindow(app, mode="main", on_saved=app.start, on_quit=app.stop)
    window.show()
    app.stop()
    return 0


def run_headless(app: AgentApplication) -> int:
    """Ohne Oberfläche: der Agent läuft im Vordergrund weiter und protokolliert auf
    stdout -- so lässt sich dieselbe Programmdatei auch als systemd-Dienst betreiben
    (siehe agent/linux/rz-checkin-agent.service)."""
    if not app.start():
        print(
            f"{app.last_error}\n\n"
            "Konfiguration anlegen (agent.ini.example als Vorlage) oder den Agenten "
            "einmal mit grafischer Oberfläche starten und die Einstellungen ausfüllen.",
            file=sys.stderr,
        )
        return 2

    stop_event = threading.Event()

    def _handle_signal(signum, _frame):
        LOG.info("Signal %s empfangen -- beende Agent", signum)
        stop_event.set()

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handle_signal)
            except (ValueError, OSError) as exc:  # noqa: BLE001 - z.B. Aufruf aus einem Thread
                LOG.debug("Signal %s nicht behandelbar: %s", name, exc)

    LOG.info("Agent läuft ohne grafische Oberfläche (Beenden mit Strg+C bzw. SIGTERM)")
    try:
        stop_event.wait()
    except KeyboardInterrupt:
        LOG.info("Beende auf Benutzerwunsch (Strg+C)")
    finally:
        app.stop()
    return 0


def _make_icon_image(status: str):
    from PIL import Image, ImageDraw

    color = status_color(status).lstrip("#")
    rgb = tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, size - 4, size - 4), fill=rgb)
    return image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reader-Agent mit kleiner Oberfläche (Systray bzw. Fenster).",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Pfad zur agent.ini (ohne Angabe wird an den üblichen Stellen der Plattform gesucht)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Ohne Oberfläche starten (z.B. als systemd-Dienst)",
    )
    parser.add_argument(
        "--window",
        action="store_true",
        help="Fenster statt Systray-Symbol erzwingen",
    )
    parser.add_argument("--verbose", action="store_true", help="Debug-Logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    config_path = agent_paths.resolve_config_path(args.config)
    app = AgentApplication(config_path, verbose=args.verbose)

    mode = choose_ui_mode(force_headless=args.headless, force_window=args.window)
    LOG.info("Betriebsart: %s (Konfiguration: %s)", mode, config_path)

    if mode == "tray":
        try:
            TrayApplication(app).run()
            return 0
        except Exception as exc:  # noqa: BLE001 - Tray kann trotz Backend zur Laufzeit scheitern
            LOG.warning("Systray nicht nutzbar (%s) -- weiter mit Fenster", exc)
            app.on_status_change = None
            app.stop()
            mode = "window" if tk_available() else "headless"

    if mode == "window":
        return run_window(app)
    return run_headless(app)


if __name__ == "__main__":
    sys.exit(main())
