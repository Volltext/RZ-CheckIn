"""Kiosk-Oberfläche: Live-Übersicht, Ein-/Auschecken für Externe, Registrierung neuer
Dienstausweise mit noch unbekannter Karte.

Kein Login nötig (Konzept 3.3) — die Seite steht am Kiosk-PC vor Ort. Wer bis zum Reader
vordringt, darf ohnehin ins Rechenzentrum; deshalb dürfen sich Mitarbeiter hier auch
selbst mit ihrer Karte registrieren, statt zwingend über den Admin-Bereich zu müssen.
Für Mitarbeiter wird dabei bewusst NUR die Dienstausweisnummer gespeichert -- kein Name,
keine Verknüpfung zu einer Person (Fachvorgabe, siehe app/models.py::Employee). Deshalb
zeigt die Live-Übersicht für Mitarbeiter auch keine Namen/Zeilen mehr, nur die Anzahl der
aktuell Anwesenden; ein manuelles Auschecken einzelner Mitarbeiter über den Kiosk entfällt
damit (dafür gibt es das automatische Auschecken nach Zeitablauf, siehe
app/services/attendance.py::run_auto_checkout, sowie bei Bedarf den Admin-Bereich).

Zustandsändernde Aktionen laufen über normale HTML-Formulare mit Server-Redirect (kein
JSON/JS nötig); Übersicht und Scan-Feedback aktualisieren sich per Polling
(app/static/app.js).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Agent, CheckLog, Employee, UnknownScan, Visitor
from app.services.attendance import (
    checkin_visitor,
    checkout_person,
    presence_by_room,
)
from app.services.feedback import latest_event, push_event
from app.templating import templates

router = APIRouter(tags=["kiosk"])


def _resolve_raum(db: Session, raum: str) -> str | None:
    """Validiert eine vom Kiosk übermittelte Raum-Angabe gegen die vorhandenen Agenten
    (siehe app/models.py::Agent -- ein Agent pro Technikraum). Unbekannte/leere Werte
    werden zu None, statt beliebigen Text ins Log zu übernehmen."""
    raum = (raum or "").strip()
    if not raum:
        return None
    return raum if db.get(Agent, raum) is not None else None


def _room_context(db: Session) -> dict:
    """Baut die Split-Ansicht je Technikraum für Kiosk-Startseite + Presence-Partial:
    ein Eintrag pro vorhandenem Agenten (= Raum) mit Mitarbeiterzahl (nur Punkte/Zähler,
    siehe app/services/attendance.py::PresentPerson) und externen Besuchern, plus ein
    Sammel-Eintrag für Personen ohne (mehr) gültige Raumzuordnung."""
    agenten = list(db.scalars(select(Agent).order_by(Agent.agent_id)))
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
def kiosk_home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    context = _room_context(db)
    context["event"] = latest_event()
    return templates.TemplateResponse(request, "kiosk/index.html", context)


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


@router.post("/kiosk/mitarbeiter/registrieren")
def mitarbeiter_karte_registrieren(
    rfid_uid: str = Form(...),
    raum: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Nach einem Scan einer unbekannten Karte (siehe kiosk/_feedback.html) kann die
    Karte per Knopfdruck direkt am Kiosk als neuer Dienstausweis registriert werden --
    ohne Namenseingabe, es wird ausschließlich die Kartennummer gespeichert. `raum` kommt
    als verstecktes Feld aus dem Scan-Event mit (Agent-ID des Readers, der die
    unbekannte Karte gesehen hat, siehe app/services/feedback.py::ScanFeedbackEvent)."""
    uid = rfid_uid.strip().upper()

    existing = db.scalar(select(Employee).where(Employee.rfid_uid == uid))
    if existing is not None:
        # Karte wurde zwischenzeitlich schon zugeordnet (Admin oder ein zweiter
        # Registrierungsversuch für dieselbe Karte) -- kein Duplikat anlegen.
        push_event("conflict", "Diese Karte ist bereits einem Mitarbeiter zugeordnet.")
        return RedirectResponse(url="/", status_code=303)

    employee = Employee(rfid_uid=uid, aktiv=True)
    db.add(employee)
    db.flush()
    db.add(
        CheckLog(
            person_type="employee",
            person_id=employee.id,
            action="checkin",
            source="manual",
            raum=_resolve_raum(db, raum),
        )
    )

    unknown = db.get(UnknownScan, uid)
    if unknown is not None:
        db.delete(unknown)

    db.commit()
    push_event("checkin")
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


@router.get("/kiosk/besucher", response_class=HTMLResponse)
def besucher_suche_seite(request: Request, raum: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    """Gibt es mehr als einen Technikraum (= mehr als ein Agent, siehe
    app/models.py::Agent) und wurde noch keiner gewählt, zeigt die Seite zuerst eine
    Raumauswahl (zwei große Touch-Buttons, siehe kiosk/besucher_suche.html); bei genau
    einem Raum wird er automatisch übernommen, bei keinem läuft der Checkin wie bisher
    ganz ohne Raumzuordnung."""
    agenten = list(db.scalars(select(Agent).order_by(Agent.agent_id)))
    gewaehlt = db.get(Agent, raum.strip()) if raum.strip() else None

    if gewaehlt is None and len(agenten) > 1:
        return templates.TemplateResponse(
            request, "kiosk/besucher_suche.html", {"raum_wahl": True, "agenten": agenten}
        )

    if gewaehlt is None and len(agenten) == 1:
        gewaehlt = agenten[0]

    return templates.TemplateResponse(
        request,
        "kiosk/besucher_suche.html",
        {
            "raum_wahl": False,
            "raum": gewaehlt.agent_id if gewaehlt else "",
            "raum_bezeichnung": gewaehlt.bezeichnung if gewaehlt else None,
            "mehrere_raeume": len(agenten) > 1,
        },
    )


@router.get("/kiosk/besucher/suche-partial", response_class=HTMLResponse)
def besucher_suche_partial(
    request: Request, q: str = "", raum: str = "", db: Session = Depends(get_db)
) -> HTMLResponse:
    treffer = _search_visitors(db, q) if q.strip() else []
    return templates.TemplateResponse(
        request, "kiosk/_besucher_suche_ergebnisse.html", {"treffer": treffer, "q": q, "raum": raum}
    )


@router.post("/kiosk/besucher/anlegen")
def besucher_anlegen(
    vorname: str = Form(...),
    nachname: str = Form(...),
    firma: str = Form(""),
    telefonnummer: str = Form(""),
    raum: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
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
