"""Kiosk-Oberfläche: Live-Übersicht + manuelles Ein-/Auschecken für Externe.

Kein Login nötig (Konzept 3.3) — die Seite steht am Kiosk-PC vor Ort. Zustandsändernde
Aktionen laufen über normale HTML-Formulare mit Server-Redirect (kein JSON/JS nötig);
die Übersicht und das Scan-Feedback aktualisieren sich per Polling (app/static/app.js).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Visitor
from app.services.attendance import checkin_visitor, checkout_person, list_present
from app.services.feedback import latest_event
from app.templating import templates

router = APIRouter(tags=["kiosk"])


@router.get("/", response_class=HTMLResponse)
def kiosk_home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    present = list_present(db)
    return templates.TemplateResponse(request, "kiosk/index.html", {"present": present})


@router.get("/kiosk/presence-partial", response_class=HTMLResponse)
def presence_partial(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    present = list_present(db)
    return templates.TemplateResponse(request, "kiosk/_presence.html", {"present": present})


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
def besucher_suche_seite(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "kiosk/besucher_suche.html", {})


@router.get("/kiosk/besucher/suche-partial", response_class=HTMLResponse)
def besucher_suche_partial(request: Request, q: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    treffer = _search_visitors(db, q) if q.strip() else []
    return templates.TemplateResponse(request, "kiosk/_besucher_suche_ergebnisse.html", {"treffer": treffer, "q": q})


@router.post("/kiosk/besucher/anlegen")
def besucher_anlegen(
    vorname: str = Form(...),
    nachname: str = Form(...),
    firma: str = Form(""),
    telefonnummer: str = Form(""),
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
    checkin_visitor(db, visitor_id=visitor.id)
    return RedirectResponse(url="/", status_code=303)


@router.post("/kiosk/besucher/einchecken")
def besucher_einchecken(visitor_id: str = Form(...), db: Session = Depends(get_db)) -> RedirectResponse:
    visitor = db.get(Visitor, visitor_id)
    if visitor is None or visitor.geloescht_am is not None:
        raise HTTPException(status_code=404, detail="Besucherprofil nicht gefunden")
    try:
        checkin_visitor(db, visitor_id=visitor.id)
    except ValueError:
        pass  # bereits eingecheckt -> einfach zur Übersicht zurück
    return RedirectResponse(url="/", status_code=303)


@router.get("/kiosk/besucher/auschecken", response_class=HTMLResponse)
def besucher_auschecken_seite(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    present = [p for p in list_present(db) if p.person_type == "visitor"]
    return templates.TemplateResponse(request, "kiosk/besucher_auschecken.html", {"present": present})


@router.get("/kiosk/besucher/auschecken-partial", response_class=HTMLResponse)
def besucher_auschecken_partial(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    present = [p for p in list_present(db) if p.person_type == "visitor"]
    return templates.TemplateResponse(request, "kiosk/_besucher_auschecken_liste.html", {"present": present})


@router.post("/kiosk/besucher/auschecken/{visitor_id}")
def besucher_auschecken(visitor_id: str, db: Session = Depends(get_db)) -> RedirectResponse:
    try:
        checkout_person(db, person_type="visitor", person_id=visitor_id)
    except ValueError:
        pass  # bereits ausgecheckt -> einfach zur Liste zurück
    return RedirectResponse(url="/kiosk/besucher/auschecken", status_code=303)
