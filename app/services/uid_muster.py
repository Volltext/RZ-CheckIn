"""Prüft Karten-UIDs gegen die im Admin-Bereich hinterlegte Muster-Liste
("UID-Syntax-Whitelist", siehe /admin/einstellungen).

Hintergrund: Es gibt bewusst kein Mitarbeiter-Register -- jede am Reader gescannte UID
togglet direkt Check-in/Check-out (siehe app/services/attendance.py::record_rfid_scan).
In einem Haus sind aber oft mehrere Kartensysteme im Umlauf (eigene Dienstausweise,
Karten von Fremdfirmen, Hotelkarten, Schlüsselanhänger). Die Muster-Liste ist damit die
einzige Möglichkeit, den Check-in auf die eigenen Ausweise zu begrenzen, ohne jede Karte
einzeln zu erfassen: Passt eine UID auf keines der Muster, wird der Scan nicht
protokolliert und der Kiosk zeigt "Bitte Dienstausweis vorhalten".

Muster-Syntax (bewusst simpel gehalten, keine regulären Ausdrücke -- Zielgruppe ist die
Haustechnik, nicht der Entwickler):

    *      beliebig viele Zeichen (auch keine)
    ?      genau ein Zeichen
    x, X   genau ein Zeichen, Alias für ? -- damit sich ein Muster so aufschreiben
           lässt, wie man es im Kopf hat: "12xxxxxxxxx89". Karten-UIDs sind
           Hex-Strings (0-9, A-F, siehe agent/reader_agent.py::_target_identifier),
           enthalten also nie ein echtes "x"; die Umdeutung kann keine gültige
           UID-Stelle verdecken.

Alles andere wird wörtlich verglichen, Groß-/Kleinschreibung spielt keine Rolle (UIDs
werden vor dem Vergleich ohnehin auf Großbuchstaben normalisiert, wie beim Schreiben ins
Log). Eine leere Muster-Liste bedeutet "keine Einschränkung" -- das ist der Standard und
entspricht dem Verhalten vor Einführung dieser Funktion.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Grenzen, damit der (als einzelner Text gespeicherte) Wert sicher in
# app/models.py::Setting.value passt und die Eingabe nicht versehentlich zur
# Textdatei wird.
MAX_MUSTER = 20
MAX_MUSTER_LAENGE = 64


def parse_muster(text: str) -> list[str]:
    """Zerlegt die Eingabe aus dem Admin-Formular in einzelne Muster.

    Trennzeichen sind Zeilenumbrüche, Kommas und Semikolons -- der Admin soll nicht
    raten müssen, wie die Liste aufgeschrieben wird. Leere Einträge und Dubletten
    fallen weg, die Reihenfolge bleibt erhalten."""
    muster: list[str] = []
    for teil in re.split(r"[\n\r,;]+", text or ""):
        eintrag = teil.strip().upper()
        if eintrag and eintrag not in muster:
            muster.append(eintrag)
    return muster


def format_muster(muster: list[str]) -> str:
    """Für die Anzeige im Textfeld: ein Muster pro Zeile."""
    return "\n".join(muster)


def pruefe_muster(muster: list[str]) -> str | None:
    """Gibt eine Fehlermeldung für den Admin zurück, oder None wenn alles passt."""
    if len(muster) > MAX_MUSTER:
        return f"Bitte höchstens {MAX_MUSTER} Muster angeben."
    for eintrag in muster:
        if len(eintrag) > MAX_MUSTER_LAENGE:
            return f"Das Muster «{eintrag[:20]}…» ist länger als {MAX_MUSTER_LAENGE} Zeichen."
        if " " in eintrag:
            return f"Das Muster «{eintrag}» enthält ein Leerzeichen — UIDs haben keine Leerzeichen."
    return None


@lru_cache(maxsize=128)
def _als_regex(muster: str) -> re.Pattern[str]:
    """Übersetzt ein Muster in einen regulären Ausdruck. Bewusst nicht fnmatch: dessen
    Zeichenklassen ([0-9]) wären für diese Zielgruppe eine Stolperfalle, und "x" als
    Platzhalter kennt fnmatch nicht."""
    teile = []
    for zeichen in muster:
        if zeichen == "*":
            teile.append(".*")
        elif zeichen in ("?", "X"):
            teile.append(".")
        else:
            teile.append(re.escape(zeichen))
    return re.compile("".join(teile), re.IGNORECASE)


def uid_passt(uid: str, muster: list[str]) -> bool:
    """True, wenn die UID auf mindestens eines der Muster passt. Leere Liste = alles
    erlaubt (siehe Modul-Docstring)."""
    if not muster:
        return True
    kandidat = (uid or "").strip().upper()
    return any(_als_regex(eintrag).fullmatch(kandidat) is not None for eintrag in muster)
