# Kiosk-PC einrichten (Windows)

Der Kiosk-PC führt zwei unabhängige Prozesse aus: einen Browser im Kiosk-Modus (zeigt
die vom Server ausgelieferte Web-Oberfläche) und den Reader-Agenten (siehe
`agent/README.md`). Dieses Dokument behandelt den Browser-Teil und die
Absicherung des PCs.

## 1. Chromium im Kiosk-Modus starten

```powershell
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --kiosk "https://rz-checkin.intern.example.org/" ^
  --noerrdialogs ^
  --disable-translate ^
  --no-first-run ^
  --disable-pinch ^
  --overscroll-history-navigation=0 ^
  --incognito
```

- `--incognito`: keine gespeicherten Formulardaten/Autofill zwischen Besuchern.
- Kein `--app=` verwenden, damit die normale Kiosk-Navigation (Tastatur/Touch) innerhalb
  der Seite funktioniert; `--kiosk` blendet Adressleiste, Tabs und Fenstersteuerung aus.

## 2. Autostart

Verknüpfung mit obigem Befehl in den Autostart-Ordner legen:
`shell:startup` (für den lokalen Kiosk-Benutzer) bzw.
`shell:common startup` (für alle Benutzer).

Windows so konfigurieren, dass der Kiosk-Benutzer automatisch angemeldet wird
(`netplwiz` / Autologon-Tool aus den Sysinternals), damit der PC nach einem Neustart
ohne Eingriff wieder im Kiosk-Modus startet.

## 3. Absicherung gegen Verlassen des Kiosk-Modus

- Windows-Tastenkombinationen einschränken (Gruppenrichtlinie oder ein Tool wie
  "Kiosk Browser Lockdown"): mindestens Alt+Tab, Alt+F4, Win-Taste, Strg+Alt+Entf
  (soweit von der jeweiligen Windows-Edition aus konfigurierbar), Rechtsklick im
  Browser deaktivieren (`--disable-context-menu` gibt es in Chromium nicht direkt --
  alternativ eine Extension/Policy verwenden).
- Für höhere Robustheit: **Windows-Kioskmodus mit zugewiesenem Zugriff** nutzen
  (Einstellungen → Konten → Weitere Konten → Zugriff auf ein Kiosk zuweisen), der
  Chromium als einzige zulässige App startet und alle Windows-Shell-Elemente blockiert.
- Physischer Zugriff auf Tastatur nur so weit wie nötig belassen (Touchscreen bevorzugt,
  Tastatur ggf. nur für Ausnahmefälle bereithalten).
- Automatische Windows-Updates außerhalb der Betriebszeiten planen, damit der Kiosk
  nicht während des Betriebs neu startet.

## 4. Verbindung zum Server

- Server-URL läuft über internes VLAN/Netz, kein Internetzugang vom Kiosk-PC nötig
  (siehe Konzept Abschnitt 5 "Netzwerk").
- Bei TLS mit eigenem/internem Zertifikat: das Root-CA-Zertifikat im Windows
  Zertifikatspeicher ("Vertrauenswürdige Stammzertifizierungsstellen", Computerkonto)
  hinterlegen, sonst zeigt Chromium eine Zertifikatswarnung im Kiosk-Modus.
- Läuft der Server hinter dem nginx-Beispiel aus `deploy/nginx-rz-checkin.conf`: die
  Anwendung liest `request.client.host` für `RZ_ADMIN_IP_ALLOWLIST` direkt aus der
  TCP-Verbindung. Hinter einem Reverse-Proxy ist das die Proxy-IP, nicht die des
  Clients -- die Allowlist ist dann nur sinnvoll, wenn der Proxy selbst im
  Admin-Netzsegment steht, oder `uvicorn --proxy-headers` zusammen mit einem
  vertrauenswürdigen `--forwarded-allow-ips` konfiguriert wird.

## 5. DSGVO-Hinweis

Der Kiosk-Bildschirm zeigt dauerhaft im Fußbereich einen Hinweis, dass Name, ggf. Firma
und Zeitpunkt von Ein-/Auscheckung protokolliert werden (siehe `app/templates/kiosk/index.html`).
Diesen Text bei Bedarf an die Formulierung des Datenschutzbeauftragten anpassen.
