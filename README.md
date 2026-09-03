# RZ-CheckIn

Eigenständiges, vom Zutrittsdienstleister unabhängiges System zum Protokollieren, wer
sich im Rechenzentrum befindet. Mitarbeiter checken automatisch per Dienstausweis
(RFID/NFC) ein und aus, externe Techniker manuell über eine Kiosk-Oberfläche mit
wiederverwendbarem Besucherprofil.

**Datensparsamkeit bei internen Mitarbeitern**: Für Mitarbeiter wird bewusst
ausschließlich die Dienstausweisnummer gespeichert — es gibt keinen Namen und keine
sonstige Verknüpfung zu einer Person im System. Die Live-Übersicht zeigt für Mitarbeiter
deshalb nur die Anzahl der aktuell Anwesenden (grüner Punkt + Zähler), keine Namen oder
Kartennummern. Externe Besucher werden weiterhin mit Namen geführt (siehe Kiosk-Ansicht
unten). Vergisst jemand das Auschecken, checkt das System automatisch nach einer im
Admin-Bereich einstellbaren Anzahl Stunden aus (`/admin/einstellungen`).

**Das System steuert keine Türen** — es ist ein reines Logging-System parallel zum
bestehenden (dienstleistergesteuerten) Zutrittssystem.

Das vollständige Konzept inkl. aller Entscheidungen steht im ursprünglichen
Anforderungsdokument; dieses README beschreibt den Code und den Betrieb. Wer nur schnell
einen Server aufsetzen möchte, ohne den ganzen Rest zu lesen: direkt weiter zu
["Schnellstart"](#schnellstart-server-in-10-minuten-aufsetzen).

## Architektur

```
Server (VM, intern)                      Kiosk-PC (Eingang RZ)
┌─────────────────────────┐    HTTPS    ┌───────────────────────────────┐
│ Ein Podman-Container:    │◄───────────┤ Browser im Kiosk-Modus         │
│  FastAPI + SQLite-Datei  │    (LAN)    │  (zeigt die Web-UI vom Server) │
│  auf gemountetem Volume  │             ├───────────────────────────────┤
└─────────────────────────┘    HTTPS    │ Reader-Agent (Python, nfcpy)   │
                              ◄──────────┤  liest PN532 per USB/COM-Port  │
                                          │  und meldet UIDs ans Backend   │
                                          └───────────────────────────────┘
```

- **Backend**: FastAPI + SQLAlchemy + SQLite (eine Datei), Jinja2/htmx-artige
  Server-Templates für Kiosk und Admin-Bereich. Läuft in einem einzigen Container.
- **Reader-Agent**: eigenständiges Python-Skript, läuft direkt auf dem
  Windows-Kiosk-PC (nicht im Container — Zugriff auf lokale USB-Hardware nötig).
- **Append-only-Log**: `checklog` erlaubt auf DB-Ebene nur `INSERT`; `UPDATE`/`DELETE`
  sind per SQLite-Trigger blockiert (Ausnahme: der Retention-Wartungsjob, siehe unten).

## Schnellstart: Server in 10 Minuten aufsetzen

Diese Anleitung setzt **nur** voraus, dass ihr euch per SSH auf einen Linux-Server
verbinden könnt (getestet mit Debian 12 / Ubuntu 22.04+) und dort `sudo`-Rechte habt.
Ihr müsst dafür kein Python, kein Docker und keine Datenbank kennen — das übernimmt
alles der Container.

**Was danach läuft**: RZ-CheckIn steckt komplett in einem einzigen "Container" — einer
Art abgeschlossener Mini-Umgebung, die die Anwendung samt allem, was sie braucht, schon
enthält. Ihr installiert auf dem Server nur das Werkzeug, das diesen Container ausführt
(**Podman**, siehe Schritt 1); alles andere bringt der Container mit.

### Schritt 1: Podman installieren

```bash
sudo apt update
sudo apt install -y podman git
```

Andere Distribution? Fedora/RHEL/Rocky: `sudo dnf install -y podman git`; Arch:
`sudo pacman -S podman git`.

Kurzer Test, ob es geklappt hat: `podman --version` sollte eine Versionsnummer ausgeben.

### Schritt 2: Projekt holen und das Container-Image bauen

```bash
git clone https://github.com/Volltext/RZ-CheckIn.git
cd RZ-CheckIn
podman build -t rz-checkin:latest -f Containerfile .
```

Der letzte Befehl dauert beim ersten Mal ein bis zwei Minuten (lädt einmalig eine
Python-Basis herunter, danach nur noch Sekunden bei erneuten Builds). Am Ende sollte
`podman images` eine Zeile mit `localhost/rz-checkin` bzw. `rz-checkin` zeigen.

> **Server ganz ohne Internetzugang?** Dann braucht ihr diesen Schritt auf dem
> Zielserver nicht — Image auf einem anderen Rechner bauen und übertragen, siehe
> ["Deployment ohne Internetzugang auf dem Server"](#deployment-ohne-internetzugang-auf-dem-server-air-gapped)
> weiter unten. Alles ab Schritt 3 hier funktioniert danach identisch.

### Schritt 3: Container starten

```bash
podman run -d --name rz-checkin \
  -p 8000:8000 \
  -v rz_checkin_data:/data \
  --restart on-failure \
  rz-checkin:latest
```

Kurz erklärt, was diese eine Zeile bewirkt:

| Teil | Bedeutung |
|---|---|
| `-d` | startet den Container im Hintergrund (statt das Terminal zu blockieren) |
| `--name rz-checkin` | Name, unter dem ihr den Container später wiederfindet (`podman ps`, `podman logs rz-checkin`, ...) |
| `-p 8000:8000` | macht ihn unter Port 8000 des Servers von außen erreichbar |
| `-v rz_checkin_data:/data` | legt Datenbank + Zugangsdaten dauerhaft in einem benannten Volume ab — überlebt Neustarts und Updates |
| `--restart on-failure` | startet den Container automatisch neu, falls er abstürzt |
| `rz-checkin:latest` | das eben gebaute (oder geladene) Image |

Prüfen, ob er läuft: `podman ps` sollte `rz-checkin` mit Status `Up ...` auflisten.

### Schritt 4: Admin-Zugangsdaten auslesen

Beim allerersten Start wird automatisch ein Admin-Zugang mit zufälligem Passwort
angelegt — es ist **nichts weiter zu konfigurieren**. Die Zugangsdaten stehen einmalig
in den Logs:

```bash
podman logs rz-checkin | grep -A3 Erststart
```

Die Ausgabe sieht ungefähr so aus:

```
========================================================================
RZ-CheckIn: Erststart -- Admin-Zugang wurde automatisch angelegt:
  Benutzername: admin
  Passwort:     ab12cd34ef56...
  Bitte nach der ersten Anmeldung unter /admin/passwort aendern!
========================================================================
```

Diese Zugangsdaten jetzt notieren — sie erscheinen wirklich nur beim allerersten Start
und lassen sich später nicht erneut anzeigen (siehe
["Admin-Passwort vergessen"](#problemlösung-häufige-stolpersteine), falls sie doch mal
verloren gehen).

### Schritt 5: Im Browser öffnen

Die IP-Adresse des Servers herausfinden (auf dem Server ausführen): `hostname -I` — die
erste ausgegebene Adresse ist meist die richtige.

- **Kiosk-Oberfläche** (läuft später am Eingang des Rechenzentrums):
  `http://<server-ip>:8000/`
- **Admin-Bereich**: `http://<server-ip>:8000/admin` — mit den Zugangsdaten aus Schritt
  4 einloggen und **sofort** unter "Eigenes Passwort" ein neues, eigenes Passwort
  setzen.

### Schritt 6 (empfohlen): Autostart nach einem Server-Neustart

`--restart on-failure` aus Schritt 3 startet den Container neu, wenn er selbst
abstürzt — startet aber der ganze Server neu (Stromausfall, Wartungsfenster), muss
Podman erst wissen, dass es seine Container wieder hochfahren soll:

```bash
sudo systemctl enable --now podman-restart.service
```

Das reicht für den normalen Betrieb. Wer es robuster mag (eigene systemd-Unit mit
Healthcheck, sauberer Neustart bei Absturz, getrennte Konfigurationsdatei für Secrets),
findet die Podman-Quadlet-Variante unter
["Produktivbetrieb mit systemd (Podman-Quadlet)"](#produktivbetrieb-mit-systemd-podman-quadlet).

### Fertig — was jetzt?

- Für jeden Technikraum bzw. Kiosk-PC im Admin-Bereich unter **"Agenten"** einen
  Reader-Agenten anlegen (der API-Key wird dabei einmalig angezeigt) — siehe
  `agent/README.md` für die Einrichtung auf dem Windows-Kiosk-PC mit dem Kartenleser
  und `deploy/KIOSK.md` für den Browser im Kiosk-Modus.
- Titel im Kiosk-Header anpassen: Umgebungsvariable `RZ_SITE_TITLE` (siehe
  ["Eigene Konfiguration"](#eigene-konfiguration-umgebungsvariablen)).
- Läuft alles wie gewünscht, lohnt sich ein Blick in
  ["Deployment (Referenz & Produktivbetrieb)"](#deployment-referenz--produktivbetrieb)
  weiter unten für Reverse-Proxy/TLS, Backups und den Retention-Wartungsjob.

### Problemlösung (häufige Stolpersteine)

- **Seite im Browser nicht erreichbar**: Läuft der Container überhaupt?
  `podman ps` sollte `rz-checkin` mit Status `Up` zeigen (steht dort `Exited`, siehe
  nächster Punkt). Falls er läuft: Firewall auf dem Server prüfen — Port 8000 muss vom
  Client-Netz aus erreichbar sein, z. B. `sudo ufw allow 8000/tcp` bei aktivierter
  UFW-Firewall unter Ubuntu/Debian.
- **`podman: command not found`**: Podman ist nicht (oder nicht erfolgreich) installiert
  — Schritt 1 wiederholen, ggf. vorher `sudo apt update` erneut ausführen.
- **Container startet nicht oder stürzt sofort wieder ab**: Logs ansehen —
  `podman logs rz-checkin` zeigt die Fehlermeldung. Häufigster Grund: Port 8000 ist auf
  dem Server bereits belegt (`-p 8000:8000` ändern, z. B. `-p 8080:8000`, dann über
  `http://<server-ip>:8080/` erreichbar).
- **Admin-Passwort vergessen**: Ein zusätzlicher Admin-User lässt sich jederzeit direkt
  im laufenden Container anlegen (ersetzt nicht das alte Passwort, gibt aber sofort
  wieder Zugriff):
  ```bash
  podman exec -it rz-checkin python -m app.cli create-admin --username admin2
  ```
  Fragt interaktiv nach einem neuen Passwort; danach mit `admin2` einloggen.
- **Nach einem Update oder Neustart sind alle Daten weg**: Das Volume
  `rz_checkin_data` wurde vermutlich nicht mit angegeben. Mit `podman volume ls`
  prüfen, ob es das Volume noch gibt — falls ja, ist es nur nicht korrekt eingebunden
  (`-v rz_checkin_data:/data` beim `podman run` nicht vergessen).
- **Von vorne anfangen** (nur zum Ausprobieren, **niemals im Produktivbetrieb mit
  echten Daten** — löscht unwiderruflich alles!):
  ```bash
  podman rm -f rz-checkin
  podman volume rm rz_checkin_data
  ```

## Kiosk-Oberfläche

Die Startseite (`/`) zeigt oben den Scan-Status sowie den Button für externe Besucher,
darunter eine Split-Ansicht mit einer Spalte je Technikraum:

- **Scan-Bereich**: im Ruhezustand nur ein kleines "Bereit zum Einlesen"-Icon (bewusst
  minimal, der Platz gehört der Raum-Übersicht); nach einem Scan erscheint dort für ein
  paar Sekunden das Ergebnis (eingecheckt/ausgecheckt, mit Ton).
- **Ein Kärtchen pro Technikraum** (ein Eintrag pro angelegtem Reader-Agenten, siehe
  "Mehrere Technikräume" unten):
  - *Mitarbeiter (intern)*: nur die Anzahl der aktuell in diesem Raum Anwesenden,
    dargestellt als grüner Punkt + Zähler (kein Name, keine Kartennummer — siehe
    Datensparsamkeit oben). Ein manuelles Auschecken einzelner Mitarbeiter gibt es am
    Kiosk deshalb bewusst nicht mehr; dafür gibt es das automatische Auschecken nach
    Zeitablauf sowie bei Bedarf den Admin-Bereich (`/admin/mitarbeiter` — Kartenverwaltung
    für Mitarbeiter, aktuell ohne eigenen Menüpunkt in der Navigation, aber weiterhin
    unter dieser Adresse erreichbar).
  - *Externe Besucher*: eine Liste mit Name/Firma/Zeit und "Auschecken"-Button pro Zeile
    (z. B. wenn jemand vergessen hat, sich abzumelden).
  - Personen ohne (mehr) gültige Raumzuordnung (z. B. Alteinträge von vor Einführung
    dieser Funktion) landen in einem zusätzlichen Kärtchen "Ohne Raumzuordnung".
- **Neue Dienstausweise**: hält jemand eine noch unbekannte Karte an den Reader, zeigt
  der Scan-Bereich statt einer Fehlermeldung einen Hinweis mit einem
  "Registrieren"-Button — ein Klick legt die Kartennummer an und checkt sofort ein, ganz
  ohne Umweg über den Admin-Bereich und **ohne Namenseingabe**.
- **Externe Besucher einchecken**: eigene, für Touch-Terminals optimierte Maske
  (`/kiosk/besucher`) mit Suche nach vorhandenem Profil (im Admin-Bereich unter
  "Einstellungen" an-/abschaltbar) und Formular für ein neues Profil; der
  Bestätigen-Button sitzt oben (sticky), damit eine aufklappende Bildschirmtastatur ihn
  nicht verdeckt.

### Mehrere Technikräume über einen Server

Ein Server kann mehrere Technikräume gleichzeitig loggen, wenn jeder Raum seinen
eigenen Reader-Agenten hat (siehe `/admin/agenten`): die **Bezeichnung** des Agenten ist
zugleich der Anzeigename des Raums in der Split-Ansicht oben, und jeder Scan dieses
Agenten wird automatisch diesem Raum zugeordnet — eine eigene Raumverwaltung gibt es
bewusst nicht, da ohnehin genau ein Agent pro Raum existiert.

Externe Besucher haben keinen eigenen Reader; sie wählen den Raum stattdessen manuell
beim Einchecken am Kiosk (`/kiosk/besucher`) über zwei große Touch-Karten, bevor sie
gesucht oder neu angelegt werden. Bei nur einem angelegten Agenten entfällt die Auswahl
(der einzige Raum wird automatisch übernommen), bei keinem läuft der Checkin wie bisher
ganz ohne Raumzuordnung.

## Projektstruktur

```
app/            FastAPI-Anwendung (läuft im Container)
  routers/      api_agent, api_public (inkl. /health), kiosk, admin
  services/     attendance (Toggle-Logik), retention, export, visitors, feedback, settings
  templates/    Jinja2-Templates (kiosk/, admin/)
  static/       CSS + minimales eigenes JS (kein CDN, siehe unten)
  cli.py        python -m app.cli {create-admin, create-agent, purge}
agent/          Reader-Agent für den Kiosk-PC (separates Programm, siehe agent/README.md)
tests/          pytest-Suite
deploy/         Podman-Quadlet-Unit, Retention-Timer, nginx-Beispiel, Kiosk-Anleitung
docs/           PRTG-Sensor-Konfiguration
```

## Lokal entwickeln

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # anpassen, insb. RZ_SESSION_SECRET und RZ_ADMIN_PASSWORD

python -m app.cli create-admin --username admin   # oder RZ_ADMIN_PASSWORD in .env setzen
uvicorn app.main:app --reload
```

- Kiosk-Oberfläche: `http://127.0.0.1:8000/`
- Admin-Bereich: `http://127.0.0.1:8000/admin`
- API-Dokumentation (Swagger): `http://127.0.0.1:8000/docs`

Reader-Agenten legt man im Admin-Bereich unter "Agenten" an (API-Key wird einmalig
angezeigt) und testet ohne Hardware:

```bash
python agent/reader_agent.py --config agent/agent.ini --simulate-uid AABBCCDD --once
```

## Tests

```bash
pytest -q
```

Jeder Test läuft gegen eine frische, temporäre SQLite-Datenbank (siehe
`tests/conftest.py`) — kein gemeinsamer Zustand zwischen Tests.

## Datenmodell (Kurzfassung)

- `employees` — Mitarbeiter, geführt **ausschließlich über `rfid_uid`** (die
  Dienstausweisnummer, eindeutig) + `aktiv`-Flag statt Löschen beim Ausscheiden. Bewusst
  **kein Name und keine sonstige personenbezogene Angabe** (siehe Datensparsamkeit oben).
  Die eigene `id` bleibt trotzdem bestehen, damit ein Kartentausch (verlorene/defekte
  Karte) möglich ist, ohne die Anwesenheitshistorie unter einem neuen Eintrag
  fortzuführen.
- `visitors` — Besucher-Stammdaten (Name, Firma, Telefon), dauerhaft/wiederverwendbar;
  DSGVO-Löschung entfernt die Zeile, das Log bleibt unangetastet (siehe unten).
- `checklog` — append-only, `person_type` + `person_id` (kein FK, polymorpher Verweis),
  `action` (checkin/checkout), `source` (`rfid`/`manual`/`auto` — Letzteres fürs
  automatische Auschecken), `raum` (Technikraum des Eintrags, siehe "Mehrere
  Technikräume" oben — ebenfalls kein FK, sondern eine lose Referenz auf
  `agents.agent_id`; `NULL` = keine Raumzuordnung). Der aktuelle "wer ist drin"-Status
  wird immer aus dem letzten Eintrag pro Person **abgeleitet**, nie separat gepflegt
  (`app/services/attendance.py::list_present`).
- `agents` — ein Eintrag pro Kiosk-PC bzw. Technikraum, API-Key-Hash + `last_seen` für
  die PRTG-Überwachung. Da pro Raum genau ein Agent existiert, dient der Eintrag
  zugleich als Raumzuordnung für die Split-Ansicht (siehe oben) — es gibt bewusst kein
  eigenes Raum-Modell dafür.
- `unknown_scans` — unbekannte UIDs, die am Reader gescannt wurden, zur späteren
  Zuordnung im Admin-Bereich.
- `settings` — Key-Value-Ablage für zur Laufzeit im Admin-Bereich änderbare
  Einstellungen (aktuell: `auto_checkout_hours`, `besucher_suche_aktiv`, siehe unten),
  im Unterschied zu `app/config.py` (Umgebungsvariablen, nur beim Start gelesen).

### Automatisches Auschecken

Vergisst jemand (Mitarbeiter oder externer Besucher), sich auszuchecken, checkt ein
Hintergrund-Task im Server-Prozess die Person automatisch aus, sobald der Check-in
länger als die im Admin-Bereich unter **Einstellungen** (`/admin/einstellungen`)
konfigurierte Anzahl Stunden zurückliegt (0 = deaktiviert, Standard 12h). Der Log-Eintrag
ist als `source=auto`, `operator="System (automatisch)"` erkennbar. Geprüft wird alle
`RZ_AUTO_CHECKOUT_CHECK_INTERVAL_SECONDS` (Standard 5 Minuten) — siehe
`app/services/attendance.py::run_auto_checkout`.

### Besuchersuche an-/abschalten

Ebenfalls unter `/admin/einstellungen`: ob die Kiosk-Maske für externe Besucher
(`/kiosk/besucher`) zuerst nach einem vorhandenen Profil suchen lässt, bevor ein neues
angelegt wird (Standard: an). Deaktiviert zeigt die Seite direkt nur das Formular zum
Neuanlegen — z. B. sinnvoll, wenn grundsätzlich jeder Besuch als neues Profil erfasst
werden soll.

### Schema-Migration bestehender Installationen

Es gibt bewusst kein separates Migrationswerkzeug (Alembic o.ä.) — beim Start
(`app/db.py::init_db`) erkennt die Anwendung ein älteres Schema (z. B. `employees` mit
noch vorhandenen Namensfeldern aus einer Version vor dieser Datenschutz-Anpassung) und
hebt es automatisch auf den aktuellen Stand: Namen werden dabei bewusst **nicht**
übernommen (genau das ist die fachliche Vorgabe), alle anderen Daten (Kartennummern,
Log-Einträge) bleiben vollständig erhalten. Rein additive Änderungen (z. B. die
nullable Spalte `checklog.raum` für die Technikraum-Zuordnung) zieht `init_db()`
einfacher per `ALTER TABLE ... ADD COLUMN` nach, ohne dass dafür eine Tabelle umbenannt
werden muss. Vor einem Update auf eine Version mit Datenmodelländerungen trotzdem ein
reguläres Backup ziehen (siehe Abschnitt "Backup").

### Append-only-Schutz

Zwei SQLite-Trigger (`app/db.py`) verbieten `UPDATE` auf `checklog` bedingungslos und
`DELETE`, solange die Hilfstabelle `retention_window` leer ist. Nur der Retention-Job
(`app/services/retention.py`) öffnet dieses Fenster für die Dauer einer einzigen
Transaktion. Die Anwendung selbst führt nie ein `UPDATE`/`DELETE` auf `checklog` aus.

**Grenze dieses Schutzes**: SQLite kennt keine Benutzerrollen auf DB-Ebene — wer
Schreibzugriff auf die Datenbankdatei selbst hat (z. B. Root auf dem Server), kann die
Trigger umgehen. Der Trigger schützt vor versehentlichen/fehlerhaften Änderungen aus der
Anwendung heraus, nicht vor einem Angreifer mit Root-Zugriff auf den Host. Dagegen
helfen Dateiberechtigungen, ein möglichst kleiner Angriffsvektor auf den Container/Host
und regelmäßige, unveränderliche Backups.

### DSGVO

- Besucherprofile: löschbar im Admin-Bereich (`/admin/besucher`). Löschen ist blockiert,
  solange die Person laut Log noch eingecheckt ist (erst auschecken). Beim Löschen wird
  nur die `visitors`-Zeile entfernt — das Log bleibt vollständig erhalten, zeigt für die
  betroffenen Einträge aber keinen Namen mehr an ("(gelöschtes Profil)").
- Log-Einträge: Aufbewahrungsfrist über `RZ_RETENTION_DAYS` (Standard 730 Tage / 2
  Jahre). Der tägliche Wartungsjob (`python -m app.cli purge`, siehe
  `deploy/rz-checkin-retention.timer`) löscht ältere Einträge hart und entfernt dabei
  verwaiste Besucherprofile (keine verbleibenden Log-Einträge mehr).
- Hinweistext für Besucher: dauerhaft im Fußbereich der Kiosk-Startseite.

## Deployment (Referenz & Produktivbetrieb)

Wer noch keinen laufenden Server hat: erst den
["Schnellstart"](#schnellstart-server-in-10-minuten-aufsetzen) oben durchgehen. Dieser
Abschnitt vertieft einzelne Themen für den dauerhaften Produktivbetrieb.

### Eigene Konfiguration (Umgebungsvariablen)

Alle Einstellungen laufen über Umgebungsvariablen mit Präfix `RZ_`, vollständige
Übersicht mit Erklärung in `.env.example`. Beim `podman run` per `-e` einzeln setzen
(z. B. `-e RZ_SITE_TITLE="Relaisstelle Musterberg"`) oder gesammelt über eine
Env-Datei:

```bash
podman run -d --name rz-checkin \
  -p 8000:8000 \
  -v rz_checkin_data:/data \
  --env-file /etc/rz-checkin/rz-checkin.env \
  --restart on-failure \
  rz-checkin:latest
```

Die wichtigsten Variablen für den Einstieg:

| Variable | Bedeutung | Standard |
|---|---|---|
| `RZ_SITE_TITLE` | Titel im Kiosk-Header | `Rechenzentrum Check-in` |
| `RZ_SESSION_SECRET` | Geheimwert zum Signieren der Admin-Session — ohne explizite Angabe wird beim Erststart automatisch einer erzeugt und auf dem Volume abgelegt (siehe `docker-entrypoint.sh`); für kontrollierte Deployments trotzdem selbst setzen (`openssl rand -hex 32`) | automatisch erzeugt |
| `RZ_ADMIN_PASSWORD` | Passwort für den automatisch angelegten Erst-Admin | leer → zufällig, siehe Schnellstart Schritt 4 |
| `RZ_AUTO_CHECKOUT_DEFAULT_HOURS` | Startwert fürs automatische Auschecken (danach zur Laufzeit unter "Einstellungen" änderbar) | `12` |
| `RZ_RETENTION_DAYS` | Aufbewahrungsfrist für Log-Einträge | `730` (2 Jahre) |
| `RZ_ADMIN_IP_ALLOWLIST` | Kommagetrennte IPs/CIDRs, die zusätzlich zum Login auf `/admin` zugreifen dürfen | leer = keine Einschränkung |

### Updates einspielen

```bash
cd RZ-CheckIn
git pull
podman build -t rz-checkin:latest -f Containerfile .
podman stop rz-checkin
podman rm rz-checkin
podman run -d --name rz-checkin \
  -p 8000:8000 \
  -v rz_checkin_data:/data \
  --restart on-failure \
  rz-checkin:latest
```

`podman rm` entfernt nur den Container selbst, nicht das Volume `rz_checkin_data` —
alle Besucherprofile, Log-Einträge und Zugangsdaten bleiben erhalten. Schema-Änderungen
migriert die Anwendung beim Start automatisch (siehe "Schema-Migration bestehender
Installationen" oben); vor einem größeren Update trotzdem ein Backup ziehen (siehe
"Backup" unten). Läuft der Container über die Quadlet-Unit (siehe unten), reicht
stattdessen `systemctl restart rz-checkin.service` nach dem `podman build`.

### Produktivbetrieb mit systemd (Podman-Quadlet)

Robuster als das einfache `--restart on-failure` aus dem Schnellstart: eine
Podman-Quadlet-Unit (Autostart auch nach Server-Neustart, `Restart=on-failure`,
Healthcheck, keine eigene Compose-Datei nötig).

1. `deploy/rz-checkin.container` nach `/etc/containers/systemd/` kopieren und anpassen
   (Image-Quelle, `EnvironmentFile`).
2. Optional: eigene Secrets (`RZ_SESSION_SECRET`, `RZ_ADMIN_PASSWORD`, ...) in
   `/etc/rz-checkin/rz-checkin.env` ablegen, Dateirechte einschränken (`chmod 600`).
   Ohne diese Datei funktioniert der Start trotzdem — siehe Schnellstart oben.
3. `systemctl daemon-reload && systemctl start rz-checkin.service`

Läuft der Container schon aus dem Schnellstart heraus per `podman run`, vorher mit
`podman rm -f rz-checkin` entfernen (das Volume `rz_checkin_data` bleibt dabei
erhalten) — die Quadlet-Unit legt ihn danach neu an.

Retention-Wartungsjob: `deploy/rz-checkin-retention.service` +
`deploy/rz-checkin-retention.timer` nach `/etc/systemd/system/` kopieren, dann
`systemctl enable --now rz-checkin-retention.timer`. Läuft bewusst als eigener,
täglicher Job getrennt von der Anwendung.

Optionaler Reverse-Proxy mit eigenem TLS-Zertifikat: `deploy/nginx-rz-checkin.conf`.

### Deployment ohne Internetzugang auf dem Server (air-gapped)

Steht der Server komplett ohne Internetzugang im internen Netz, entfällt Schritt 2 des
Schnellstarts (`git clone` + `podman build` brauchen Internetzugang). Stattdessen das
Image auf einer Maschine **mit** Internetzugang bauen und als Datei übertragen:

```bash
# Auf der Build-Maschine (mit Internetzugang):
git clone https://github.com/Volltext/RZ-CheckIn.git
cd RZ-CheckIn
podman build -t rz-checkin:latest -f Containerfile .
podman save rz-checkin:latest -o rz-checkin-image.tar.gz --format docker-archive
# oder mit Docker gebaut: docker save rz-checkin:latest | gzip > rz-checkin-image.tar.gz
```

Die Datei `rz-checkin-image.tar.gz` per USB-Stick oder internem Fileshare auf den
Zielserver übertragen, dort laden und wie im Schnellstart ab Schritt 3 weitermachen:

```bash
podman load -i rz-checkin-image.tar.gz
podman run -d --name rz-checkin \
  -p 8000:8000 \
  -v rz_checkin_data:/data \
  --restart on-failure \
  rz-checkin:latest
```

Reader-Agenten (für die Kiosk-PCs) werden anschließend ganz normal im Admin-Bereich
unter "Agenten" angelegt (siehe `agent/README.md`).

Auch der Reader-Agent selbst und der Kiosk-PC sind für den Betrieb ganz ohne
Internetzugang ausgelegt (siehe `agent/README.md` Abschnitt 6, "Wheelhouse"); die
Web-Oberfläche lädt zudem kein CDN/keine externen Schriften (siehe
["Frontend-Technologie"](#frontend-technologie)) und funktioniert komplett aus dem
Server heraus.

### Backup

Die SQLite-Datei liegt auf dem Podman-Volume. Einfachste Variante: den Container kurz
stoppen und die Datei kopieren, oder SQLites Online-Backup-API bei laufendem Betrieb
nutzen (z. B. `sqlite3 /pfad/zur/db ".backup /pfad/zum/backup.db"` — funktioniert auch
während des laufenden Containers, da SQLite dafür ausgelegt ist). Backups regelmäßig per
Cronjob auf dem Host anlegen und **außerhalb** des Containers/Hosts aufbewahren.

Die eigentliche Datei liegt innerhalb des Volumes; ihren Pfad auf dem Host findet man
mit `podman volume inspect rz_checkin_data`.

### Kiosk-PC (Windows)

Siehe `deploy/KIOSK.md` (Browser im Kiosk-Modus, Absicherung) und `agent/README.md`
(Reader-Agent für den ACR122U-A9, als Kommandozeilen-Dienst (nssm) oder als
Systray-.exe für den normalen Autostart, inkl. air-gapped Build-Anleitung).

### Monitoring (PRTG)

Siehe `docs/PRTG.md`.

## Sicherheitsannahmen

- Rein internes Tool: kein Internetzugang für Server oder Kiosk-PC nötig, Betrieb im
  internen VLAN (siehe "Deployment ohne Internetzugang auf dem Server" oben).
- Kiosk-Oberfläche (`/`, `/kiosk/...`) ist bewusst ohne Login — sie steht am Kiosk-PC vor
  Ort und ihre Nutzung (Live-Übersicht einsehen, Besucher ein-/auschecken, neue
  Dienstausweise registrieren) ist nicht schützenswert im gleichen Sinn wie der
  Admin-Bereich.
- **Registrierung neuer Dienstausweise** (unbekannte Karte → per Knopfdruck am Kiosk
  anlegen, siehe oben) folgt demselben Vertrauensmodell: wer physisch bis zum Reader
  vordringt, darf ohnehin ins Rechenzentrum. Der Zutritt selbst wird weiterhin vom
  bestehenden Zutrittssystem kontrolliert (Konzept: "steuert keine Türen") — die
  Registrierung entscheidet nicht, wer reindarf, sondern nur, ab wann eine ohnehin
  gültige Karte im Protokoll erscheint. Es wird dabei bewusst kein Name erfasst.
- Admin-Bereich (`/admin/...`) ist Login-geschützt (Argon2-Passworthash, signierte
  Session-Cookies) und kann zusätzlich per `RZ_ADMIN_IP_ALLOWLIST` auf bestimmte
  Quell-IPs eingeschränkt werden.
- Reader-Agent-Endpunkte (`/api/checkin/rfid`, `/api/agent/heartbeat`) verlangen einen
  `X-Agent-Key`-Header; der Key wird nur als SHA-256-Hash gespeichert und beim Anlegen
  im Admin-Bereich einmalig im Klartext angezeigt.
- Ein lokaler Admin-User genügt für v1. Der Login läuft hinter einem
  `AuthProvider`-Protokoll (`app/auth/`), damit sich später ein LDAP/AD-Provider
  ergänzen lässt, ohne das Datenmodell zu ändern.
- **Automatischer Admin-Bootstrap**: Ist beim allerersten Start kein `RZ_ADMIN_PASSWORD`
  gesetzt, legt der Container selbst einen Admin mit einem zufälligen Passwort an und
  zeigt es einmalig in den Logs (`podman logs`) an — damit ist ein frisch gestarteter
  Container ohne weitere Einrichtung sofort nutzbar (siehe Schnellstart oben). Wer das
  nicht möchte (z. B. automatisiertes Deployment mit separat provisioniertem Admin),
  setzt `RZ_ADMIN_AUTO_BOOTSTRAP=false`.

## Frontend-Technologie

Server-gerenderte Jinja2-Templates + ein winziges eigenes JavaScript
(`app/static/app.js`, keine externe Bibliothek) für Polling (Live-Übersicht,
Scan-Feedback) und Live-Suche. Bewusst kein CDN-Import (Kiosk-PC braucht laut Konzept
keinen Internetzugang) und keine Build-Pipeline. Zustandsändernde Aktionen
(Besucher anlegen/ein-/auschecken, Admin-Formulare) laufen über normale HTML-Formulare
mit Server-Redirect.

**Dark Mode**: Der Admin-Bereich folgt automatisch der Farbschema-Einstellung des
Betriebssystems/Browsers (`prefers-color-scheme`), kein manueller Umschalter nötig. Der
Kiosk-Bildschirm bleibt bewusst immer dunkel (Wandmontage, aus der Distanz lesbar) und
ändert sich nicht mit dem OS-Farbschema.

**Polling & Formulare**: `app.js` überschreibt ein per Polling nachgeladenes Fragment
nicht, solange der Nutzer gerade in einem darin enthaltenen Feld tippt (erkannt an
Fokus *und* einem bereits eingegebenen Wert) — wichtig z. B. für die Besuchersuche, wo
die Ergebnisliste sonst während der Eingabe verschwinden könnte.
