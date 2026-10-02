"""Kiosk-Oberfläche: Live-Übersicht, Ein-/Auschecken für Externe.

Kein Login nötig (Konzept 3.3) — die Seite steht am Kiosk-PC vor Ort. Für Mitarbeiter
gibt es kein Register: jede am Reader gescannte Karten-UID togglet direkt Checkin/
Checkout (siehe app/services/attendance.py::record_rfid_scan), ohne dass die Karte
vorher irgendwo angelegt werden muss. Es wird dabei bewusst NUR die Kartennummer
gespeichert -- kein Name, keine Verknüpfung zu einer Person. Deshalb zeigt die
Live-Übersicht für Mitarbeiter auch keine Namen/Zeilen, nur die Anzahl der aktuell
Anwesenden; ein manuelles Auschecken einzelner Mitarbeiter über den Kiosk entfällt damit
(dafür gibt es das automatische Auschecken nach Zeitablauf, siehe
app/services/attendance.py::run_auto_checkout, sowie bei Bedarf den Admin-Bereich).

Zustandsändernde Aktionen laufen über normale HTML-Formulare mit Server-Redirect (kein
JSON/JS nötig); Übersicht und Scan-Feedback aktualisieren sich per Polling
(app/static/app.js).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Agent, Visitor
from app.services.attendance import (
    checkin_visitor,
    checkout_person,
    presence_by_room,
)
from app.services.agents import agent_for_ip, client_ip
from app.services.feedback import latest_event
from app.services.settings import get_besucher_suche_aktiv
from app.templating import templates

router = APIRouter(tags=["kiosk"])

KIOSK_RAUM_COOKIE = "kiosk_raum"


def _resolve_raum(db: Session, raum: str) -> str | None:
    """Validiert eine vom Kiosk übermittelte Raum-Angabe gegen die vorhandenen, aktiven
    Agenten (siehe app/models.py::Agent -- ein Agent pro Technikraum). Unbekannte, leere
    oder entfernte (geloescht_am gesetzt, siehe app/services/agents.py) Werte werden zu
    None, statt beliebigen Text ins Log zu übernehmen."""
    raum = (raum or "").strip()
    if not raum:
        return None
    agent = db.get(Agent, raum)
    return raum if agent is not None and agent.geloescht_am is None else None


def _room_context(db: Session) -> dict:
    """Baut die Split-Ansicht je Technikraum für Kiosk-Startseite + Presence-Partial:
    ein Eintrag pro vorhandenem Agenten (= Raum) mit Mitarbeiterzahl (nur Punkte/Zähler,
    siehe app/services/attendance.py::PresentPerson) und externen Besuchern, plus ein
    Sammel-Eintrag für Personen ohne (mehr) gültige Raumzuordnung."""
    agenten = list(db.scalars(select(Agent).where(Agent.geloescht_am.is_(None)).order_by(Agent.agent_id)))
    anwesenheit = presence_by_room(db)

    rooms = []
    for agent in agenten:
        personen = anwesenheit.get(agent.agent_id, [])
        rooms.append(
            {
                "agent": agent,
                "mitarbeiter_anzahl": sum(1 for p in personen if p.person_type == "employee"),
                "extern": [p for p in personen if p.person_type == "visitor"],
            }
        )

    bekannte_raeume = {a.agent_id for a in agenten}
    unzugeordnet = [
        p for raum, personen in anwesenheit.items() if raum not in bekannte_raeume for p in personen
    ]
    return {
        "rooms": rooms,
        "unzugeordnet_mitarbeiter_anzahl": sum(1 for p in unzugeordnet if p.person_type == "employee"),
        "unzugeordnet_extern": [p for p in unzugeordnet if p.person_type == "visitor"],
    }


@router.get("/", response_class=HTMLResponse)
def kiosk_home(request: Request, raum: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    """Der Kiosk-Browser wird einmalig mit `?raum=<agent_id>` gestartet (siehe
    deploy/KIOSK.md); die Raum-Zuordnung dieses Kiosks landet dann in einem langlebigen
    Cookie, damit die Besucher-Maske den passenden Raum automatisch übernimmt."""
    context = _room_context(db)
    context["event"] = latest_event()
    response = templates.TemplateResponse(request, "kiosk/index.html", context)
    fester_raum = _resolve_raum(db, raum)
    if fester_raum:
        response.set_cookie(
            KIOSK_RAUM_COOKIE, fester_raum, max_age=60 * 60 * 24 * 365 * 10, httponly=True, samesite="lax"
        )
    return response


@router.post("/kiosk/auschecken/visitor/{person_id}")
def manuelles_auschecken(person_id: str, db: Session = Depends(get_db)) -> RedirectResponse:
    """Manuelles Auschecken für externe Besucher (links auf der Kiosk-Startseite) -- z.B.
    falls jemand vergessen hat, sich beim Verlassen abzumelden. Für Mitarbeiter gibt es
    diese Aktion am Kiosk bewusst nicht mehr (siehe Modul-Docstring)."""
    try:
        checkout_person(db, person_type="visitor", person_id=person_id, operator="Kiosk (manuell)")
    except ValueError:
        pass  # bereits ausgecheckt -> einfach zur Übersicht zurück
    return RedirectResponse(url="/", status_code=303)


@router.get("/kiosk/presence-partial", response_class=HTMLResponse)
def presence_partial(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(request, "kiosk/_presence.html", _room_context(db))


@router.get("/kiosk/feedback-partial", response_class=HTMLResponse)
def feedback_partial(request: Request) -> HTMLResponse:
    event = latest_event()
    return templates.TemplateResponse(request, "kiosk/_feedback.html", {"event": event})


def _search_visitors(db: Session, q: str) -> list[Visitor]:
    like = f"%{q.strip()}%"
    stmt = (
        select(Visitor)
        .where(Visitor.geloescht_am.is_(None))
        .where(
            (Visitor.vorname.ilike(like))
            | (Visitor.nachname.ilike(like))
            | (Visitor.telefonnummer.ilike(like))
        )
        .order_by(Visitor.nachname, Visitor.vorname)
        .limit(20)
    )
    return list(db.scalars(stmt))


def _find_duplicates(db: Session, vorname: str, nachname: str, telefonnummer: str) -> list[Visitor]:
    """Bestehende, nicht gelöschte Profile mit gleichem Vor-/Nachnamen (ohne Groß-/
    Kleinschreibung) oder gleicher Telefonnummer."""
    bedingung = (func.lower(Visitor.vorname) == vorname.strip().lower()) & (
        func.lower(Visitor.nachname) == nachname.strip().lower()
    )
    tel = telefonnummer.strip()
    if tel:
        bedingung = bedingung | (Visitor.telefonnummer == tel)
    stmt = select(Visitor).where(Visitor.geloescht_am.is_(None)).where(bedingung).limit(5)
    return list(db.scalars(stmt))


@router.get("/kiosk/besucher", response_class=HTMLResponse)
def besucher_suche_seite(request: Request, raum: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    """Gibt es mehr als einen Technikraum (= mehr als ein Agent, siehe
    app/models.py::Agent) und wurde noch keiner gewählt, zeigt die Seite zuerst eine
    Raumauswahl (zwei große Touch-Buttons, siehe kiosk/besucher_suche.html); bei genau
    einem Raum wird er automatisch übernommen, bei keinem läuft der Checkin wie bisher
    ganz ohne Raumzuordnung."""
    kontext = _besucher_kontext(request, db, raum)
    return templates.TemplateResponse(request, "kiosk/besucher_suche.html", kontext)


def _besucher_kontext(request: Request, db: Session, raum: str, **extra) -> dict:
    agenten = list(db.scalars(select(Agent).where(Agent.geloescht_am.is_(None)).order_by(Agent.agent_id)))
    gewaehlt = db.get(Agent, raum.strip()) if raum.strip() else None
    if gewaehlt is not None and gewaehlt.geloescht_am is not None:
        gewaehlt = None

    # Fest zugeordneter Kiosk (Cookie, siehe kiosk_home): Raum automatisch übernehmen,
    # keine Auswahl und kein "Anderer Raum"-Button.
    kiosk_raum = _resolve_raum(db, request.cookies.get(KIOSK_RAUM_COOKIE, ""))
    if kiosk_raum is None:
        # Ohne Cookie: Raum über die zuletzt vom Agent gemeldete IP dieses PCs ermitteln
        # (Agent und Browser laufen auf demselben Kiosk-PC, siehe Agent.letzte_ip).
        agent_per_ip = agent_for_ip(db, client_ip(request))
        kiosk_raum = agent_per_ip.agent_id if agent_per_ip else None
    raum_fest = kiosk_raum is not None
    if gewaehlt is None and kiosk_raum:
        gewaehlt = db.get(Agent, kiosk_raum)

    if gewaehlt is None and len(agenten) > 1:
        return {"raum_wahl": True, "agenten": agenten}

    if gewaehlt is None and len(agenten) == 1:
        gewaehlt = agenten[0]

    return {
        "raum_wahl": False,
        "raum": gewaehlt.agent_id if gewaehlt else "",
        "raum_bezeichnung": gewaehlt.bezeichnung if gewaehlt else None,
        "mehrere_raeume": len(agenten) > 1 and not raum_fest,
        "suche_aktiv": get_besucher_suche_aktiv(db),
        **extra,
    }


@router.get("/kiosk/besucher/suche-partial", response_class=HTMLResponse)
def besucher_suche_partial(
    request: Request, q: str = "", raum: str = "", db: Session = Depends(get_db)
) -> HTMLResponse:
    # Auch server-seitig prüfen, nicht nur die Eingabe im Template verstecken -- eine im
    # Admin-Bereich deaktivierte Suche soll auch bei einem direkten Aufruf des Partials
    # keine Treffer mehr liefern (siehe /admin/einstellungen).
    if not get_besucher_suche_aktiv(db):
        treffer: list[Visitor] = []
    else:
        treffer = _search_visitors(db, q) if q.strip() else []
    return templates.TemplateResponse(
        request, "kiosk/_besucher_suche_ergebnisse.html", {"treffer": treffer, "q": q, "raum": raum}
    )


@router.post("/kiosk/besucher/anlegen")
def besucher_anlegen(
    request: Request,
    vorname: str = Form(...),
    nachname: str = Form(...),
    firma: str = Form(""),
    telefonnummer: str = Form(""),
    raum: str = Form(""),
    trotzdem: str = Form(""),
    db: Session = Depends(get_db),
):
    if not trotzdem:
        duplikate = _find_duplicates(db, vorname, nachname, telefonnummer)
        if duplikate:
            kontext = _besucher_kontext(
                request,
                db,
                raum,
                duplikate=duplikate,
                eingabe={
                    "vorname": vorname.strip(),
                    "nachname": nachname.strip(),
                    "firma": firma.strip(),
                    "telefonnummer": telefonnummer.strip(),
                },
            )
            return templates.TemplateResponse(request, "kiosk/besucher_suche.html", kontext)
    visitor = Visitor(
        vorname=vorname.strip(),
        nachname=nachname.strip(),
        firma=firma.strip() or None,
        telefonnummer=telefonnummer.strip() or None,
    )
    db.add(visitor)
    db.commit()
    db.refresh(visitor)
    checkin_visitor(db, visitor_id=visitor.id, raum=_resolve_raum(db, raum))
    return RedirectResponse(url="/", status_code=303)


@router.post("/kiosk/besucher/einchecken")
def besucher_einchecken(
    visitor_id: str = Form(...), raum: str = Form(""), db: Session = Depends(get_db)
) -> RedirectResponse:
    visitor = db.get(Visitor, visitor_id)
    if visitor is None or visitor.geloescht_am is not None:
        raise HTTPException(status_code=404, detail="Besucherprofil nicht gefunden")
    try:
        checkin_visitor(db, visitor_id=visitor.id, raum=_resolve_raum(db, raum))
    except ValueError:
        pass  # bereits eingecheckt -> einfach zur Übersicht zurück
    return RedirectResponse(url="/", status_code=303)
