# Reader-Agent — Installation auf dem Kiosk-PC (Windows & Linux)

Der Reader-Agent verbindet den USB-RFID-Leser mit dem RZ-CheckIn-Backend: gelesene
Karten-UIDs → `POST /api/checkin/rfid`, dazu ein regelmäßiger Heartbeat für die
PRTG-Überwachung. Er läuft **nicht** im Server-Container, sondern direkt auf dem
Kiosk-PC, weil er auf den lokal angeschlossenen USB-Leser zugreifen muss.

Es gibt zwei Varianten, die dieselbe Kernlogik (`reader_agent.py`) nutzen:

- **Kommandozeile** (`reader_agent.py`): ein einzelnes Python-Skript, klassisch als
  Windows-Dienst betrieben (nssm, Abschnitt 4) oder als systemd-Dienst unter Linux
  (Abschnitt 9).
- **GUI-Variante** (`tray_app.py`, als fertige Programmdatei `RZ-CheckIn-Agent.exe` bzw.
  `rz-checkin-agent`): Symbol im Infobereich/Systray (grün = Verbindung ok, grau =
  Verbindungsstörung), Kontextmenü und ein kleines Einstellungen-Fenster zum Bearbeiten
  von Server-URL/Agent-ID/API-Key/Kartenleser, ganz ohne Texteditor/Konsole. Der
  angeschlossene Kartenleser wird dabei erkannt und vorausgewählt, und der Autostart
  lässt sich per Häkchen ein- und ausschalten. Gedacht für den normalen Autostart des
  Kiosk-Benutzers. Siehe Abschnitt 5 (Windows) und 9 (Linux).

**Windows oder Linux?** Beide Plattformen sind gleichwertig unterstützt und nutzen
dieselben Quelldateien; gebaut wird nur jeweils auf der Zielplattform (PyInstaller kann
nicht über Plattformgrenzen hinweg bauen).

| | Windows | Linux |
|---|---|---|
| Fertige Programmdatei | `RZ-CheckIn-Agent.exe` (`build_exe.ps1`) | `rz-checkin-agent` (`build_linux.sh`) |
| Treiber/Zugriff auf den Leser | Zadig → libusbK (Abschnitt 1) | udev-Regel + Kernel-Modul-Blacklist (Abschnitt 9.2, macht `linux/install.sh`) |
| Autostart mit Oberfläche | Autostart-Ordner / Aufgabenplanung | `~/.config/autostart/` (macht `linux/install.sh`) |
| Dienst ohne Oberfläche | nssm (Abschnitt 4) | systemd (Abschnitt 9.5) |
| Einrichtung | Datei kopieren, starten, Einstellungen ausfüllen | `sudo ./linux/install.sh`, starten, Einstellungen ausfüllen |

Die GUI-Variante erkennt beim Start selbst, was die Umgebung hergibt: Systray-Symbol,
ersatzweise ein kleines Fenster (Desktops ohne Infobereich, z.B. GNOME/Wayland), und
ohne grafische Oberfläche läuft sie ohne Fenster einfach weiter (`--headless`, für den
systemd-Dienst). Details in Abschnitt 9.1.

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

1. `agent.ini.example` nach `C:\rz-checkin-agent\agent.ini` kopieren (Kommandozeile;
   unter Linux nach `~/.config/rz-checkin-agent/agent.ini`, siehe Abschnitt 8) bzw. beim
   ersten Start der GUI-Variante öffnet sich automatisch das Einstellungen-Fenster, wenn
   noch keine `agent.ini` existiert.
2. Im Admin-Bereich des Backends (`/admin/agenten`) einen neuen Agenten anlegen — der
   API-Key wird dabei **einmalig** angezeigt.
3. `server_url`, `agent_id`, `api_key` und `reader` (`usb:072f:2200` für den ACR122U-A9)
   eintragen.

Im Einstellungen-Fenster ist `reader` ein **Auswahlfeld**: angeschlossene Leser stehen
oben ("ACS ACR122U-A9 — angeschlossen") und sind vorausgewählt, darunter folgen nfcpys
Auto-Erkennung (`usb`), die bekannten Lesermodelle und der Eintrag "Benutzerdefiniert",
der das Feld zum freien Eintippen leert. Unter dem Feld steht jeweils der Wert, der
tatsächlich in der `agent.ini` landet. Erkannt wird über die USB-Geräteliste, ohne den
Leser zu öffnen — er taucht dort also auch dann auf, wenn ihn (noch) ein Kernel-Treiber
belegt oder die Zugriffsrechte fehlen. "Suchen" liest die Liste neu ein, praktisch nach
dem Einstecken.

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

## 5. GUI-Variante unter Windows: Installation als .exe

Die GUI-Variante (`tray_app.py`) zeigt ein Icon im Infobereich, bietet ein
Einstellungen-Fenster (Server-URL/Agent-ID/API-Key/Reader, schreibt `agent.ini`) und
läuft im Hintergrund weiter, solange Windows läuft. Fertig als `RZ-CheckIn-Agent.exe`
gebaut, braucht der Kiosk-PC **weder Python noch irgendeine Installation** — eine Datei
kopieren reicht. Das Linux-Gegenstück steht in Abschnitt 9.

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

Einfachste Variante: im Einstellungen-Fenster das Häkchen **"Beim Anmelden dieses
Benutzers automatisch starten"** setzen. Das trägt die Programmdatei unter Windows im
Registry-Schlüssel `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` ein (dasselbe,
was eine Verknüpfung im Autostart-Ordner bewirkt) und lässt sich dort genauso wieder
abwählen — Administratorrechte braucht es dafür nicht.

Von Hand geht es weiterhin: eine Verknüpfung zur `.exe` in den Autostart-Ordner des
Kiosk-Benutzerkontos legen (`Win+R` → `shell:startup`). Für mehr Kontrolle
(Wiederanlauf bei Absturz, Start auch ohne Login) alternativ über die Aufgabenplanung
(`taskschd.msc`) einen Trigger "Bei Anmeldung" mit Aktion `RZ-CheckIn-Agent.exe`
anlegen.

Bei jedem Start sucht die App ihre `agent.ini` (Suchreihenfolge siehe Abschnitt 8) —
findet sie keine, öffnet sich automatisch das Einstellungen-Fenster.

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

Unter Linux läuft derselbe Ablauf mit `prepare_wheelhouse.sh` und
`build_linux.sh --wheelhouse <ordner>` (siehe Abschnitt 9.3). Wheelhouse und Build-Rechner
müssen dabei dieselbe Distribution/Architektur und Python-Version haben.

## 7. Verhalten bei Verbindungsabbruch

Kann ein Scan nicht sofort an den Server übermittelt werden (Netzwerkstörung, Server
kurzzeitig nicht erreichbar), landet er in der JSONL-Datei aus `spool_path`
(Standard: `agent_spool.jsonl`) und wird mit exponentiellem Backoff (max. alle 5 Minuten)
erneut versucht — mit dem ursprünglichen Scan-Zeitpunkt, damit das Protokoll zeitlich
korrekt bleibt. Der Reader selbst versucht bei einem Aussetzer (Kabel ab, PC-Standby)
alle 5 Sekunden neu zu verbinden. Die GUI-Variante zeigt eine Verbindungsstörung am
grauen (statt grünen) Symbol bzw. in der Statuszeile ihres Fensters.

## 8. Wo liegen Konfiguration, Log und Offline-Puffer?

Der Agent sucht seine `agent.ini` beim Start an mehreren Stellen und nimmt die erste
gefundene (`agent/agent_paths.py`). Ein ausdrücklich angegebener Pfad gewinnt immer:

1. `--config <pfad>` auf der Kommandozeile
2. Umgebungsvariable `RZ_AGENT_CONFIG` (nutzt der systemd-Dienst)
3. `agent.ini` im **Arbeitsverzeichnis**
4. `agent.ini` neben der **Programmdatei** (.exe bzw. Binärdatei)
5. benutzerbezogen: `%APPDATA%\RZ-CheckIn-Agent\agent.ini` (Windows) bzw.
   `~/.config/rz-checkin-agent/agent.ini` (Linux)
6. nur Linux: `/etc/rz-checkin-agent/agent.ini` (systemweit, für den Dienst)

Findet er keine, legt das Einstellungen-Fenster beim Speichern eine neue an — unter
Windows neben der Programmdatei (der gewohnte "ein Ordner für alles"-Aufbau), unter
Linux unter `~/.config/rz-checkin-agent/` (dort mit Dateirechten `0600`, weil der
API-Key im Klartext darin steht).

`spool_path` und `log_path` sind im Auslieferungszustand relative Namen. Sie werden
relativ zum **Ordner der `agent.ini`** angelegt; ist der nicht beschreibbar (typisch für
`/etc/rz-checkin-agent`), weicht der Agent auf `~/.local/state/rz-checkin-agent/` aus
bzw. beim systemd-Dienst auf `/var/lib/rz-checkin-agent/` (`StateDirectory`). Absolute
Pfade in der `agent.ini` werden unverändert übernommen.

Für bestehende Windows-Installationen ändert sich damit nichts: liegt die `agent.ini`
wie bisher neben der .exe, wird genau sie gefunden und Log/Puffer landen wie gehabt
daneben.

## 9. Linux-Kiosk-PC

Unter Linux gibt es dieselbe GUI-Variante wie unter Windows — eine einzelne
Programmdatei (`rz-checkin-agent`), Symbol im Systray bzw. ein kleines Fenster, und die
Einstellungen werden dort ausgefüllt statt in einem Texteditor. Der Unterschied
gegenüber Windows liegt nicht in der App, sondern im Drumherum: Zugriffsrechte auf das
USB-Gerät, ein Kernel-Treiber, der dem Leser im Weg steht, und der Autostart. Genau das
erledigt `agent/linux/install.sh` in einem Rutsch.

### 9.1 Drei Betriebsarten, automatisch gewählt

Beim Start prüft die App die Umgebung (`tray_app.choose_ui_mode`) und entscheidet:

| Betriebsart | Wann | Was man sieht |
|---|---|---|
| **tray** | Desktop mit Infobereich: Windows, KDE, XFCE, MATE, Cinnamon, GNOME **mit** AppIndicator-Erweiterung | Symbol im Systray, Kontextmenü mit "Einstellungen …"/"Beenden" |
| **window** | Grafische Oberfläche, aber kein nutzbarer Systray (typisch GNOME/Wayland) | Kleines Fenster mit Statusampel, den vier Einstellungsfeldern und "Speichern & neu starten"/"Beenden" |
| **headless** | Keine grafische Oberfläche (SSH, systemd-Dienst) oder `--headless` | Kein Fenster, Ausgabe im Log bzw. Journal |

Erzwingen lässt sich das mit `--window` bzw. `--headless`. Es ist also immer dieselbe
Programmdatei — auf dem Kiosk-Desktop mit Oberfläche, auf einem Rechner ohne Desktop als
Dienst.

Die App prüft dabei nicht nur, ob die Tray-Bibliothek vorhanden ist, sondern auch, ob im
X-Server überhaupt ein **Systray-Manager** läuft (`_NET_SYSTEM_TRAY_S0`). Das ist der
Unterschied zwischen "Symbol wird angezeigt" und "Anwendung läuft unsichtbar im
Hintergrund": GNOME hat den klassischen Infobereich entfernt, ein dorthin gemeldetes
Symbol käme nie an. In dem Fall erscheint bewusst das Fenster.

Fehlt unter Linux das Paket für das Einstellungen-Fenster (`python3-tk`, siehe 9.3),
läuft die App trotzdem — dann eben ohne Fenster; die `agent.ini` wird in dem Fall von
Hand angelegt (`agent.ini.example` als Vorlage).

### 9.2 Hardware: ACR122U-A9 unter Linux zugänglich machen

**Muss der Treiber getauscht werden wie unter Windows? Nein.** Ein Zadig-Äquivalent gibt
es unter Linux nicht und wird auch nicht gebraucht: libusb spricht USB-Geräte direkt über
den Kernel an, ohne dass ein herstellerspezifischer Treiber installiert oder ersetzt
werden müsste. "Out of the box" heißt das aber trotzdem nicht — statt eines Treiberwechsels
sind drei andere Kleinigkeiten zu erledigen, die `linux/install.sh` in einem Aufruf
abräumt. Es geht dabei ausschließlich um Zugriffsrechte und darum, das Gerät wieder
freizugeben; installiert oder ersetzt wird nichts:

1. **Zugriffsrechte.** Ohne udev-Regel gehört das USB-Gerät `root`, der Agent bekommt
   `Permission denied`. Die mitgelieferte Regel
   (`linux/99-rz-checkin-acr122u.rules`) gibt dem angemeldeten Desktop-Benutzer Zugriff
   (`TAG+="uaccess"`) und zusätzlich der Gruppe `plugdev` (für den Dienstbetrieb ohne
   Desktop-Sitzung).
2. **Der Kernel-NFC-Treiber.** Linux bringt für den PN532/ACR122U einen eigenen Treiber
   mit (`pn533_usb`). Wird er geladen, belegt er das Gerät exklusiv und nfcpy meldet
   `Resource busy` bzw. findet es gar nicht mehr. `linux/blacklist-rz-checkin-nfc.conf`
   setzt `pn533_usb`, `pn533` und `nfc` auf die Blacklist.
3. **pcscd.** Der PC/SC-Dienst (die Linux-Entsprechung zum Windows-Smartcard-Dienst,
   siehe Abschnitt 1) greift ebenfalls nach dem ACR122U. Auf einem reinen Kiosk-PC wird
   er nicht gebraucht: `sudo systemctl disable --now pcscd.socket pcscd.service` bzw.
   `install.sh --disable-pcscd`.

Der Vergleich zu Windows: dort **ersetzt** Zadig den CCID-Treiber durch libusbK, hier
wird der störende Kernel-Treiber lediglich **nicht geladen**. Beides hat denselben Zweck —
den PC/SC-Weg aus dem Spiel nehmen, damit nfcpy direkt über libusb sprechen kann. Ob es
geklappt hat, zeigt das Einstellungen-Fenster: taucht der Leser dort als "angeschlossen"
auf, ist er am USB sichtbar; kommt trotzdem `Permission denied` oder `Resource busy` ins
Log, fehlt einer der drei Schritte (siehe 9.7).

*PN532-Board statt ACR122U*: Das meldet sich als USB-Seriell-Gerät, in der `agent.ini`
dann z.B. `reader = tty:USB0:pn532` (`/dev/ttyUSB0`). Statt der udev-Regel braucht es
dafür nur die Gruppe `dialout`: `sudo usermod -aG dialout <benutzer>`.

### 9.3 Programmdatei bauen (auf einem Build-Rechner, einmalig)

```bash
cd agent
sudo apt install python3-venv python3-tk     # Debian/Ubuntu
# bzw.: sudo dnf install python3-tkinter
./build_linux.sh                              # oder: ./build_linux.sh --wheelhouse ~/wheelhouse
```

Ergebnis: `agent/dist/rz-checkin-agent` — eine einzelne Datei mit Python und allen
Abhängigkeiten, die auf dem Kiosk-PC ohne Installation und ohne Netzwerkzugriff läuft.

Zwei Stolpersteine, die es unter Windows nicht gibt:

- **glibc-Version.** PyInstaller bündelt Python und die Python-Pakete, aber nicht die
  C-Bibliotheken des Systems. Eine auf einem neueren System gebaute Datei meldet auf
  einem älteren `version 'GLIBC_2.xx' not found`. Deshalb auf der **ältesten**
  eingesetzten Distribution bauen — die daraus entstehende Datei läuft dann auch auf
  allen neueren.
- **tkinter zur Bauzeit.** Fehlt `python3-tk` auf dem Build-Rechner, fehlt später das
  Einstellungen-Fenster in der fertigen Datei (der Agent selbst läuft trotzdem).
  `build_linux.sh` warnt in dem Fall.

Die für den USB-Zugriff nötige `libusb-1.0` legt `build_linux.sh` mit in die
Programmdatei (aus dem PyPI-Paket `libusb`, passend zur Architektur des Build-Rechners) —
auf dem Kiosk-PC muss dafür also nichts installiert werden. Nur wer den Agenten direkt
aus den Quellen betreibt (Abschnitt 9.6), braucht dort das Systempaket `libusb-1.0-0`.

Für den air-gapped Fall gibt es `./prepare_wheelhouse.sh` als Gegenstück zu
`prepare_wheelhouse.ps1` (siehe Abschnitt 6) — auf einem Rechner mit Internetzugang
ausführen, Ordner übertragen, dann `./build_linux.sh --wheelhouse <ordner>`. Wheels sind
plattform- und Python-versionsabhängig: die Wheelhouse muss auf derselben
Distribution/Architektur und Python-Version heruntergeladen werden wie auf dem
Build-Rechner.

### 9.4 Installation auf dem Kiosk-PC

Programmdatei und den Ordner `agent/linux/` auf den Kiosk-PC kopieren (USB-Stick,
internes Fileshare), dann:

```bash
sudo ./linux/install.sh --binary rz-checkin-agent
```

Das Skript

1. installiert die Programmdatei nach `/usr/local/bin/rz-checkin-agent`,
2. installiert die udev-Regel und legt bei Bedarf die Gruppe `plugdev` an,
3. blacklistet die Kernel-NFC-Module und entlädt sie sofort,
4. weist auf ein laufendes `pcscd` hin (bzw. deaktiviert es mit `--disable-pcscd`),
5. trägt den Autostart für den Kiosk-Benutzer ein
   (`~/.config/autostart/rz-checkin-agent.desktop`) und nimmt ihn in die Gruppe
   `plugdev` auf.

Danach einmal ab- und wieder anmelden (Gruppenmitgliedschaften greifen erst in einer
neuen Sitzung), den Agenten starten und im Einstellungen-Fenster Server-URL, Agent-ID,
API-Key und Kartenleser eintragen — genau wie unter Windows; der angeschlossene Leser ist
dort bereits vorausgewählt. Ab der nächsten Anmeldung startet er automatisch mit.

Den Autostart-Eintrag schreibt `install.sh` nach
`~/.config/autostart/rz-checkin-agent.desktop`. **Dieselbe** Datei legt auch das Häkchen
"Beim Anmelden dieses Benutzers automatisch starten" im Einstellungen-Fenster an bzw.
entfernt sie wieder — beide Wege meinen also denselben Eintrag, und ein per `install.sh`
eingerichteter Autostart zeigt sich im Fenster als gesetztes Häkchen. Wer den Agenten
ohne `install.sh` betreibt (etwa aus den Quellen), kann den Autostart damit komplett im
Fenster ein- und ausschalten; udev-Regel und Modul-Blacklist bleiben davon unberührt und
sind weiterhin einmalig einzurichten.

Rückgängig machen: `sudo ./linux/install.sh --uninstall` (Konfiguration und Log bleiben
erhalten).

### 9.5 Ohne Oberfläche: als systemd-Dienst

Soll der Agent unabhängig von einer angemeldeten Desktop-Sitzung laufen (Start vor dem
Login, automatischer Neustart nach Absturz — die Rolle, die unter Windows nssm
übernimmt):

```bash
sudo ./linux/install.sh --binary rz-checkin-agent --service
sudo nano /etc/rz-checkin-agent/agent.ini      # server_url, agent_id, api_key, reader
sudo systemctl start rz-checkin-agent
journalctl -u rz-checkin-agent -f
```

Der Dienst (`linux/rz-checkin-agent.service`) läuft unter einem eigenen, unprivilegierten
Konto `rz-checkin` in der Gruppe `plugdev`, liest `/etc/rz-checkin-agent/agent.ini` und
legt Log und Offline-Puffer unter `/var/lib/rz-checkin-agent/` ab.

**Nicht beides gleichzeitig**: Autostart-Eintrag *und* Dienst würden sich um denselben
Kartenleser streiten. Entweder — oder.

Wer lieber die Kommandozeilen-Variante aus einem Python-Venv betreibt (ohne gebaute
Programmdatei), ersetzt in der Unit lediglich die `ExecStart`-Zeile:

```ini
ExecStart=/opt/rz-checkin-agent/venv/bin/python /opt/rz-checkin-agent/reader_agent.py --config /etc/rz-checkin-agent/agent.ini
```

### 9.6 Direkt aus den Quellen starten (Test/Entwicklung)

```bash
sudo apt install python3-venv python3-tk libusb-1.0-0
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-tray.txt
python tray_app.py                     # GUI (Systray bzw. Fenster)
python tray_app.py --headless          # ohne Oberfläche
python reader_agent.py --simulate-uid AABBCCDD --once   # ohne Hardware
```

### 9.7 Fehlersuche unter Linux

| Symptom im Log | Ursache / Abhilfe |
|---|---|
| `Permission denied` beim Öffnen des Readers | udev-Regel fehlt, oder der Benutzer ist noch nicht in `plugdev` bzw. war seit dem `usermod` nicht neu angemeldet |
| `Resource busy` / Gerät wird gar nicht gefunden | Kernel-Modul `pn533_usb` geladen (`lsmod \| grep pn533`) oder `pcscd` läuft — siehe 9.2 |
| Kein Systray-Symbol, stattdessen ein Fenster | Der Desktop hat keinen Infobereich (GNOME/Wayland). Entweder so lassen oder die AppIndicator-Erweiterung installieren; erzwingen lässt sich das Fenster mit `--window` |
| Weder Symbol noch Fenster | `python3-tk` fehlt (aus den Quellen) bzw. es wurde ohne tkinter gebaut (siehe 9.3); der Agent läuft dann headless weiter |
| `version 'GLIBC_2.xx' not found` | Die Programmdatei wurde auf einer neueren Distribution gebaut als der Kiosk-PC — auf der älteren neu bauen (siehe 9.3) |
| `cannot find a suitable libusb-1.0` | Nur beim Betrieb aus den Quellen: `sudo apt install libusb-1.0-0`. Die gebaute Programmdatei bringt die Bibliothek selbst mit (siehe 9.3) |
| Reader hängt nach USB-Aussetzer | Wie unter Windows: `reset_after_failures` löst einen USB-Reset aus (unter Linux über libusb, ohne Zusatzsoftware). Als `reset_command` bietet sich das De-/Reautorisieren des Ports an — braucht `root`, also nur im Dienstbetrieb sinnvoll: `reset_command = /bin/sh -c 'echo 0 > /sys/bus/usb/devices/1-2/authorized; sleep 2; echo 1 > /sys/bus/usb/devices/1-2/authorized'` (Pfad des Geräts ermitteln mit `grep -l 072f /sys/bus/usb/devices/*/idVendor`) |

Das Log liegt bei der GUI-Variante unter `~/.local/state/rz-checkin-agent/reader_agent.log`
(bzw. neben der `agent.ini`, siehe Abschnitt 8), beim Dienst zusätzlich im Journal:
`journalctl -u rz-checkin-agent`.
