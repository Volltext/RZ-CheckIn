"""Entfernen von Besucherprofilen aus der aktiven Kontaktliste.

Das Log muss zwingend lückenlos und mit Klarnamen lesbar bleiben — auch für Besucher,
die aus der Kontaktverwaltung entfernt wurden (Nachvollziehbarkeit, wer wann im
Rechenzentrum war). Deshalb ist dies bewusst KEIN Hard-Delete: die `visitors`-Zeile
bleibt bestehen, nur `geloescht_am` wird gesetzt. Kiosk-Suche, Admin-Besucherliste und
die öffentliche API blenden Profile mit gesetztem `geloescht_am` aus (siehe die
entsprechenden `geloescht_am.is_(None)`-Filter in app/routers/kiosk.py,
app/routers/api_public.py und app/routers/admin.py::besucher_liste); Log-Ansicht und
CSV-Export lösen den Namen dagegen weiterhin über die Visitor-Zeile auf (siehe
app/routers/admin.py::_log_eintraege und app/services/export.py) und zeigen ihn also
unverändert an.

Die endgültige, unwiderrufliche DSGVO-Löschung von Besucher-Stammdaten geschieht erst
automatisch nach Ablauf der Aufbewahrungsfrist, siehe app/services/retention.py::purge
(entfernt dabei auch die letzten verbleibenden Log-Einträge der Person — erst danach
verschwindet der Name endgültig aus dem System)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Visitor
from app.services.attendance import is_present


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VisitorCurrentlyPresentError(Exception):
    """Wird geworfen, wenn ein Besucherprofil entfernt werden soll, während die Person
    laut Log noch im Rechenzentrum eingecheckt ist. Erst auschecken, dann entfernen —
    sonst verschwindet die Person kommentarlos aus der Live-Übersicht."""


def delete_visitor(db: Session, visitor_id: str) -> None:
    visitor = db.get(Visitor, visitor_id)
    if visitor is None or visitor.geloescht_am is not None:
        return
    if is_present(db, "visitor", visitor_id):
        raise VisitorCurrentlyPresentError(
            "Besucher ist aktuell eingecheckt — vor dem Entfernen auschecken."
        )

    visitor.geloescht_am = _now()
    db.commit()
