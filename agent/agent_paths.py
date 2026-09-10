#!/usr/bin/env python3
"""Plattformabhängige Standardpfade des Reader-Agenten (Windows, Linux, macOS).

Unter Windows liegt alles traditionell in EINEM Ordner (`C:\\rz-checkin-agent` mit
`agent.ini`, Log und Offline-Puffer daneben) -- genau so bleibt es auch, damit
bestehende Installationen unverändert weiterlaufen.

Unter Linux ist ein solcher "alles in einem Ordner"-Aufbau unüblich und funktioniert
mit einer Binärdatei in `/usr/local/bin` auch schlicht nicht: das Arbeitsverzeichnis
ist beim Start über `.desktop`/systemd nicht vorhersagbar und `/usr/local/bin` ist für
den Kiosk-Benutzer nicht beschreibbar. Deshalb gelten dort die üblichen XDG-Pfade
(`~/.config/rz-checkin-agent/agent.ini`, `~/.local/state/rz-checkin-agent/` für Log und
Offline-Puffer), zusätzlich wird `/etc/rz-checkin-agent/agent.ini` für den systemweiten
Dienst berücksichtigt.

Nur Standardbibliothek -- das Modul wird sowohl vom reinen Kommandozeilen-Agenten als
auch von der GUI-Variante importiert.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

APP_DIR_NAME = "rz-checkin-agent"
WINDOWS_APP_DIR_NAME = "RZ-CheckIn-Agent"
CONFIG_FILE_NAME = "agent.ini"

# Umgebungsvariable, die alle Suchpfade übersteuert -- praktisch für Dienste, die die
# Konfiguration an einer festen Stelle liegen haben (siehe agent/linux/*.service).
CONFIG_ENV_VAR = "RZ_AGENT_CONFIG"

# Systemweite Konfiguration (nur Linux/macOS): vom systemd-Dienst genutzt, wenn der
# Agent ohne angemeldeten Benutzer laufen soll.
SYSTEM_CONFIG_PATH = Path("/etc") / APP_DIR_NAME / CONFIG_FILE_NAME


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def program_dir() -> Path:
    """Ordner, in dem das laufende Programm liegt -- bei einer PyInstaller-Binärdatei
    der Ordner der .exe bzw. der Linux-Binärdatei (NICHT das temporäre Entpack-
    Verzeichnis `sys._MEIPASS`), sonst der Ordner dieses Moduls."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def user_config_dir() -> Path:
    """Benutzerbezogener Konfigurationsordner nach den Konventionen der Plattform."""
    if is_windows():
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / WINDOWS_APP_DIR_NAME
        return Path.home() / "AppData" / "Roaming" / WINDOWS_APP_DIR_NAME
    if is_macos():
        return Path.home() / "Library" / "Application Support" / WINDOWS_APP_DIR_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".config" / APP_DIR_NAME


def user_data_dir() -> Path:
    """Ordner für veränderliche Dateien (Log, Offline-Puffer)."""
    if is_windows():
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / WINDOWS_APP_DIR_NAME
        return Path.home() / "AppData" / "Local" / WINDOWS_APP_DIR_NAME
    if is_macos():
        return Path.home() / "Library" / "Application Support" / WINDOWS_APP_DIR_NAME
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".local" / "state" / APP_DIR_NAME


def config_search_paths() -> list[Path]:
    """Alle Orte, an denen nach einer vorhandenen `agent.ini` gesucht wird -- in genau
    dieser Reihenfolge, der erste Treffer gewinnt.

    Das Arbeitsverzeichnis steht bewusst vorn: so verhält sich der Agent in einem
    ausgepackten Ordner (der bisherige Windows-Aufbau und die Entwicklung im
    Repository) wie gehabt, ohne dass jemand etwas umstellen muss."""
    paths = [
        Path.cwd() / CONFIG_FILE_NAME,
        program_dir() / CONFIG_FILE_NAME,
        user_config_dir() / CONFIG_FILE_NAME,
    ]
    if not is_windows():
        paths.append(SYSTEM_CONFIG_PATH)
    # Doppelte Einträge entfernen (z.B. wenn das Programm im Arbeitsverzeichnis liegt),
    # Reihenfolge dabei erhalten.
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def default_config_path() -> Path:
    """Wohin eine NEUE `agent.ini` geschrieben wird, wenn noch keine existiert.

    Windows: in den Programmordner (der gewohnte "ein Ordner für alles"-Aufbau).
    Linux/macOS: in den Konfigurationsordner des Benutzers -- der Programmordner ist
    dort typischerweise `/usr/local/bin` und nicht beschreibbar."""
    if is_windows():
        return program_dir() / CONFIG_FILE_NAME
    return user_config_dir() / CONFIG_FILE_NAME


def resolve_config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Pfad zur `agent.ini`, die verwendet werden soll.

    Reihenfolge: ausdrücklich übergebener Pfad (`--config`) > Umgebungsvariable
    RZ_AGENT_CONFIG > erste existierende Datei aus `config_search_paths()` >
    `default_config_path()` (existiert dann noch nicht -- die GUI legt sie über das
    Einstellungen-Fenster an)."""
    if explicit:
        return Path(explicit).expanduser()
    from_env = os.environ.get(CONFIG_ENV_VAR)
    if from_env:
        return Path(from_env).expanduser()
    for candidate in config_search_paths():
        if candidate.is_file():
            return candidate
    return default_config_path()


def _is_writable_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        with tempfile.NamedTemporaryFile(dir=path, prefix=".rz-checkin-", suffix=".tmp"):
            return True
    except OSError:
        # Nicht nur os.access() -- das meldet für root auch dort Schreibrechte, wo das
        # Dateisystem read-only eingehängt ist.
        return False


def data_dir_for(config_path: Path) -> Path:
    """Basisordner für Log und Offline-Puffer, wenn in der `agent.ini` relative Pfade
    stehen (der Auslieferungszustand).

    Bevorzugt wird der Ordner der `agent.ini` selbst -- damit bleibt der Windows-Aufbau
    "alles in einem Ordner" exakt wie bisher. Ist er nicht beschreibbar (typischer
    Linux-Fall: Konfiguration unter `/etc/rz-checkin-agent`), wird auf das
    Datenverzeichnis des Benutzers ausgewichen, statt den Agenten beim Schreiben des
    Logs scheitern zu lassen."""
    parent = config_path.expanduser().parent
    if _is_writable_dir(parent):
        return parent
    return user_data_dir()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def apply_data_paths(config, config_path: Path) -> None:
    """Macht relative `spool_path`/`log_path` aus der Konfiguration absolut (siehe
    `data_dir_for`) und legt den Zielordner an. Wird sowohl vom Kommandozeilen-Agenten
    als auch von der GUI direkt nach dem Laden der Konfiguration aufgerufen, damit beide
    Varianten dieselben Dateien verwenden -- unabhängig davon, aus welchem
    Arbeitsverzeichnis heraus gestartet wurde."""
    base: Path | None = None
    for attribute in ("spool_path", "log_path"):
        value = getattr(config, attribute, None)
        if not value or Path(value).is_absolute():
            continue
        if base is None:
            base = ensure_dir(data_dir_for(config_path))
        setattr(config, attribute, str(base / value))
