# Reader-Agent — Installation auf dem Windows-Kiosk-PC

Der Reader-Agent verbindet den USB-RFID-Leser mit dem RZ-CheckIn-Backend: gelesene
Karten-UIDs → `POST /api/checkin/rfid`, dazu ein regelmäßiger Heartbeat für die
PRTG-Überwachung. Er läuft **nicht** im Server-Container, sondern direkt auf dem
Kiosk-PC, weil er auf den lokal angeschlossenen USB-Leser zugreifen muss.

Es gibt zwei Varianten, die dieselbe Kernlogik (`reader_agent.py`) nutzen:

- **Kommandozeile** (`reader_agent.py`): ein einzelnes Python-Skript, klassisch als
  Windows-Dienst betrieben (nssm) oder für Linux/Test-Aufbauten. Siehe Abschnitt 4.
- **Systray-App** (`tray_app.py`, bzw. als fertige `RZ-CheckIn-Agent.exe`): Icon im
  Infobereich (grün = Verbindung ok, grau = Verbindungsstörung), Rechtsklick-Menü und ein
  kleines Einstellungen-Fenster zum Bearbeiten von Server-URL/Agent-ID/API-Key/Reader,
  ganz ohne Texteditor/Konsole. Gedacht für den normalen Windows-Autostart. Siehe
  Abschnitt 5.

Referenzhardware ist der **NFC-Kartenleser USB ACR122U-A9 (RFID)**.

## 1. Hardware: ACR122U-A9 unter Windows einrichten

Der ACR122U ist intern ein PN532-Chip, meldet sich aber als **PC/SC-Smartcard-Leser**.
Windows lädt dafür automatisch seinen eingebauten CCID-Treiber und der eingebaute
"Windows-Smartcard"-Dienst (`SCardSvr`) übernimmt das Gerät exklusiv — nfcpy kann dann
nicht mehr direkt per USB darauf zugreifen. Für den Kiosk-PC deshalb einmalig:

1. **Zadig** installieren (https://zadig.akeo.ie/, portable .exe, kein Installer nötig
   — für den air-gapped Zielrechner vorher auf einem Rechner mit Internetzugang laden
   und per USB-Stick übertragen).
2. ACR122U anschließen, in Zadig unter "Options → List All Devices" das Gerät
   **"ACS ACR122U PICC Interface"** auswählen.
3. Als Zieltreiber **libusbK** (empfohlen) oder **WinUSB** wählen, "Replace Driver"
   klicken. Das ersetzt NUR den Treiber für dieses eine Gerät — andere Smartcard-Leser
   im System bleiben unberührt.
4. Den Windows-Dienst "Smartcard" (`SCardSvr`) für den ACR122U-Anschluss NICHT zwingend
   deaktivieren (er greift nach dem Treiberwechsel ohnehin nicht mehr auf das Gerät zu);
   sollte es trotzdem zu Konflikten kommen, den Dienst über `services.msc` auf "Manuell"
   stellen und beenden.
5. Test ohne Backend-Verbindung, direkt im `agent`-Ordner:
   ```powershell
   python reader_agent.py --config agent.ini --simulate-uid AABBCCDD --once
   ```
   Danach mit echter Karte: `reader = usb:072f:2200` in `agent.ini` eintragen (siehe
   `agent.ini.example`) und den Agenten normal starten — beim Auflegen einer Karte
   sollte im Log `Scan erkannt: <UID>` erscheinen.

**Fehlersuche**: Meldet sich der Leser nicht (`OSError`/"Reader nicht erreichbar" im
Log), im Geräte-Manager prüfen, ob unter "libusbK-Geräte" (bzw. "USB-Geräte")
"ACS ACR122U PICC Interface" mit dem in Zadig gesetzten Treiber erscheint — taucht er
stattdessen noch unter "Smartcard-Leser" auf, hat ein anderer Prozess (z.B. ein zweiter
gestarteter Agent, oder ein Kartenleser-Tool von Drittanbietern) das Gerät blockiert;
Zadig-Schritt wiederholen bzw. den anderen Prozess beenden.

*Alternative Hardware*: nfcpy unterstützt daneben PN532-Boards, die sich als
USB-Seriell/UART melden (`reader = tty:COM3:pn532`, siehe COM-Port im Geräte-Manager) —
für die ist kein Zadig/Treiberwechsel nötig, sie sind aber nicht die hier beschaffte
Referenzhardware.

## 2. Python-Laufzeit (nur für die Kommandozeilen-Variante)

Wer die fertige `RZ-CheckIn-Agent.exe` einsetzt (Abschnitt 5), braucht diesen Schritt
**nicht** — die .exe bringt ihre eigene Python-Laufzeit mit. Für den reinen
Kommandozeilen-Agenten (`reader_agent.py`) reicht eine reguläre Python-Installation
(offizieller Installer von python.org) oder das "Embeddable Package":

```powershell
py -3 -m venv C:\rz-checkin-agent\venv
C:\rz-checkin-agent\venv\Scripts\pip install -r requirements.txt
```

Siehe Abschnitt 6 für den air-gapped Fall, in dem `pip install` hier keinen
Internetzugriff hat.

## 3. Konfiguration

1. `agent.ini.example` nach `C:\rz-checkin-agent\agent.ini` kopieren (Kommandozeile)
   bzw. beim ersten Start der `RZ-CheckIn-Agent.exe` öffnet sich automatisch das
   Einstellungen-Fenster, wenn noch keine `agent.ini` existiert.
2. Im Admin-Bereich des Backends (`/admin/agenten`) einen neuen Agenten anlegen — der
   API-Key wird dabei **einmalig** angezeigt.
3. `server_url`, `agent_id`, `api_key` und `reader` (`usb:072f:2200` für den ACR122U-A9)
   eintragen.

Test ohne Hardware (prüft Konfiguration + Verbindung zum Server):

```powershell
python reader_agent.py --config agent.ini --simulate-uid AABBCCDD --once
```

Erwartete Ausgabe: `Scan AABBCCDD: unknown_card` (oder `checkin`/`checkout`, falls die
UID bereits einem Mitarbeiter zugeordnet ist).

## 4. Kommandozeilen-Variante als Windows-Dienst einrichten (nssm)

Unter Windows gibt es kein systemd — [nssm](https://nssm.cc/) (Non-Sucking Service
Manager) übernimmt die Rolle: Autostart vor dem Login, automatischer Neustart bei
Absturz. Alternative: die Systray-Variante aus Abschnitt 5 im normalen
Benutzer-Autostart, dafür braucht es nssm nicht.

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

## 5. Systray-App: Installation als .exe

Die Systray-App (`tray_app.py`) zeigt ein Icon im Infobereich, bietet ein
Einstellungen-Fenster (Server-URL/Agent-ID/API-Key/Reader, schreibt `agent.ini`) und
läuft im Hintergrund weiter, solange Windows läuft. Fertig als `RZ-CheckIn-Agent.exe`
gebaut, braucht der Kiosk-PC **weder Python noch irgendeine Installation** — eine Datei
kopieren reicht.

### 5.1 .exe bauen (auf einem Build-Rechner, einmalig)

Der Build selbst braucht [PyInstaller](https://pyinstaller.org/), das bündelt Python +
alle Abhängigkeiten in eine einzelne Datei. Auf einem Windows-Rechner mit Internetzugang
(muss nicht der Kiosk-PC sein):

```powershell
cd agent
.\build_exe.ps1
```

Ergebnis: `agent\dist\RZ-CheckIn-Agent.exe`. Diese eine Datei auf den/die Kiosk-PC(s)
kopieren (z.B. per USB-Stick oder internem Fileshare) — dort ist danach nichts weiter zu
installieren.

### 5.2 Autostart einrichten

Einfachste Variante: eine Verknüpfung zur `.exe` in den Autostart-Ordner des
Kiosk-Benutzerkontos legen (`Win+R` → `shell:startup`). Für mehr Kontrolle
(Wiederanlauf bei Absturz, Start auch ohne Login) alternativ über die Aufgabenplanung
(`taskschd.msc`) einen Trigger "Bei Anmeldung" mit Aktion `RZ-CheckIn-Agent.exe`
anlegen.

Bei jedem Start prüft die App, ob im Arbeitsverzeichnis eine `agent.ini` existiert —
falls nicht, öffnet sich automatisch das Einstellungen-Fenster.

## 6. Air-Gapped: Wheelhouse vorbereiten

Sowohl der Kiosk-PC als auch idealerweise der Build-Rechner für die .exe haben **keinen
Internetzugang**. Damit trotzdem nichts "von außen" gezogen werden muss, gibt es den
Zwischenschritt einer **Wheelhouse** (ein lokaler Ordner mit vorab heruntergeladenen
Python-Paketen):

1. **Einmalig, auf einem beliebigen Rechner MIT Internetzugang** (z.B. ein normaler
   Büro-PC — ausdrücklich nicht der Kiosk-PC oder Server):
   ```powershell
   cd agent
   .\prepare_wheelhouse.ps1 -Ziel C:\rz-checkin-wheelhouse
   ```
   Lädt alle Pakete aus `requirements.txt` + `requirements-tray.txt` als Wheel-Dateien
   herunter (kein Installieren, nur Herunterladen).
2. Den Ordner `C:\rz-checkin-wheelhouse` per USB-Stick/internem Fileshare auf den
   air-gapped Build-Rechner übertragen.
3. Dort komplett offline bauen:
   ```powershell
   cd agent
   .\build_exe.ps1 -Wheelhouse C:\rz-checkin-wheelhouse
   ```

Die daraus entstehende `RZ-CheckIn-Agent.exe` ist danach für beliebig viele Kiosk-PCs
einsetzbar, ganz ohne weiteren Netzwerkzugriff — sie enthält alles, was sie braucht.

Für die **Kommandozeilen-Variante** (ohne .exe-Build) funktioniert derselbe Ablauf mit
purem `pip`:

```powershell
pip install --no-index --find-links C:\rz-checkin-wheelhouse -r requirements.txt
```

Die Wheelhouse muss nur einmal pro Projektversion neu vorbereitet werden (wenn sich
`requirements.txt`/`requirements-tray.txt` ändern) — nicht bei jedem Build.

## 7. Verhalten bei Verbindungsabbruch

Kann ein Scan nicht sofort an den Server übermittelt werden (Netzwerkstörung, Server
kurzzeitig nicht erreichbar), landet er in der JSONL-Datei aus `spool_path`
(Standard: `agent_spool.jsonl`) und wird mit exponentiellem Backoff (max. alle 5 Minuten)
erneut versucht — mit dem ursprünglichen Scan-Zeitpunkt, damit das Protokoll zeitlich
korrekt bleibt. Der Reader selbst versucht bei einem Aussetzer (Kabel ab, PC-Standby)
alle 5 Sekunden neu zu verbinden. Die Systray-App zeigt eine Verbindungsstörung am
grauen (statt grünen) Icon.

## 8. Alternative: Linux/systemd

Für einen Test- oder Linux-Kiosk-Aufbau reicht eine einfache systemd-Unit (nur für die
Kommandozeilen-Variante, die Systray-App ist Windows-spezifisch):

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
