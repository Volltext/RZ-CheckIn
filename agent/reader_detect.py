#!/usr/bin/env python3
"""Erkennung angeschlossener Kartenleser für das Einstellungen-Fenster.

Statt die nfcpy-Pfadsyntax (`usb:072f:2200`) von Hand eintippen zu müssen, listet das
Auswahlfeld im Einstellungen-Fenster die tatsächlich angeschlossenen Leser auf und wählt
den gefundenen vor. Frei eintragen lässt sich weiterhin alles -- die Erkennung ist eine
Hilfe, keine Einschränkung (nfcpy unterstützt mehr Geräte, als hier namentlich bekannt
sind, und exotische Aufbauten wie `udp` bleiben so möglich).

Bewusst ohne nfcpy: die Suche soll auch dann eine Liste liefern, wenn nfcpy den Leser
gerade nicht öffnen kann (fehlende Rechte, Kernel-Treiber belegt das Gerät) -- gerade
dann will man im Fenster ja sehen, dass der Leser grundsätzlich am USB hängt. Gelesen
wird deshalb nur die Geräteliste des USB-Bus (libusb, ohne das Gerät zu öffnen) bzw. die
Liste der seriellen Schnittstellen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

LOG = logging.getLogger("reader_detect")

# Von nfcpy unterstützte USB-Leser, soweit hier namentlich bekannt -- Grundlage für
# "erkannt: <Name>" im Auswahlfeld. Die Liste dient nur der Beschriftung und Vorauswahl;
# ein hier nicht aufgeführter, von nfcpy unterstützter Leser lässt sich weiterhin von
# Hand eintragen oder über "usb" (Auto-Erkennung durch nfcpy) ansprechen.
KNOWN_USB_READERS: dict[tuple[int, int], str] = {
    (0x072F, 0x2200): "ACS ACR122U-A9",
    (0x04E6, 0x5591): "SCM SCL3711",
    (0x04CC, 0x2533): "NXP PN533",
    (0x04CC, 0x0531): "NXP PN531",
    (0x054C, 0x0193): "Sony RC-S320 (PN531)",
    (0x054C, 0x02E1): "Sony RC-S330/RC-S360",
    (0x054C, 0x06C1): "Sony RC-S380/S",
    (0x054C, 0x06C3): "Sony RC-S380/P",
}

# Auswahleintrag, der das Feld zum freien Eintippen leert (siehe tray_app.AgentWindow).
CUSTOM_LABEL = "Benutzerdefiniert (Wert selbst eintragen) …"

# nfcpy-Pfad, bei dem nfcpy sich selbst einen passenden USB-Leser sucht.
AUTO_VALUE = "usb"
AUTO_LABEL = "Automatisch: erster gefundener USB-Leser"


@dataclass(frozen=True)
class ReaderOption:
    """Ein Eintrag des Auswahlfelds: `value` landet in der agent.ini, `label` steht im
    Fenster."""

    value: str
    label: str
    detected: bool = False


def usb_device_ids() -> list[tuple[int, int]]:
    """Vendor-/Product-IDs aller angeschlossenen USB-Geräte.

    Genutzt wird libusb1/usb1 -- dieselbe Bibliothek, über die auch nfcpy auf USB
    zugreift, sie ist also mit nfcpy ohnehin vorhanden. Die Geräte werden dabei nur
    aufgezählt, nicht geöffnet: das Auflisten braucht unter Linux keine besonderen
    Rechte und funktioniert selbst dann, wenn ein Kernel-Treiber das Gerät gerade
    belegt."""
    try:
        import usb1
    except Exception as exc:  # noqa: BLE001 - ohne usb1 gibt es eben keine Erkennung
        LOG.debug("USB-Erkennung nicht möglich (usb1 fehlt: %s)", exc)
        return []
    geraete: list[tuple[int, int]] = []
    try:
        with usb1.USBContext() as context:
            for device in context.getDeviceIterator(skip_on_error=True):
                try:
                    geraete.append((device.getVendorID(), device.getProductID()))
                except Exception:  # noqa: BLE001 - einzelne Geräte dürfen die Liste nicht kippen
                    continue
    except Exception as exc:  # noqa: BLE001
        LOG.debug("USB-Geräte konnten nicht aufgelistet werden: %s", exc)
        return []
    return geraete


def detected_usb_readers() -> list[ReaderOption]:
    """Angeschlossene Leser, die namentlich bekannt sind."""
    optionen = []
    for vid, pid in usb_device_ids():
        name = KNOWN_USB_READERS.get((vid, pid))
        if name is None:
            continue
        wert = f"usb:{vid:04x}:{pid:04x}"
        optionen.append(ReaderOption(wert, f"{name} — angeschlossen", detected=True))
    return optionen


def serial_port_options() -> list[ReaderOption]:
    """Serielle Schnittstellen, an denen ein PN532-Board hängen könnte.

    Welche davon wirklich ein Leser ist, lässt sich ohne Ansprechen des Geräts nicht
    sagen -- deshalb sind das Vorschläge (`tty:USB0:pn532`), keine Erkennung."""
    try:
        from serial.tools import list_ports
    except Exception as exc:  # noqa: BLE001 - pyserial fehlt
        LOG.debug("Serielle Schnittstellen nicht auflistbar (%s)", exc)
        return []
    optionen = []
    try:
        for port in list_ports.comports():
            geraet = port.device or ""
            # nfcpy erwartet den Namen ohne Pfad und ohne "tty": /dev/ttyUSB0 -> USB0,
            # unter Windows bleibt COM3 einfach COM3.
            kurz = geraet.rsplit("/", 1)[-1]
            if kurz.startswith("tty"):
                kurz = kurz[len("tty") :]
            if not kurz:
                continue
            wert = f"tty:{kurz}:pn532"
            beschreibung = (port.description or "").strip()
            zusatz = f" ({beschreibung})" if beschreibung and beschreibung != "n/a" else ""
            optionen.append(ReaderOption(wert, f"PN532 an {geraet}{zusatz}", detected=True))
    except Exception as exc:  # noqa: BLE001
        LOG.debug("Serielle Schnittstellen konnten nicht gelesen werden: %s", exc)
    return optionen


def _known_name_for_value(value: str) -> str | None:
    """Name des Lesers zu einem Pfad wie `usb:072f:2200`, falls bekannt."""
    teile = value.split(":")
    if len(teile) != 3 or teile[0] != "usb":
        return None
    try:
        return KNOWN_USB_READERS.get((int(teile[1], 16), int(teile[2], 16)))
    except ValueError:
        return None


def reader_options(current: str | None = None) -> list[ReaderOption]:
    """Alle Einträge des Auswahlfelds, in Anzeigereihenfolge:

    1. tatsächlich angeschlossene, bekannte USB-Leser,
    2. serielle Schnittstellen (PN532-Vorschläge),
    3. der aktuell konfigurierte Wert, falls er in 1./2. nicht vorkommt,
    4. nfcpys Auto-Erkennung ("usb"),
    5. die bekannten Leser, die gerade nicht angeschlossen sind,
    6. der Eintrag zum freien Eintippen.
    """
    optionen: list[ReaderOption] = []
    gesehen: set[str] = set()

    def hinzu(option: ReaderOption) -> None:
        if option.value in gesehen:
            return
        gesehen.add(option.value)
        optionen.append(option)

    for option in detected_usb_readers():
        hinzu(option)
    for option in serial_port_options():
        hinzu(option)

    if current and current not in gesehen and current != AUTO_VALUE:
        name = _known_name_for_value(current)
        # Unbekannte Werte (eigene Geräte, udp:...) stehen unverändert im Feld -- die
        # Beschriftung IST dann der Wert.
        hinzu(ReaderOption(current, f"{name} — nicht angeschlossen" if name else current))

    hinzu(ReaderOption(AUTO_VALUE, AUTO_LABEL))

    for (vid, pid), name in KNOWN_USB_READERS.items():
        wert = f"usb:{vid:04x}:{pid:04x}"
        hinzu(ReaderOption(wert, f"{name} — nicht angeschlossen"))

    optionen.append(ReaderOption("", CUSTOM_LABEL))
    return optionen


def label_for_value(optionen: list[ReaderOption], value: str) -> str:
    """Beschriftung zu einem agent.ini-Wert; unbekannte Werte werden unverändert
    angezeigt (dann steht der rohe Wert im Feld)."""
    for option in optionen:
        if option.value and option.value == value:
            return option.label
    return value


def value_for_label(optionen: list[ReaderOption], label: str) -> str:
    """Umkehrung: aus der Auswahl bzw. dem eingetippten Text den Wert für die agent.ini
    machen. Ein selbst eingetippter Text (z.B. `usb:1234:5678`) bleibt unverändert."""
    text = label.strip()
    for option in optionen:
        if option.label == text:
            return option.value
    return text
