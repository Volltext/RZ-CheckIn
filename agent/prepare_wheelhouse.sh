#!/usr/bin/env bash
#
# Lädt alle Python-Pakete, die für Reader-Agent + Linux-Build gebraucht werden, als
# Wheel-Dateien in einen lokalen Ordner ("Wheelhouse") -- das Gegenstück zu
# prepare_wheelhouse.ps1 unter Windows.
#
# NUR auf einem Rechner MIT Internetzugang ausführen (z.B. ein normaler Büro-PC,
# ausdrücklich NICHT der air-gapped Kiosk-PC oder Server). Den erzeugten Ordner
# anschließend per USB-Stick/internem Fileshare auf den Rechner übertragen, der
# tatsächlich baut (siehe build_linux.sh --wheelhouse).
#
# WICHTIG: Wheels sind plattform- und Python-versionsabhängig. Die Wheelhouse muss also
# auf einem Rechner MIT DERSELBEN Distribution/Architektur und derselben Python-Version
# heruntergeladen werden wie der spätere Build-Rechner -- sonst findet pip dort nur
# unpassende Dateien.
#
# Verwendung:
#   ./prepare_wheelhouse.sh [Zielordner]      # Standard: ./wheelhouse

set -euo pipefail
cd "$(dirname "$0")"

ZIEL="${1:-wheelhouse}"
PYTHON="${PYTHON:-python3}"

"$PYTHON" -m pip download -d "$ZIEL" -r requirements.txt
"$PYTHON" -m pip download -d "$ZIEL" -r requirements-tray.txt

echo
echo "Fertig: $ZIEL enthält alle benötigten Wheel-Dateien."
echo "Ordner auf den Build-/Zielrechner übertragen, dort z.B.:"
echo "  pip install --no-index --find-links $ZIEL -r requirements.txt"
echo "  ./build_linux.sh --wheelhouse $ZIEL"
