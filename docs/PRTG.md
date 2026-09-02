# PRTG-Überwachung

Zwei unabhängige Sensoren decken die beiden relevanten Fehlerbilder ab (siehe Konzept
Abschnitt 8.3): "Server ist down" und "Server läuft, aber der Kiosk-PC/Reader-Agent hat
keine Verbindung mehr".

## 1. Allgemeiner Backend-Health-Sensor

- Sensortyp: **HTTP** (einfacher Erreichbarkeits-Check reicht) oder **HTTP Data Advanced**
  für eine inhaltliche Prüfung.
- URL: `https://rz-checkin.intern.example.org/health`
- Erwartete Antwort: `{"status": "ok", "database": "ok", "version": "..."}`
- Bei "HTTP Data Advanced": JSON-Pfad `status` auf den erwarteten Wert `ok` prüfen.

## 2. Agent-Heartbeat-Sensor (pro Kiosk-PC)

- Sensortyp: **HTTP Data Advanced** (oder **REST Custom Sensor**)
- URL: `https://rz-checkin.intern.example.org/health/agent/kiosk1`
  (`kiosk1` durch die jeweilige Agent-ID ersetzen, siehe Admin-Bereich → Agenten)
- Antwortbeispiel:
  ```json
  {
    "agent_id": "kiosk1",
    "status": "online",
    "last_seen": "2025-01-15T10:32:04.123456+00:00",
    "seconds_since_last_heartbeat": 12.3
  }
  ```
- Der Endpunkt antwortet **immer mit HTTP 200** (auch wenn `status` = `offline`), damit
  PRTG die JSON-Antwort auswerten kann statt nur den HTTP-Statuscode zu sehen. Einen
  unbekannten `agent_id` liefert HTTP 404 -- das sollte im Normalbetrieb nicht auftreten
  und darf als Fehler gewertet werden.
- Kanal/Regel in PRTG:
  - JSON-Pfad `status` == `"online"` → OK, sonst Warnung/Fehler.
  - Alternativ/zusätzlich: `seconds_since_last_heartbeat` als numerischen Kanal
    auslesen und einen Schwellwert setzen (Standard-Grenze im Backend:
    `RZ_AGENT_OFFLINE_THRESHOLD_SECONDS`, Default 90 Sekunden -- der PRTG-Schwellwert
    sollte etwas darüber liegen, um mit dem Backend-Status übereinzustimmen).
  - `status: "unknown"` (Agent wurde angelegt, hat aber noch nie einen Heartbeat
    gesendet) separat behandeln -- das ist normal direkt nach dem Anlegen eines neuen
    Agenten, aber auffällig, wenn der Agent schon länger laufen sollte.

## 3. Ergebnis

| Sensor | Rot bedeutet |
|---|---|
| Backend-Health | Server/Container ist nicht erreichbar oder die Datenbank antwortet nicht |
| Agent-Heartbeat | Server läuft, aber der Reader-Agent auf diesem Kiosk-PC meldet sich nicht mehr (Netzwerk, Absturz, Reader-Problem) |

Ein Agenten-Eintrag wird im Admin-Bereich unter "Agenten" angelegt; der API-Key wird
dabei einmalig angezeigt und muss in die `agent.ini` des jeweiligen Kiosk-PCs
eingetragen werden (siehe `agent/README.md`).
