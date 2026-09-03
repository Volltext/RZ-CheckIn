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
Anforderungsdokument; dieses README beschreibt den Code und den Betrieb.

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
    Zeitablauf sowie bei Bedarf den Admin-Bereich (`/admin/mitarbeiter`).
  - *Externe Besucher*: eine Liste mit Name/Firma/Zeit und "Auschecken"-Button pro Zeile
    (z. B. wenn jemand vergessen hat, sich abzumelden).
  - Personen ohne (mehr) gültige Raumzuordnung (z. B. Alteinträge von vor Einführung
    dieser Funktion) landen in einem zusätzlichen Kärtchen "Ohne Raumzuordnung".
- **Neue Dienstausweise**: hält jemand eine noch unbekannte Karte an den Reader, zeigt
  der Scan-Bereich statt einer Fehlermeldung einen Hinweis mit einem
  "Registrieren"-Button — ein Klick legt die Kartennummer an und checkt sofort ein, ganz
  ohne Umweg über den Admin-Bereich und **ohne Namenseingabe**.

### Mehrere Technikräume über einen Server

Ein Server kann mehrere Technikräume gleichzeitig loggen, wenn jeder Raum seinen
eigenen Reader-Agenten hat (siehe `/admin/agenten`): die **Bezeichnung** des Agenten ist
zugleich der Anzeigename des Raums in der Split-Ansicht oben, und jeder Scan dieses
Agenten wird automatisch diesem Raum zugeordnet — eine eigene Raumverwaltung gibt es
bewusst nicht, da ohnehin genau ein Agent pro Raum existiert.

Externe Besucher haben keinen eigenen Reader; sie wählen den Raum stattdessen manuell
beim Einchecken am Kiosk (`/kiosk/besucher`) über zwei große Touch-Buttons, bevor sie
gesucht oder neu angelegt werden. Bei nur einem angelegten Agenten entfällt die Auswahl
(der einzige Raum wird automatisch übernommen), bei keinem läuft der Checkin wie bisher
ganz ohne Raumzuordnung.

## Projektstruktur

```
app/            FastAPI-Anwendung (läuft im Container)
  routers/      api_agent, api_public (inkl. /health), kiosk, admin
  services/     attendance (Toggle-Logik), retention, export, visitors, feedback
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
  Einstellungen (aktuell: `auto_checkout_hours`, siehe unten), im Unterschied zu
  `app/config.py` (Umgebungsvariablen, nur beim Start gelesen).

### Automatisches Auschecken

Vergisst jemand (Mitarbeiter oder externer Besucher), sich auszuchecken, checkt ein
Hintergrund-Task im Server-Prozess die Person automatisch aus, sobald der Check-in
länger als die im Admin-Bereich unter **Einstellungen** (`/admin/einstellungen`)
konfigurierte Anzahl Stunden zurückliegt (0 = deaktiviert, Standard 12h). Der Log-Eintrag
ist als `source=auto`, `operator="System (automatisch)"` erkennbar. Geprüft wird alle
`RZ_AUTO_CHECKOUT_CHECK_INTERVAL_SECONDS` (Standard 5 Minuten) — siehe
`app/services/attendance.py::run_auto_checkout`.

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

## Deployment

### Super-easy: fertiges Image laden und starten

Für den schnellen Test oder ein Deployment ohne Build-Toolchain auf dem Server: ein
exportiertes Image-Tar laden und direkt starten. Session-Secret und ein erster
Admin-Zugang werden beim allerersten Start automatisch erzeugt (siehe unten) — es ist
**nichts weiter zu konfigurieren**.

```bash
podman load -i rz-checkin-image.tar.gz
podman run -d --name rz-checkin \
  -p 8000:8000 \
  -v rz_checkin_data:/data \
  --restart on-failure \
  rz-checkin:latest

# Admin-Zugangsdaten aus den Logs des allerersten Starts holen:
podman logs rz-checkin | grep -A3 Erststart
```

Danach ist die Kiosk-Oberfläche unter `http://<server>:8000/` erreichbar, der
Admin-Bereich unter `http://<server>:8000/admin` (Zugangsdaten siehe oben, direkt nach
dem ersten Login unter `/admin/passwort` ändern). `-v rz_checkin_data:/data` sorgt
dafür, dass die SQLite-Datenbank und das automatisch erzeugte Session-Secret einen
Container-Neustart oder -Update überleben — beim Neustart erscheinen dann keine neuen
Zugangsdaten mehr in den Logs, der bestehende Admin bleibt gültig.

Reader-Agenten (für die Kiosk-PCs) werden anschließend ganz normal im Admin-Bereich
unter "Agenten" angelegt (siehe `agent/README.md`).

Das Image-Tar selbst erzeugt man auf einer Maschine mit Build-Werkzeug einmalig aus dem
Quellcode und verteilt es dann z. B. per USB-Stick oder internem Fileshare an den
Zielserver (kein Registry-Zugriff auf dem Server nötig):

```bash
podman build -t rz-checkin:latest -f Containerfile .
podman save rz-checkin:latest -o rz-checkin-image.tar.gz --format docker-archive
# oder mit Docker gebaut: docker save rz-checkin:latest | gzip > rz-checkin-image.tar.gz
```

### Server (Podman, ein Container) — aus dem Quellcode bauen

```bash
podman build -t rz-checkin:latest -f Containerfile .
```

Für den dauerhaften Betrieb die Podman-Quadlet-Unit verwenden (Autostart,
`Restart=on-failure`, kein eigenes Compose nötig):

1. `deploy/rz-checkin.container` nach `/etc/containers/systemd/` kopieren und anpassen
   (Image-Quelle, `EnvironmentFile`).
2. Optional: eigene Secrets (`RZ_SESSION_SECRET`, `RZ_ADMIN_PASSWORD`, ...) in
   `/etc/rz-checkin/rz-checkin.env` ablegen, Dateirechte einschränken (`chmod 600`).
   Ohne diese Datei funktioniert der Start trotzdem — siehe "Super-easy" oben.
3. `systemctl daemon-reload && systemctl start rz-checkin.service`

Retention-Wartungsjob: `deploy/rz-checkin-retention.service` +
`deploy/rz-checkin-retention.timer` nach `/etc/systemd/system/` kopieren, dann
`systemctl enable --now rz-checkin-retention.timer`. Läuft bewusst als eigener,
täglicher Job getrennt von der Anwendung.

Optionaler Reverse-Proxy mit eigenem TLS-Zertifikat: `deploy/nginx-rz-checkin.conf`.

### Backup

Die SQLite-Datei liegt auf dem Podman-Volume. Einfachste Variante: den Container kurz
stoppen und die Datei kopieren, oder SQLites Online-Backup-API bei laufendem Betrieb
nutzen (z. B. `sqlite3 /pfad/zur/db ".backup /pfad/zum/backup.db"` — funktioniert auch
während des laufenden Containers, da SQLite dafür ausgelegt ist). Backups regelmäßig per
Cronjob auf dem Host anlegen und **außerhalb** des Containers/Hosts aufbewahren.

### Kiosk-PC (Windows)

Siehe `deploy/KIOSK.md` (Browser im Kiosk-Modus, Absicherung) und `agent/README.md`
(Reader-Agent für den ACR122U-A9, als Kommandozeilen-Dienst (nssm) oder als
Systray-.exe für den normalen Autostart, inkl. air-gapped Build-Anleitung).

### Monitoring (PRTG)

Siehe `docs/PRTG.md`.

## Air-Gapped-Betrieb

Server und Kiosk-PC sind für den Betrieb ganz ohne Internetzugang ausgelegt:

- **Server**: Das Container-Image wird auf einer Maschine mit Internetzugang einmalig
  gebaut und als Tar exportiert (`podman save`), dann per USB-Stick/internem Fileshare
  auf den Zielserver übertragen und dort per `podman load` gestartet — kein
  Registry-Zugriff auf dem Server nötig (siehe "Super-easy Deployment" oben).
- **Reader-Agent**: dieselbe Idee als "Wheelhouse" (vorab heruntergeladene
  Python-Pakete) — einmal auf einem Rechner mit Internetzugang vorbereiten
  (`agent/prepare_wheelhouse.ps1`), Ordner übertragen, damit air-gapped installieren
  bzw. die `.exe` bauen (`agent/build_exe.ps1 -Wheelhouse ...`). Details in
  `agent/README.md` Abschnitt 6. Die fertige `.exe` selbst braucht auf dem Kiosk-PC
  danach keinerlei Netzwerkzugriff mehr.
- Kein CDN-Import im Frontend, keine externen Schriften/Icons (siehe
  "Frontend-Technologie" unten) — die Web-Oberfläche funktioniert komplett aus dem
  Server heraus.

## Sicherheitsannahmen

- Rein internes Tool: kein Internetzugang für Server oder Kiosk-PC nötig, Betrieb im
  internen VLAN (siehe "Air-Gapped-Betrieb" oben).
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
  Container ohne weitere Einrichtung sofort nutzbar (siehe "Super-easy Deployment"
  oben). Wer das nicht möchte (z. B. automatisiertes Deployment mit separat
  provisioniertem Admin), setzt `RZ_ADMIN_AUTO_BOOTSTRAP=false`.

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
