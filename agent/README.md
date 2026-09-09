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

### Wie der Agent den Reader anspricht (Dauerbetrieb)

Für den Kiosk-Betrieb sind zwei Details in `run_reader_loop` wichtig -- beide sind der
Grund dafür, dass der Leser nach einem Scan sofort für den nächsten Ausweis bereit ist:

- **Der Reader wird einmal geöffnet und bleibt offen.** Pro Karte wird nur neu gesucht,
  nicht neu verbunden. nfcpys `clf.connect()` kehrt nach *jeder* erkannten Karte zurück
  -- wer die Verbindung drumherum in der Schleife auf- und wieder abbaut, schließt den
  ACR122U also nach jedem einzelnen Scan. Passiert das mitten in einer laufenden
  Kartensitzung, bleiben Antwortdaten im USB-Endpunkt liegen; das nächste Öffnen liest
  sie als vermeintliche Versionsantwort des Readers und scheitert
  (`failed to retrieve ACR122U version string` → `[Errno 19] No such device`) -- der
  Leser ist dann bis zum physischen Aus-/Einstecken tot.
- **Es wird nur bis zur Kartenerkennung gegangen** (`clf.sense`), die Karte selbst wird
  nicht protokollseitig aktiviert. Für den Check-in wird ausschließlich die UID
  gebraucht, und die steht bereits in der Antwort auf das Suchkommando -- byte-identisch
  mit dem, was nfcpy nach der Aktivierung als `Tag.identifier` liefern würde. Die
  Aktivierung ist bei DESFire-Karten der fehleranfälligste Teil (daher die Warnung
  `does not support fsd 256` im Log) und entfällt damit komplett, ebenso die
  anschließende Anwesenheitsprüfung, die die Karte im Sekundentakt weiter anspricht.

Einzelne Lesefehler (typischerweise: die Karte wird mitten im Lesevorgang weggezogen)
werden übergangen; erst mehrere Fehler in Folge oder ein wirklich verschwundenes Gerät
führen zum Neuaufbau der Verbindung. Beim Schließen gibt der Agent den USB-Handle immer
selbst frei, weil nfcpy dabei noch ein Kommando an den Reader schickt und den Handle
offen lässt, wenn dieses Kommando fehlschlägt.

Der Piepton beim Scannen kommt ebenfalls vom Agenten (`beep_on_scan`, siehe
`agent.ini.example`) und nicht von nfcpys `beep-on-connect`: nfcpy räumt dem ACR122U
dort nur 400 ms Antwortzeit für einen 300 ms langen Piepton ein -- wird das knappe
Zeitfenster überschritten, kommt die Antwort trotzdem, bleibt im Endpunkt liegen und
bringt die Verbindung aus dem Tritt.

### Automatischer Reader-Reset bei hängendem Gerät

Manche ACR122U-Exemplare hängen sich nach einem USB-Aussetzer (z.B. USB-Selective-
Suspend, ein wackliges Kabel, ein zu langer/über einen Hub geführter Anschluss)
dauerhaft auf: der Agent versucht zwar alle 5s neu zu verbinden, aber jeder Versuch
scheitert weiterhin (`failed to retrieve ACR122U version string` / `insufficient data
for decoding chip response`) -- nur physisches Aus-/Einstecken hilft. Damit das nicht
jedes Mal ein manuelles Eingreifen am Kiosk-PC braucht, versucht der Agent von sich aus
gegenzusteuern (siehe `reset_after_failures`/`reset_command` in `agent.ini.example`):

1. Nach `reset_after_failures` Fehlversuchen IN FOLGE (Standard 3, `0` deaktiviert)
   löst der Agent selbst einen **USB-Reset** aus (`agent/reader_agent.py::
   _try_usb_reset`) -- softwareseitig dieselbe Art Reset, die auch beim Aus-/Einstecken
   passiert, ohne dass jemand am Gerät sein muss. Reicht in vielen Fällen bereits aus
   und räumt nebenbei auch Datenreste in den USB-Endpunkten weg.

   Der Reset läuft über **libusb1/usb1** -- also über genau die Bibliothek, die auch
   nfcpy selbst für den USB-Zugriff nutzt (`nfc/clf/transport.py`:
   `import usb1 as libusb`). Sie ist mit nfcpy ohnehin installiert und spricht das
   Gerät über denselben Treiber an, unter dem der Reader schon läuft -- es gibt also
   nichts zusätzlich einzurichten.

   Klappt das auf einem Gerät nicht, versucht der Agent es zusätzlich über **pyusb**.
   Das ist eine andere Bibliothek mit eigener DLL-Suche: sie braucht unter Windows eine
   `libusb-1.0.dll`, die Zadig NICHT automatisch irgendwo im PATH installiert (Zadig
   ersetzt nur den Kernel-Treiber) -- ohne sie meldet pyusb `No backend available`.
   Deshalb steht in `requirements.txt` zusätzlich das PyPI-Paket
   [`libusb`](https://pypi.org/project/libusb/), das die passende, vorkompilierte
   Bibliothek für jede Zielplattform (inkl. Windows x86/x64/arm64) gleich mitbringt --
   ein normales `pip install -r requirements.txt` reicht, keine manuelle DLL-Suche
   nötig. Der .exe-Build (`build_exe.ps1`) nimmt die Bibliothek über
   `--collect-data libusb` mit auf.
2. Hilft das nicht, kann zusätzlich `reset_command` gesetzt werden: ein beliebiger
   Shell-Befehl, der NACH dem erfolglosen USB-Reset läuft. Unter Windows bietet sich ein
   Deaktivieren+Aktivieren des Geräts im Geräte-Manager an, z.B. per
   [devcon](https://learn.microsoft.com/windows-hardware/drivers/devtest/devcon)
   (Teil des Windows Driver Kit, portabel kopierbar):
   ```ini
   reset_command = C:\rz-checkin-agent\devcon.exe restart "USB\VID_072F&PID_2200*"
   ```
   Alternative ohne WDK-Download: das seit Windows 10 eingebaute `pnputil`, das
   allerdings die genaue Geräte-Instanz-ID statt eines Hardware-ID-Musters braucht
   (einmalig im Geräte-Manager unter "Details" → "Geräteinstanzpfad" ablesen, oder per
   `pnputil /enum-devices /class USB`):
   ```ini
   reset_command = powershell -Command "pnputil /disable-device '<Geräteinstanzpfad>'; Start-Sleep 2; pnputil /enable-device '<Geräteinstanzpfad>'"
   ```
   Der Befehl läuft mit den Rechten des Agent-Prozesses (bei nssm i.d.R. `LocalSystem`,
   das reicht für devcon/pnputil ohne weitere Berechtigungen aus).

Beide Mechanismen sind bewusst best-effort: schlägt der Reset fehl, läuft die normale
5s-Retry-Schleife einfach weiter, der Agent stürzt dabei nie ab. Ist auch der
Hard-Reset-Befehl nicht genug, bleibt weiterhin nur das physische Aus-/Einstecken --
das deutet dann eher auf ein Kabel-/Port-/Energieverwaltungsproblem hin (siehe unten).

**Zusätzlich empfohlen** (unabhängig vom automatischen Reset, siehe README-Hauptteil
"Kiosk-PC"-Abschnitt): USB-Energieverwaltung für den Anschluss des Readers deaktivieren
(Geräte-Manager → USB-Root-Hub bzw. "ACS ACR122U PICC Interface" → Eigenschaften →
Energieverwaltung → Haken bei "Computer kann das Gerät ausschalten..." entfernen) sowie
einen direkten USB-2-Port am Mainboard statt Hub/USB-3-Port verwenden -- das beugt dem
Aufhängen von vornherein vor, statt es nur nachträglich zu beheben.

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

Erwartete Ausgabe: `Scan AABBCCDD: checkin` -- es gibt kein Mitarbeiter-Register, jede
UID togglet direkt Checkin/Checkout.

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

**Fehlersuche**: Meldet die .exe auf dem Kiosk-PC `ModuleNotFoundError: No module named
'nfc.clf.<treiber>'` (z.B. `nfc.clf.acr122`) oder `nfc.tag.<typ>`, wurde mit einer älteren
Version von `build_exe.ps1` ohne `--collect-submodules nfc` gebaut — nfcpy lädt seine
Reader-/Tag-Treiber erst zur Laufzeit passend zum erkannten Gerät bzw. Kartentyp nach, das
sieht PyInstaller beim Bauen nicht. Mit aktuellem `build_exe.ps1` neu bauen.

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
