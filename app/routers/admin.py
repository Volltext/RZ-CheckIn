"""Verwaltungsbereich: Login-geschützt, separat vom Kiosk (Konzept 3.4).

Mitarbeiterkarten registrieren/zuordnen, Besucherprofile verwalten (inkl. Entfernen aus
der aktiven Kontaktliste per Soft-Delete, siehe app/services/visitors.py),
Log rein lesend einsehen + CSV-Export, Agenten verwalten (Entfernen ebenfalls per
Soft-Delete, siehe app/services/agents.py), eigenes Passwort ändern.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_auth_provider
from app.db import get_db
from app.models import Agent, AdminUser, CheckLog, Employee, UnknownScan, Visitor
from app.security import (
    check_admin_ip_allowlist,
    create_session_token,
    generate_agent_api_key,
    get_current_admin,
    hash_agent_api_key,
    hash_password,
    verify_password,
)
from app.services.attendance import checkout_person, is_present
from app.services.export import export_checklog_csv
from app.services.settings import (
    get_auto_checkout_hours,
    get_besucher_suche_aktiv,
    set_auto_checkout_hours,
    set_besucher_suche_aktiv,
)
from app.services.agents import delete_agent
from app.services.visitors import VisitorCurrentlyPresentError, delete_visitor
from app.templating import templates
from app.config import get_settings

settings = get_settings()

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(check_admin_ip_allowlist)])


@router.get("/", response_class=HTMLResponse)
def admin_index() -> RedirectResponse:
    return RedirectResponse(url="/admin/besucher", status_code=303)


# --- Login/Logout -----------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/login.html", {"fehler": None})


@router.post("/login", response_model=None)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    admin = get_auth_provider().authenticate(db, username, password)
    if admin is None:
        return templates.TemplateResponse(
            request,
            "admin/login.html",
            {"fehler": "Benutzername oder Passwort ist falsch."},
            status_code=401,
        )
    response = RedirectResponse(url="/admin/besucher", status_code=303)
    response.set_cookie(
        settings.session_cookie_name,
        create_session_token(admin.id),
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(settings.session_cookie_name)
    return response


# --- Mitarbeiter -------------------------------------------------------------


@router.get("/mitarbeiter", response_class=HTMLResponse)
def mitarbeiter_liste(
    request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)
) -> HTMLResponse:
    mitarbeiter = list(db.scalars(select(Employee).order_by(Employee.erstellt_am)))
    anwesend = {
        m.id: is_present(db, "employee", m.id) for m in mitarbeiter
    }
    unbekannte_karten = list(db.scalars(select(UnknownScan).order_by(UnknownScan.zuletzt_gesehen.desc())))
    return templates.TemplateResponse(
        request,
        "admin/mitarbeiter.html",
        {"admin": admin, "mitarbeiter": mitarbeiter, "anwesend": anwesend, "unbekannte_karten": unbekannte_karten},
    )


@router.post("/mitarbeiter/anlegen")
def mitarbeiter_anlegen(
    rfid_uid: str = Form(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RedirectResponse:
    """Legt einen neuen Mitarbeiter-Eintrag direkt mit Dienstausweisnummer an -- es gibt
    bewusst kein Namensfeld mehr (siehe app/models.py::Employee)."""
    uid = rfid_uid.strip().upper()
    bestehend = db.scalar(select(Employee).where(Employee.rfid_uid == uid))
    if bestehend is not None:
        raise HTTPException(status_code=409, detail="Diese Dienstausweisnummer ist bereits vergeben")
    db.add(Employee(rfid_uid=uid, aktiv=True))
    db.commit()
    unknown = db.get(UnknownScan, uid)
    if unknown is not None:
        db.delete(unknown)
        db.commit()
    return RedirectResponse(url="/admin/mitarbeiter", status_code=303)


@router.post("/mitarbeiter/{employee_id}/auschecken")
def mitarbeiter_auschecken(
    employee_id: str, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)
) -> RedirectResponse:
    """Manuelles Auschecken durch den Admin -- am Kiosk selbst geht das für Mitarbeiter
    bewusst nicht mehr (keine Namen mehr in der Live-Übersicht, siehe app/routers/kiosk.py)."""
    try:
        checkout_person(
            db, person_type="employee", person_id=employee_id, operator=f"Admin ({admin.username})"
        )
    except ValueError:
        pass  # bereits ausgecheckt -> einfach zur Liste zurück
    return RedirectResponse(url="/admin/mitarbeiter", status_code=303)


@router.post("/mitarbeiter/{employee_id}/karte-zuordnen")
def mitarbeiter_karte_zuordnen(
    employee_id: str,
    rfid_uid: str = Form(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RedirectResponse:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Mitarbeiter nicht gefunden")
    uid = rfid_uid.strip().upper()
    bestehend = db.scalar(select(Employee).where(Employee.rfid_uid == uid))
    if bestehend is not None and bestehend.id != employee.id:
        raise HTTPException(status_code=409, detail="Karte ist bereits einem anderen Mitarbeiter zugeordnet")
    employee.rfid_uid = uid
    unknown = db.get(UnknownScan, uid)
    if unknown is not None:
        db.delete(unknown)
    db.commit()
    return RedirectResponse(url="/admin/mitarbeiter", status_code=303)


@router.post("/mitarbeiter/{employee_id}/karte-entfernen")
def mitarbeiter_karte_entfernen(
    employee_id: str, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)
) -> RedirectResponse:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Mitarbeiter nicht gefunden")
    employee.rfid_uid = None
    db.commit()
    return RedirectResponse(url="/admin/mitarbeiter", status_code=303)


@router.post("/mitarbeiter/{employee_id}/aktiv")
def mitarbeiter_aktiv_setzen(
    employee_id: str,
    aktiv: bool = Form(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RedirectResponse:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Mitarbeiter nicht gefunden")
    employee.aktiv = aktiv
    db.commit()
    return RedirectResponse(url="/admin/mitarbeiter", status_code=303)


@router.post("/unbekannte-karten/{uid}/zuordnen")
def unbekannte_karte_zuordnen(
    uid: str,
    employee_id: str = Form(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RedirectResponse:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Mitarbeiter nicht gefunden")
    bestehend = db.scalar(select(Employee).where(Employee.rfid_uid == uid))
    if bestehend is not None and bestehend.id != employee.id:
        raise HTTPException(status_code=409, detail="Karte ist bereits einem anderen Mitarbeiter zugeordnet")
    employee.rfid_uid = uid
    unknown = db.get(UnknownScan, uid)
    if unknown is not None:
        db.delete(unknown)
    db.commit()
    return RedirectResponse(url="/admin/mitarbeiter", status_code=303)


@router.post("/unbekannte-karten/{uid}/loeschen")
def unbekannte_karte_loeschen(
    uid: str, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)
) -> RedirectResponse:
    unknown = db.get(UnknownScan, uid)
    if unknown is not None:
        db.delete(unknown)
        db.commit()
    return RedirectResponse(url="/admin/mitarbeiter", status_code=303)


# --- Besucherprofile ---------------------------------------------------------


@router.get("/besucher", response_class=HTMLResponse)
def besucher_liste(
    request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)
) -> HTMLResponse:
    besucher = list(
        db.scalars(
            select(Visitor).where(Visitor.geloescht_am.is_(None)).order_by(Visitor.nachname, Visitor.vorname)
        )
    )
    return templates.TemplateResponse(request, "admin/besucher.html", {"admin": admin, "besucher": besucher})


@router.post("/besucher/{visitor_id}/bearbeiten")
def besucher_bearbeiten(
    visitor_id: str,
    vorname: str = Form(...),
    nachname: str = Form(...),
    firma: str = Form(""),
    telefonnummer: str = Form(""),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> RedirectResponse:
    visitor = db.get(Visitor, visitor_id)
    if visitor is None:
        raise HTTPException(status_code=404, detail="Besucherprofil nicht gefunden")
    visitor.vorname = vorname.strip()
    visitor.nachname = nachname.strip()
    visitor.firma = firma.strip() or None
    visitor.telefonnummer = telefonnummer.strip() or None
    db.commit()
    return RedirectResponse(url="/admin/besucher", status_code=303)


@router.post("/besucher/{visitor_id}/loeschen", response_model=None)
def besucher_loeschen(
    request: Request,
    visitor_id: str,
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> HTMLResponse | RedirectResponse:
    try:
        delete_visitor(db, visitor_id)
    except VisitorCurrentlyPresentError as exc:
        besucher = list(
            db.scalars(
                select(Visitor)
                .where(Visitor.geloescht_am.is_(None))
                .order_by(Visitor.nachname, Visitor.vorname)
            )
        )
        return templates.TemplateResponse(
            request,
            "admin/besucher.html",
            {"admin": admin, "besucher": besucher, "fehler": str(exc)},
            status_code=409,
        )
    return RedirectResponse(url="/admin/besucher", status_code=303)


# --- Log-Ansicht + Export -----------------------------------------------------


def _parse_datum(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@router.get("/log", response_class=HTMLResponse)
def log_ansicht(
    request: Request,
    von: str = "",
    bis: str = "",
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> HTMLResponse:
    von_dt = _parse_datum(von)
    bis_dt = _parse_datum(bis)
    return templates.TemplateResponse(
        request,
        "admin/log.html",
        {"admin": admin, "eintraege": _log_eintraege(db, von_dt, bis_dt), "von": von, "bis": bis},
    )


def _log_eintraege(db: Session, von_dt: datetime | None, bis_dt: datetime | None) -> list[dict]:
    query = select(CheckLog).order_by(CheckLog.timestamp.desc()).limit(500)
    if von_dt is not None:
        query = query.where(CheckLog.timestamp >= von_dt)
    if bis_dt is not None:
        query = query.where(CheckLog.timestamp <= bis_dt)

    # Für Mitarbeiter gibt es keinen Namen -- im Log/Export erscheint stattdessen die
    # Dienstausweisnummer (die einzige gespeicherte Kennung, siehe app/models.py::Employee).
    employees = {e.id: (e.rfid_uid or "(ohne Kartennummer)") for e in db.scalars(select(Employee))}
    visitors = {v.id: (v.voller_name, v.firma) for v in db.scalars(select(Visitor))}
    # Raum kommt (wie bei Mitarbeitern/Besuchern) nur als Anzeige-Auflösung dazu -- der
    # Log-Eintrag selbst speichert nur die Agent-ID (siehe app/models.py::CheckLog.raum),
    # kein FK. Wird ein Agent später gelöscht/umbenannt, bleibt der Log-Eintrag
    # unverändert; hier wird dafür nur ein Platzhalter angezeigt (append-only, siehe
    # app/db.py -- Löschen eines Agenten/Besuchers ändert am Log nie etwas).
    agenten = {a.agent_id: a.bezeichnung for a in db.scalars(select(Agent))}

    eintraege = []
    for entry in db.scalars(query):
        if entry.person_type == "employee":
            name = employees.get(entry.person_id, "(gelöschter Mitarbeiter-Eintrag)")
            firma = None
        else:
            name, firma = visitors.get(entry.person_id, ("(gelöschtes Profil)", None))
        if entry.raum is None:
            raum = None
        else:
            raum = agenten.get(entry.raum, "(gelöschter Raum)")
        eintraege.append({"entry": entry, "name": name, "firma": firma, "raum": raum})
    return eintraege


@router.get("/log/export.csv")
def log_export(
    von: str = "",
    bis: str = "",
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> StreamingResponse:
    von_dt = _parse_datum(von)
    bis_dt = _parse_datum(bis)
    dateiname = f"rz-checkin-log_{datetime.now(timezone.utc):%Y%m%d-%H%M}.csv"
    return StreamingResponse(
        export_checklog_csv(db, von=von_dt, bis=bis_dt),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{dateiname}"'},
    )


# --- Agenten -------------------------------------------------------------------


@router.get("/agenten", response_class=HTMLResponse)
def agenten_liste(
    request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)
) -> HTMLResponse:
    agenten = list(
        db.scalars(select(Agent).where(Agent.geloescht_am.is_(None)).order_by(Agent.agent_id))
    )
    return templates.TemplateResponse(request, "admin/agenten.html", {"admin": admin, "agenten": agenten})


@router.post("/agenten/anlegen", response_class=HTMLResponse)
def agent_anlegen(
    request: Request,
    agent_id: str = Form(...),
    bezeichnung: str = Form(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> HTMLResponse:
    agent_id = agent_id.strip()
    # db.get() findet auch bereits entfernte (Soft-Delete) Agenten -- die Prüfung
    # verhindert damit zugleich, dass eine agent_id nach dem Entfernen erneut vergeben
    # wird und bestehende Log-Einträge dadurch auf einen anderen Raum umgedeutet werden
    # (siehe app/services/agents.py).
    if db.get(Agent, agent_id) is not None:
        raise HTTPException(status_code=409, detail="Agent-ID bereits vergeben")
    api_key = generate_agent_api_key()
    db.add(Agent(agent_id=agent_id, bezeichnung=bezeichnung.strip(), api_key_hash=hash_agent_api_key(api_key)))
    db.commit()
    return templates.TemplateResponse(
        request, "admin/agent_erstellt.html", {"admin": admin, "agent_id": agent_id, "api_key": api_key}
    )


@router.post("/agenten/{agent_id}/loeschen")
def agent_loeschen(
    agent_id: str, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)
) -> RedirectResponse:
    delete_agent(db, agent_id)
    return RedirectResponse(url="/admin/agenten", status_code=303)


# --- Eigenes Passwort ---------------------------------------------------------


@router.get("/passwort", response_class=HTMLResponse)
def passwort_form(
    request: Request, admin: AdminUser = Depends(get_current_admin)
) -> HTMLResponse:
    return templates.TemplateResponse(request, "admin/passwort.html", {"admin": admin, "fehler": None, "erfolg": False})


@router.post("/passwort", response_class=HTMLResponse)
def passwort_aendern(
    request: Request,
    aktuelles_passwort: str = Form(...),
    neues_passwort: str = Form(...),
    neues_passwort_wiederholen: str = Form(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> HTMLResponse:
    fehler = None
    if not verify_password(aktuelles_passwort, admin.password_hash):
        fehler = "Aktuelles Passwort ist falsch."
    elif len(neues_passwort) < 8:
        fehler = "Neues Passwort muss mindestens 8 Zeichen lang sein."
    elif neues_passwort != neues_passwort_wiederholen:
        fehler = "Die Wiederholung stimmt nicht mit dem neuen Passwort überein."

    if fehler:
        return templates.TemplateResponse(
            request, "admin/passwort.html", {"admin": admin, "fehler": fehler, "erfolg": False}, status_code=400
        )

    admin.password_hash = hash_password(neues_passwort)
    db.commit()
    return templates.TemplateResponse(request, "admin/passwort.html", {"admin": admin, "fehler": None, "erfolg": True})


# --- Einstellungen -------------------------------------------------------------


@router.get("/einstellungen", response_class=HTMLResponse)
def einstellungen_form(
    request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(get_current_admin)
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin/einstellungen.html",
        {
            "admin": admin,
            "auto_checkout_stunden": get_auto_checkout_hours(db),
            "besucher_suche_aktiv": get_besucher_suche_aktiv(db),
            "erfolg": False,
        },
    )


@router.post("/einstellungen", response_class=HTMLResponse)
def einstellungen_speichern(
    request: Request,
    auto_checkout_stunden: int = Form(...),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> HTMLResponse:
    fehler = None
    if auto_checkout_stunden < 0:
        fehler = "Bitte 0 (deaktiviert) oder eine positive Stundenzahl angeben."

    if fehler:
        return templates.TemplateResponse(
            request,
            "admin/einstellungen.html",
            {
                "admin": admin,
                "auto_checkout_stunden": get_auto_checkout_hours(db),
                "besucher_suche_aktiv": get_besucher_suche_aktiv(db),
                "fehler": fehler,
                "erfolg": False,
            },
            status_code=400,
        )

    set_auto_checkout_hours(db, auto_checkout_stunden)
    return templates.TemplateResponse(
        request,
        "admin/einstellungen.html",
        {
            "admin": admin,
            "auto_checkout_stunden": auto_checkout_stunden,
            "besucher_suche_aktiv": get_besucher_suche_aktiv(db),
            "erfolg": True,
        },
    )


@router.post("/einstellungen/besucher-suche", response_class=HTMLResponse)
def einstellungen_besucher_suche_speichern(
    request: Request,
    aktiv: bool = Form(False),
    db: Session = Depends(get_db),
    admin: AdminUser = Depends(get_current_admin),
) -> HTMLResponse:
    """Checkbox-Formular: nicht angehakt sendet das Feld gar nicht mit, daher
    Form(False) als Default statt Form(...)."""
    set_besucher_suche_aktiv(db, aktiv)
    return templates.TemplateResponse(
        request,
        "admin/einstellungen.html",
        {
            "admin": admin,
            "auto_checkout_stunden": get_auto_checkout_hours(db),
            "besucher_suche_aktiv": aktiv,
            "erfolg": True,
        },
    )
