#!/usr/bin/env bash
#
# Baut den Reader-Agenten (GUI-Variante) zu EINER ausführbaren Datei für Linux --
# das Gegenstück zu build_exe.ps1 unter Windows.
#
# Läuft auf dem BUILD-Rechner (braucht einmalig Internetzugang bzw. eine vorbereitete
# Wheelhouse, siehe prepare_wheelhouse.sh), NICHT auf dem air-gapped Kiosk-PC. Ergebnis
# ist dist/rz-checkin-agent -- eine einzelne Datei, die auf dem Kiosk-PC ohne
# Python-Installation und ohne Netzwerkzugriff läuft.
#
# Verwendung:
#   ./build_linux.sh                          # Pakete von PyPI (Build-Rechner online)
#   ./build_linux.sh --wheelhouse ~/wheelhouse  # komplett offline aus der Wheelhouse
#
# WICHTIG: PyInstaller bündelt zwar Python und alle Python-Pakete, NICHT aber die
# C-Bibliotheken des Systems (glibc & Co.). Deshalb auf der ÄLTESTEN Distribution bauen,
# die im Einsatz ist -- eine auf Debian 12 gebaute Datei läuft auf Ubuntu 24.04, umgekehrt
# nicht. Auf dem Build-Rechner werden gebraucht: python3 (>= 3.11), python3-venv und
# python3-tk (Debian/Ubuntu) bzw. python3-tkinter (RHEL/Fedora) -- ohne tkinter zur
# Bauzeit fehlt später das Einstellungen-Fenster in der fertigen Datei.

set -euo pipefail
cd "$(dirname "$0")"

WHEELHOUSE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --wheelhouse)
            WHEELHOUSE="${2:?--wheelhouse braucht einen Pfad}"
            shift 2
            ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unbekannte Option: $1" >&2
            exit 64
            ;;
    esac
done

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c 'import tkinter' 2>/dev/null; then
    echo "WARNUNG: tkinter fehlt auf diesem Build-Rechner -- die fertige Datei hätte kein" >&2
    echo "         Einstellungen-Fenster. Nachinstallieren: apt install python3-tk" >&2
    echo "         (Debian/Ubuntu) bzw. dnf install python3-tkinter (RHEL/Fedora)." >&2
fi

VENV_DIR="build-venv"
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi
PIP="$VENV_DIR/bin/pip"
PYINSTALLER="$VENV_DIR/bin/pyinstaller"

PIP_ARGS=(install)
if [ -n "$WHEELHOUSE" ]; then
    echo "Installiere ausschließlich aus Wheelhouse: $WHEELHOUSE (kein Netzwerkzugriff)"
    PIP_ARGS+=(--no-index --find-links "$WHEELHOUSE")
else
    echo "Installiere von PyPI (Build-Rechner hat Internetzugang)"
fi
PIP_ARGS+=(-r requirements.txt -r requirements-tray.txt)
"$PIP" "${PIP_ARGS[@]}"

# Pfad der von nfcpy genutzten libusb-Bibliothek aus dem PyPI-Paket `libusb` ermitteln
# (passend zur Architektur des Build-Rechners, siehe reader_agent.py::_pyusb_backend).
LIBUSB_SO="$("$VENV_DIR/bin/python" -c 'import libusb; print(libusb.dll._name)' 2>/dev/null || true)"

echo "Baue rz-checkin-agent ..."
# --collect-submodules nfc: nfcpy lädt seine Reader-/Tag-Treiber (nfc.clf.acr122,
#   nfc.clf.pn532, nfc.tag.tt2 usw.) je nach erkanntem Gerät bzw. Kartentyp per
#   importlib mit einem zur Laufzeit gebauten Modulnamen nach -- PyInstallers statische
#   Analyse sieht solche Strings nicht und würde die Module weglassen.
# --collect-submodules pystray: pystray sucht sich sein Backend (appindicator/gtk/xorg)
#   erst beim Import passend zur Desktop-Umgebung aus, ebenfalls dynamisch.
# --collect-data libusb: das libusb-Paket liefert die für den automatischen USB-Reset
#   genutzte Bibliothek als reine Binärdatei mit, ohne Python-Code.
# Kein --windowed: unter Linux ist das ein reines macOS-Merkmal (App-Bundle); die
#   Ausgabe auf stdout wird hier sogar gebraucht (--headless im systemd-Dienst).
PYINSTALLER_ARGS=(
    --noconfirm
    --onefile
    --name "rz-checkin-agent"
    --paths .
    --collect-submodules nfc
    --collect-submodules pystray
    --collect-data libusb
)
if [ -n "$LIBUSB_SO" ] && [ -f "$LIBUSB_SO" ]; then
    # Unter Windows bringen die Wheels die libusb-DLL mit; unter Linux erwartet usb1
    # (die Bibliothek, über die nfcpy auf USB zugreift) eine libusb-1.0.so entweder im
    # System oder direkt in seinem eigenen Paketordner. Ohne sie startet der Agent zwar,
    # findet aber keinen Leser ("cannot find a suitable libusb-1.0"). Die passende
    # Bibliothek aus dem PyPI-Paket `libusb` deshalb genau dorthin legen -- damit läuft
    # die fertige Datei auch auf einem System ohne installiertes libusb-Paket.
    PYINSTALLER_ARGS+=(--add-binary "$LIBUSB_SO:usb1")
    echo "libusb wird mitgeliefert: $LIBUSB_SO"
else
    echo "WARNUNG: Keine libusb-Bibliothek zum Mitliefern gefunden -- auf dem Kiosk-PC" >&2
    echo "         muss dann das Systempaket libusb-1.0-0 installiert sein." >&2
fi

"$PYINSTALLER" "${PYINSTALLER_ARGS[@]}" tray_app.py

echo
echo "Fertig: dist/rz-checkin-agent"
echo "Auf den Kiosk-PC kopieren und dort einrichten:"
echo "  ./linux/install.sh --binary dist/rz-checkin-agent"
echo "Die Datei enthält Python und alle Abhängigkeiten -- auf dem Kiosk-PC ist weder eine"
echo "Python-Installation noch Netzwerkzugriff nötig."
