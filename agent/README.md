# Reader-Agent — Installation auf dem Windows-Kiosk-PC

Der Reader-Agent ist ein eigenständiges Python-Skript (`reader_agent.py`). Er läuft
**nicht** im Server-Container, sondern direkt auf dem Kiosk-PC, weil er auf den lokal
angeschlossenen USB-RFID-Leser zugreifen muss. Er verbindet den PN532-Leser mit dem
RZ-CheckIn-Backend: gelesene Karten-UIDs → `POST /api/checkin/rfid`, dazu ein
regelmäßiger Heartbeat für die PRTG-Überwachung.

## 1. Hardware: PN532 als USB-Seriell-Board (empfohlen)

PN532-Boards gibt es als reines USB/HID-Gerät (braucht unter Windows den `libusb`-Treiber
über **Zadig**, weil Windows sonst den Standard-HID-Treiber lädt) oder als
**USB-zu-Seriell (UART)** über einen CP2102/FTDI-Chip (meldet sich als normaler
COM-Port). Für Windows die **UART-Variante** verwenden — nfcpy spricht sie direkt über
`tty:COM3:pn532` an, es sind nur die Standardtreiber nötig (Windows installiert sie i.d.R.
automatisch), kein Zadig/libusb-Gefrickel.

Nach dem Anschließen im Geräte-Manager unter "Anschlüsse (COM & LPT)" nachsehen, welcher
COM-Port zugewiesen wurde, und in `agent.ini` eintragen.

## 2. Python-Laufzeit

Reguläre Python-Installation (offizieller Installer von python.org reicht) oder das
"Embeddable Package", falls keine volle Installation auf dem Kiosk-PC gewünscht ist.

```powershell
py -3 -m venv C:\rz-checkin-agent\venv
C:\rz-checkin-agent\venv\Scripts\pip install -r requirements.txt
```

`requirements.txt` für den Agenten (im `agent/`-Ordner mitgeliefert) enthält `nfcpy`,
`pyserial` und `requests`.

## 3. Konfiguration

1. `agent.ini.example` nach `C:\rz-checkin-agent\agent.ini` kopieren.
2. Im Admin-Bereich des Backends (`/admin/agenten`) einen neuen Agenten anlegen — der
   API-Key wird dabei **einmalig** angezeigt.
3. `server_url`, `agent_id`, `api_key` und `reader` (COM-Port) in `agent.ini` eintragen.

Test ohne Hardware (prüft Konfiguration + Verbindung zum Server):

```powershell
python reader_agent.py --config agent.ini --simulate-uid AABBCCDD --once
```

Erwartete Ausgabe: `Scan AABBCCDD: unknown_card (None)` (oder `checkin`/`checkout`, falls
die UID bereits einem Mitarbeiter zugeordnet ist).

## 4. Als Windows-Dienst einrichten (nssm)

Unter Windows gibt es kein systemd — [nssm](https://nssm.cc/) (Non-Sucking Service
Manager) übernimmt die Rolle: Autostart vor dem Login, automatischer Neustart bei
Absturz. Empfohlen gegenüber einer einfachen Verknüpfung im Autostart-Ordner, weil nssm
den Prozess überwacht und neu startet.

```powershell
nssm install RZCheckinAgent C:\rz-checkin-agent\venv\Scripts\python.exe
nssm set RZCheckinAgent AppParameters "reader_agent.py --config agent.ini"
nssm set RZCheckinAgent AppDirectory C:\rz-checkin-agent
nssm set RZCheckinAgent AppExit Default Restart
nssm set RZCheckinAgent Start SERVICE_AUTO_START
nssm start RZCheckinAgent
```

Logs landen zusätzlich zur Konsole in der Datei aus `log_path` (Standard:
`reader_agent.log` im Arbeitsverzeichnis).

## 5. Verhalten bei Verbindungsabbruch

Kann ein Scan nicht sofort an den Server übermittelt werden (Netzwerkstörung, Server
kurzzeitig nicht erreichbar), landet er in der JSONL-Datei aus `spool_path`
(Standard: `agent_spool.jsonl`) und wird mit exponentiellem Backoff (max. alle 5 Minuten)
erneut versucht — mit dem ursprünglichen Scan-Zeitpunkt, damit das Protokoll zeitlich
korrekt bleibt. Der Reader selbst versucht bei einem Aussetzer (Kabel ab, PC-Standby)
alle 5 Sekunden neu zu verbinden.

## 6. Alternative: Linux/systemd

Für einen Test- oder Linux-Kiosk-Aufbau reicht eine einfache systemd-Unit:

```ini
[Unit]
Description=RZ-CheckIn Reader-Agent
After=network-online.target

[Service]
ExecStart=/opt/rz-checkin-agent/venv/bin/python reader_agent.py --config agent.ini
WorkingDirectory=/opt/rz-checkin-agent
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```
