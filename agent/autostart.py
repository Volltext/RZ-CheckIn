#!/usr/bin/env python3
"""Autostart des Agenten für den angemeldeten Benutzer ein- und ausschalten.

Damit lässt sich der Autostart direkt im Einstellungen-Fenster umlegen, statt ihn unter
Windows von Hand in den Autostart-Ordner zu legen bzw. unter Linux `linux/install.sh`
aufzurufen. Beide Wege schreiben denselben Eintrag -- wer den Autostart über install.sh
eingerichtet hat, sieht die Checkbox im Fenster gesetzt und kann ihn dort wieder
abschalten.

Bewusst nur benutzerbezogen (kein Dienst, keine Administratorrechte):
* Windows: Wert im Registry-Schlüssel HKCU\\...\\CurrentVersion\\Run -- dasselbe, was eine
  Verknüpfung im Autostart-Ordner bewirkt, aber ohne .lnk-Bastelei.
* Linux: .desktop-Datei in ~/.config/autostart/ (XDG-Autostart, von allen gängigen
  Desktops unterstützt) -- exakt die Datei, die auch linux/install.sh anlegt.

Der systemd-Dienst (linux/rz-checkin-agent.service) ist davon unberührt: er startet den
Agenten systemweit ohne Oberfläche und wird weiterhin mit systemctl verwaltet.
"""

from __future__ import annotations

import logging
import os
import shlex
import sys
from pathlib import Path

import agent_paths

LOG = logging.getLogger("autostart")

APP_NAME = "RZ-CheckIn Agent"
# Dateiname bzw. Registry-Wertname -- muss zu linux/install.sh passen, damit beide Wege
# denselben Eintrag meinen.
ENTRY_NAME = "rz-checkin-agent"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_VALUE_NAME = "RZ-CheckIn-Agent"


class AutostartError(RuntimeError):
    """Der Eintrag konnte nicht geschrieben/entfernt werden (Rechte, Dateisystem)."""


def supported() -> bool:
    """Auf dieser Plattform umschaltbar? macOS bräuchte ein LaunchAgent-plist -- das
    wird hier (mangels Zielplattform) nicht unterstützt, die Checkbox bleibt dort aus."""
    return agent_paths.is_windows() or agent_paths.is_linux()


def command() -> str:
    """Befehl, der beim Anmelden gestartet wird.

    Als gebaute Programmdatei ist das die Datei selbst; läuft der Agent aus den Quellen,
    zusätzlich der Python-Interpreter samt Skriptpfad. Eine ausdrücklich gewählte
    Konfigurationsdatei (--config bzw. RZ_AGENT_CONFIG) wird mitgegeben, damit der
    Autostart dieselbe benutzt wie die gerade laufende Instanz."""
    if getattr(sys, "frozen", False):
        teile = [str(Path(sys.executable).resolve())]
    else:
        teile = [str(Path(sys.executable).resolve()), str(Path(__file__).resolve().parent / "tray_app.py")]
    konfiguration = os.environ.get(agent_paths.CONFIG_ENV_VAR)
    if konfiguration:
        teile += ["--config", konfiguration]
    return " ".join(_quote(teil) for teil in teile)


def _quote(text: str) -> str:
    if agent_paths.is_windows():
        return f'"{text}"' if " " in text else text
    return shlex.quote(text)


def desktop_file() -> Path:
    """Pfad der XDG-Autostart-Datei (nur Linux)."""
    basis = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(basis) / "autostart" / f"{ENTRY_NAME}.desktop"


def location() -> str:
    """Wo der Eintrag liegt -- für die Anzeige im Fenster."""
    if agent_paths.is_windows():
        return f"Registry: HKCU\\{WINDOWS_RUN_KEY}\\{WINDOWS_VALUE_NAME}"
    if agent_paths.is_linux():
        return str(desktop_file())
    return "auf dieser Plattform nicht unterstützt"


def is_enabled() -> bool:
    if agent_paths.is_windows():
        return _windows_value() is not None
    if agent_paths.is_linux():
        return desktop_file().is_file()
    return False


def enable() -> None:
    if agent_paths.is_windows():
        _windows_set(command())
    elif agent_paths.is_linux():
        _linux_write(command())
    else:
        raise AutostartError("Autostart wird auf dieser Plattform nicht unterstützt")
    LOG.info("Autostart eingeschaltet (%s)", location())


def disable() -> None:
    if agent_paths.is_windows():
        _windows_delete()
    elif agent_paths.is_linux():
        _linux_remove()
    else:
        raise AutostartError("Autostart wird auf dieser Plattform nicht unterstützt")
    LOG.info("Autostart ausgeschaltet (%s)", location())


def set_enabled(enabled: bool) -> None:
    enable() if enabled else disable()


# --------------------------------------------------------------------------------------
# Windows: HKCU-Run-Schlüssel
# --------------------------------------------------------------------------------------


def _windows_value() -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
            value, _typ = winreg.QueryValueEx(key, WINDOWS_VALUE_NAME)
            return value
    except FileNotFoundError:
        return None
    except OSError as exc:  # noqa: BLE001 - z.B. gesperrter Schlüssel per Gruppenrichtlinie
        LOG.debug("Autostart-Schlüssel nicht lesbar: %s", exc)
        return None


def _windows_set(befehl: str) -> None:
    import winreg

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, WINDOWS_VALUE_NAME, 0, winreg.REG_SZ, befehl)
    except OSError as exc:
        raise AutostartError(f"Autostart konnte nicht eingetragen werden: {exc}") from exc


def _windows_delete() -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, WINDOWS_VALUE_NAME)
    except FileNotFoundError:
        return  # war gar nicht eingetragen
    except OSError as exc:
        raise AutostartError(f"Autostart konnte nicht entfernt werden: {exc}") from exc


# --------------------------------------------------------------------------------------
# Linux: XDG-Autostart
# --------------------------------------------------------------------------------------

_DESKTOP_VORLAGE = """[Desktop Entry]
Type=Application
Name={name}
GenericName=Reader-Agent
Comment=Liest Dienstausweise am Kartenleser und meldet sie an RZ-CheckIn
Exec={befehl}
Terminal=false
Categories=Utility;System;
X-GNOME-Autostart-enabled=true
"""


def _linux_write(befehl: str) -> None:
    ziel = desktop_file()
    try:
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_text(_DESKTOP_VORLAGE.format(name=APP_NAME, befehl=befehl), encoding="utf-8")
        ziel.chmod(0o644)
    except OSError as exc:
        raise AutostartError(f"Autostart-Datei konnte nicht geschrieben werden: {exc}") from exc


def _linux_remove() -> None:
    try:
        desktop_file().unlink(missing_ok=True)
    except OSError as exc:
        raise AutostartError(f"Autostart-Datei konnte nicht entfernt werden: {exc}") from exc
