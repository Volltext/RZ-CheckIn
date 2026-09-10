#!/usr/bin/env bash
#
# Richtet den RZ-CheckIn Reader-Agenten auf einem Linux-Kiosk-PC ein -- das Gegenstück
# zum "exe in den Autostart legen" unter Windows, nur eben mit den drei Kleinigkeiten,
# die Linux zusätzlich braucht: Zugriffsrechte auf den Kartenleser (udev), den störenden
# Kernel-NFC-Treiber loswerden (modprobe-Blacklist) und den Autostart einhängen.
#
#   sudo ./install.sh                       # Desktop-Autostart (Systray/Fenster)
#   sudo ./install.sh --service             # stattdessen systemd-Dienst ohne Oberfläche
#   sudo ./install.sh --uninstall           # alles wieder entfernen (Konfig bleibt)
#
# Ohne --binary wird ../dist/rz-checkin-agent erwartet (Ergebnis von build_linux.sh).
# Das Skript startet sich bei Bedarf selbst per sudo neu.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BINARY="$SCRIPT_DIR/../dist/rz-checkin-agent"
PREFIX="/usr/local/bin"
TARGET_USER=""
MODE="autostart"          # autostart | service | none
DISABLE_PCSCD="nein"
UNINSTALL="nein"

usage() {
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'HELP'

Optionen:
  --binary PFAD      Zu installierende Programmdatei (Standard: ../dist/rz-checkin-agent)
  --prefix ORDNER    Zielordner der Programmdatei (Standard: /usr/local/bin)
  --user NAME        Benutzerkonto für Autostart/Gruppenrechte (Standard: der sudo-Aufrufer)
  --service          systemd-Dienst ohne Oberfläche statt Desktop-Autostart einrichten
  --no-autostart     Nur Programm + Gerätezugriff einrichten, keinen Start einhängen
  --disable-pcscd    Den PC/SC-Dienst (pcscd) stoppen und dauerhaft deaktivieren
  --uninstall        Installation wieder entfernen
  -h, --help         Diese Hilfe
HELP
}

while [ $# -gt 0 ]; do
    case "$1" in
        --binary) BINARY="${2:?--binary braucht einen Pfad}"; shift 2 ;;
        --prefix) PREFIX="${2:?--prefix braucht einen Pfad}"; shift 2 ;;
        --user) TARGET_USER="${2:?--user braucht einen Namen}"; shift 2 ;;
        --service) MODE="service"; shift ;;
        --no-autostart) MODE="none"; shift ;;
        --disable-pcscd) DISABLE_PCSCD="ja"; shift ;;
        --uninstall) UNINSTALL="ja"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unbekannte Option: $1" >&2; usage >&2; exit 64 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Benötigt Administratorrechte -- starte neu über sudo ..."
    exec sudo -- "$0" "$@"
fi

# Beim Aufruf über sudo ist der eigentliche Kiosk-Benutzer $SUDO_USER, nicht root.
if [ -z "$TARGET_USER" ]; then
    TARGET_USER="${SUDO_USER:-}"
fi

DIENST_BENUTZER="rz-checkin"
ZIEL_BINARY="$PREFIX/rz-checkin-agent"
UDEV_ZIEL="/etc/udev/rules.d/99-rz-checkin-acr122u.rules"
MODPROBE_ZIEL="/etc/modprobe.d/blacklist-rz-checkin-nfc.conf"
SERVICE_ZIEL="/etc/systemd/system/rz-checkin-agent.service"
SYSTEM_CONFIG_DIR="/etc/rz-checkin-agent"

autostart_datei() {
    # ~/.config/autostart/ des Zielbenutzers
    local home
    home="$(getent passwd "$1" | cut -d: -f6)"
    [ -n "$home" ] || return 1
    printf '%s/.config/autostart/rz-checkin-agent.desktop\n' "$home"
}

# --------------------------------------------------------------------------------------
# Deinstallation
# --------------------------------------------------------------------------------------
if [ "$UNINSTALL" = "ja" ]; then
    if systemctl list-unit-files rz-checkin-agent.service >/dev/null 2>&1; then
        systemctl disable --now rz-checkin-agent.service 2>/dev/null || true
    fi
    rm -f "$SERVICE_ZIEL" "$UDEV_ZIEL" "$MODPROBE_ZIEL" "$ZIEL_BINARY"
    systemctl daemon-reload 2>/dev/null || true
    udevadm control --reload-rules 2>/dev/null || true
    if [ -n "$TARGET_USER" ] && ziel="$(autostart_datei "$TARGET_USER")"; then
        rm -f "$ziel"
    fi
    echo "Entfernt. Konfiguration und Log wurden bewusst NICHT gelöscht:"
    echo "  ~/.config/rz-checkin-agent/  bzw.  $SYSTEM_CONFIG_DIR/"
    exit 0
fi

# --------------------------------------------------------------------------------------
# 1. Programmdatei installieren
# --------------------------------------------------------------------------------------
if [ ! -f "$BINARY" ]; then
    echo "Programmdatei nicht gefunden: $BINARY" >&2
    echo "Zuerst auf einem Build-Rechner bauen (agent/build_linux.sh) oder --binary angeben." >&2
    exit 66
fi
install -D -m 0755 "$BINARY" "$ZIEL_BINARY"
echo "[1/5] Programmdatei installiert: $ZIEL_BINARY"

# --------------------------------------------------------------------------------------
# 2. Zugriff auf den Kartenleser (udev + Gruppe)
# --------------------------------------------------------------------------------------
install -D -m 0644 "$SCRIPT_DIR/99-rz-checkin-acr122u.rules" "$UDEV_ZIEL"
getent group plugdev >/dev/null || groupadd plugdev
udevadm control --reload-rules >/dev/null 2>&1 || true
udevadm trigger >/dev/null 2>&1 || true
echo "[2/5] udev-Regel installiert: $UDEV_ZIEL"

# --------------------------------------------------------------------------------------
# 3. Kernel-NFC-Treiber aus dem Weg räumen
# --------------------------------------------------------------------------------------
install -D -m 0644 "$SCRIPT_DIR/blacklist-rz-checkin-nfc.conf" "$MODPROBE_ZIEL"
modprobe -r pn533_usb pn533 nfc 2>/dev/null || true
echo "[3/5] Kernel-Module pn533_usb/pn533/nfc geblacklistet und entladen"

# --------------------------------------------------------------------------------------
# 4. pcscd -- belegt den ACR122U ebenfalls exklusiv
# --------------------------------------------------------------------------------------
if [ "$DISABLE_PCSCD" = "ja" ]; then
    systemctl disable --now pcscd.socket pcscd.service 2>/dev/null || true
    echo "[4/5] pcscd gestoppt und deaktiviert"
elif systemctl is-active --quiet pcscd.service 2>/dev/null || systemctl is-active --quiet pcscd.socket 2>/dev/null; then
    echo "[4/5] ACHTUNG: pcscd läuft und belegt den Kartenleser -- der Agent bekommt ihn dann"
    echo "      nicht auf. Erneut mit --disable-pcscd aufrufen oder von Hand:"
    echo "      sudo systemctl disable --now pcscd.socket pcscd.service"
else
    echo "[4/5] pcscd läuft nicht -- nichts zu tun"
fi

# --------------------------------------------------------------------------------------
# 5. Autostart bzw. Dienst
# --------------------------------------------------------------------------------------
case "$MODE" in
    autostart)
        if [ -z "$TARGET_USER" ]; then
            echo "[5/5] Kein Benutzer ermittelbar -- Autostart übersprungen." >&2
            echo "      Erneut mit --user <benutzer> aufrufen." >&2
            exit 65
        fi
        usermod -aG plugdev "$TARGET_USER"
        ziel="$(autostart_datei "$TARGET_USER")"
        autostart_dir="$(dirname "$ziel")"
        # Ordner anlegen und dem Benutzer übereignen -- unter sudo angelegt würden
        # ~/.config bzw. ~/.config/autostart sonst root gehören, und der Desktop des
        # Kiosk-Benutzers könnte anschließend nichts mehr darin ändern.
        mkdir -p "$autostart_dir"
        chown "$TARGET_USER:" "$(dirname "$autostart_dir")" "$autostart_dir"
        install -m 0644 -o "$TARGET_USER" -g "$(id -gn "$TARGET_USER")" \
            "$SCRIPT_DIR/rz-checkin-agent.desktop" "$ziel"
        # Exec-Zeile auf den tatsächlichen Installationsort anpassen (--prefix).
        sed -i "s|^Exec=.*|Exec=$ZIEL_BINARY|" "$ziel"
        echo "[5/5] Autostart für Benutzer '$TARGET_USER' eingerichtet: $ziel"
        ;;
    service)
        id -u "$DIENST_BENUTZER" >/dev/null 2>&1 || \
            useradd --system --no-create-home --shell /usr/sbin/nologin "$DIENST_BENUTZER"
        usermod -aG plugdev "$DIENST_BENUTZER"
        install -D -m 0644 "$SCRIPT_DIR/rz-checkin-agent.service" "$SERVICE_ZIEL"
        if [ "$PREFIX" != "/usr/local/bin" ]; then
            sed -i "s|^ExecStart=.*|ExecStart=$ZIEL_BINARY --headless|" "$SERVICE_ZIEL"
        fi
        mkdir -p "$SYSTEM_CONFIG_DIR"
        if [ ! -f "$SYSTEM_CONFIG_DIR/agent.ini" ] && [ -f "$SCRIPT_DIR/../agent.ini.example" ]; then
            install -m 0640 -o root -g "$DIENST_BENUTZER" \
                "$SCRIPT_DIR/../agent.ini.example" "$SYSTEM_CONFIG_DIR/agent.ini"
            echo "      Vorlage abgelegt: $SYSTEM_CONFIG_DIR/agent.ini -- bitte ausfüllen."
        fi
        systemctl daemon-reload
        systemctl enable rz-checkin-agent.service
        echo "[5/5] Dienst eingerichtet. Nach dem Ausfüllen der agent.ini starten mit:"
        echo "      sudo systemctl start rz-checkin-agent"
        ;;
    none)
        echo "[5/5] Autostart übersprungen (--no-autostart)"
        ;;
esac

echo
echo "Fertig."
case "$MODE" in
    autostart)
        cat <<HINWEIS
Nächste Schritte:
  1. Im Admin-Bereich unter "Agenten" einen Agenten anlegen (API-Key wird einmalig
     angezeigt).
  2. Einmal ab- und wieder anmelden (die neue Gruppenmitgliedschaft 'plugdev' gilt erst
     für neue Sitzungen), oder den Rechner neu starten.
  3. "$ZIEL_BINARY" starten -- beim ersten Start erscheinen die Einstellungen.
     Danach startet der Agent bei jeder Anmeldung automatisch.
HINWEIS
        ;;
    service)
        cat <<HINWEIS
Nächste Schritte:
  1. Im Admin-Bereich unter "Agenten" einen Agenten anlegen (API-Key wird einmalig
     angezeigt).
  2. $SYSTEM_CONFIG_DIR/agent.ini ausfüllen (server_url, agent_id, api_key, reader).
  3. sudo systemctl start rz-checkin-agent
     Logs: journalctl -u rz-checkin-agent -f
HINWEIS
        ;;
esac
