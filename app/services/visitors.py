"""DSGVO-Löschung von Besucherprofilen.

Das checklog bleibt dabei bewusst vollständig unangetastet (append-only, siehe
app/db.py) — es wird nur die `visitors`-Zeile mit den personenbezogenen Stammdaten
entfernt. Historische Log-Einträge verweisen danach auf eine nicht mehr auflösbare
person_id; Anzeige/Export zeigen dafür einen generischen Platzhalter
("gelöschtes Profil", siehe app/services/export.py) statt eines Namens.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Visitor
from app.services.attendance import is_present


class VisitorCurrentlyPresentError(Exception):
    """Wird geworfen, wenn ein Besucherprofil gelöscht werden soll, während die Person
    laut Log noch im Rechenzentrum eingecheckt ist. Erst auschecken, dann löschen —
    sonst verschwindet die Person kommentarlos aus der Live-Übersicht."""


def delete_visitor(db: Session, visitor_id: str) -> None:
    visitor = db.get(Visitor, visitor_id)
    if visitor is None:
        return
    if is_present(db, "visitor", visitor_id):
        raise VisitorCurrentlyPresentError(
            "Besucher ist aktuell eingecheckt — vor dem Löschen auschecken."
        )

    db.delete(visitor)
    db.commit()
