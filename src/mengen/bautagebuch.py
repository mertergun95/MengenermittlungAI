"""Bautagebuch-PDFs (bau-mobil / Crystal Reports) in Tagesberichte zerlegen.

Die PDFs enthalten echten Text, daher kein OCR: `pdftotext -layout` (poppler) genügt.
Ein Bericht kann über mehrere Seiten gehen ('Seite 1 von 3'); Folgeseiten
wiederholen nur den Kopf mit Datum.
"""
import re
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from mengen.schema import Tagesbericht, Taetigkeitszeile

MONATE = {
    m: i + 1
    for i, m in enumerate(
        "Januar Februar März April Mai Juni Juli August September Oktober November Dezember".split()
    )
}

_DATUM = re.compile(r"Bautagebuch\s*\n\s*\w+, (\d{1,2})\. (\w+) (\d{4})")
_SEITE = re.compile(r"Seite (\d+) von (\d+)")
_PROJEKT = re.compile(r"Projekt/Baustelle:\s*(.+?)\s*\((\d+)\)\s*$", re.M)
_GESAMT = re.compile(r"^\s*Gesamt\s+(\d+)\s+([\d.,]+)\s*Std", re.M)
_MASCHINE = re.compile(r"\d,\d\dStd\s{2,}([^\d\s].*?)\s*$")
AUFZAEHLUNG = "-–⁃•· "
_TAET_START = "ausgeführte Tätigkeiten / Sonstiges"
_TAET_ENDE = re.compile(r"Dieses Bautagebuch wurde|^SH\d{2}-\d{4}-\d{3}\s*$|bau-mobil - das mobile", re.M)


def lese_pdf(pfad: str | Path) -> list[str]:
    """Text je Seite. Benötigt `pdftotext` (Paket poppler-utils)."""
    ergebnis = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(pfad), "-"],
        capture_output=True, text=True, check=True,
    )
    seiten = ergebnis.stdout.split("\f")
    return seiten[:-1] if seiten and not seiten[-1].strip() else seiten


def lese_bautagebuch(pfad: str | Path) -> list[Tagesbericht]:
    return zerlege(lese_pdf(pfad), Path(pfad).name)


def zerlege(seiten: list[str], dokument: str) -> list[Tagesbericht]:
    """Seiten zu Tagesberichten gruppieren und Kopfdaten sowie Tätigkeiten auslesen."""
    gruppen: list[list[tuple[int, str]]] = []
    for nr, seite in enumerate(seiten, start=1):
        s = _SEITE.search(seite)
        if not gruppen or (s and s.group(1) == "1"):
            gruppen.append([])
        gruppen[-1].append((nr, seite))
    return [_bericht(g, dokument) for g in gruppen if _DATUM.search(g[0][1])]


def _datum(text: str) -> date:
    tag, monat, jahr = _DATUM.search(text).groups()
    return date(int(jahr), MONATE[monat], int(tag))


def _dezimal(text: str) -> Decimal:
    return Decimal(text.replace(".", "").replace(",", "."))


def _bericht(seiten: list[tuple[int, str]], dokument: str) -> Tagesbericht:
    erste = seiten[0][1]
    projekt = _PROJEKT.search(erste)
    gesamt = _GESAMT.search(erste)
    return Tagesbericht(
        datum=_datum(erste),
        projekt=projekt.group(1) if projekt else "",
        projekt_nr=projekt.group(2) if projekt else "",
        wetter=_wetter(erste),
        personal_anzahl=int(gesamt.group(1)) if gesamt else None,
        stunden_gesamt=_dezimal(gesamt.group(2)) if gesamt else None,
        maschinen=_maschinen(erste),
        taetigkeiten=_taetigkeiten(seiten),
        dokument=dokument,
        seiten=[nr for nr, _ in seiten],
    )


def _wetter(text: str) -> str:
    m = re.search(r"Wetter / Temperatur.*\n\s*(.+?)(?:\s{2,}|$)", text)
    return m.group(1).strip() if m else ""


def _maschinen(text: str) -> list[str]:
    a, e = text.find("Personaleinsatz"), text.find(_TAET_START)
    if a < 0:
        return []
    zeilen = text[a:e if e > a else None].splitlines()
    return [m.group(1) for z in zeilen if (m := _MASCHINE.search(z))]


def _taetigkeits_text(seite: str, ist_erste: bool) -> str:
    if ist_erste:
        a = seite.find(_TAET_START)
        if a < 0:
            return ""
        seite = seite[a + len(_TAET_START):]
    else:
        # Folgeseite: Kopf bis einschließlich Datumszeile überspringen
        m = _DATUM.search(seite)
        seite = seite[seite.find("\n", m.end()) + 1:] if m else seite
    ende = _TAET_ENDE.search(seite)
    return seite[: ende.start()] if ende else seite


def _taetigkeiten(seiten: list[tuple[int, str]]) -> list[Taetigkeitszeile]:
    zeilen: list[Taetigkeitszeile] = []
    abschnitt, block, leer = "", 0, False
    for i, (nr, seite) in enumerate(seiten):
        for roh in _taetigkeits_text(seite, i == 0).splitlines():
            text = re.sub(r"\s{2,}", " ", roh).strip()
            if not text.strip(AUFZAEHLUNG):
                leer = bool(zeilen)
                continue
            vorher = zeilen[-1] if zeilen else None
            # Umbrochene Zeile: setzt einen '-'-Punkt fort, der nicht mit Satzzeichen endet
            if vorher and text[0] not in AUFZAEHLUNG and vorher.text[0] in AUFZAEHLUNG \
                    and not re.search(r"[.:!)]$", vorher.text) and not leer:
                vorher.text += " " + text
                continue
            if leer:
                block += 1
                leer = False
            inhalt = text.lstrip(AUFZAEHLUNG).strip()
            if inhalt.endswith(":"):
                abschnitt = inhalt.rstrip(" :")
                block += 1
            zeilen.append(Taetigkeitszeile(text=text, seite=nr, abschnitt=abschnitt, block=block))
    return zeilen


if __name__ == "__main__":
    for datei in sys.argv[1:]:
        for b in lese_bautagebuch(datei):
            print(f"\n=== {b.datum:%d.%m.%Y}  {b.projekt} ({b.projekt_nr})  S. {b.seiten}"
                  f"  {b.personal_anzahl} MA / {b.stunden_gesamt} h  {', '.join(b.maschinen)}")
            for z in b.taetigkeiten:
                print(f"  [{z.seite:>2} b{z.block:<2} {z.abschnitt[:25]:<25}] {z.text}")
