"""Kabelkatalog: Kabeltyp -> Außendurchmesser (aus Herstellerdatenblättern).

Die Bautagebücher nennen nur den Aufbau ('160x1x0,9'), das LV rechnet aber
nach Durchmesser ab ('D bis 25 mm', 'D über 25-40 mm'). Derselbe Aufbau
kommt in mehreren Bauarten mit unterschiedlichem Durchmesser vor, daher
liefert `durchmesser` einen Bereich.
"""
import csv
import re
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from mengen.bautagebuch import lese_pdf

KATALOG_PFAD = Path(__file__).resolve().parents[2] / "data" / "kabelkatalog.csv"
FELDER = ["hersteller", "bauart", "typ", "variante", "durchmesser_mm", "bestell_nr", "quelle"]

# Datenblatt-Zeile: Bestell-Nr., Material-Nr., 'BayRail® <Bauart> <Aufbau> <Variante>', 10 Zahlenspalten
_ZEILE = re.compile(r"^\s*(\d{7})\s+\d{8}\s+BayRail®\s+(?:CPR\s+)?(\S+)\s+(\d+x\d+x\d,\d+)\s+(.*)$")
_ZAHL = re.compile(r"(?<![\w,(])\d+(?:\.\d+)?(?![\w,)])")


@dataclass(frozen=True)
class Kabel:
    hersteller: str
    bauart: str
    typ: str
    variante: str
    durchmesser_mm: Decimal
    bestell_nr: str
    quelle: str


def lese_datenblatt(pfad: str | Path) -> list[Kabel]:
    """Produkttabelle eines Bayka-Datenblatts (PDF) auslesen."""
    return lese_zeilen("\n".join(lese_pdf(pfad)).splitlines(), Path(pfad).name)


def lese_zeilen(zeilen: list[str], quelle: str) -> list[Kabel]:
    ergebnis = []
    for zeile in zeilen:
        m = _ZEILE.match(re.sub(r"\s+", " ", zeile))
        if not m:
            continue
        bestell_nr, bauart, typ, rest = m.groups()
        rest = re.sub(r"\)(\d)", r") \1", rest)  # '(00128976)33.0' kommt ohne Leerzeichen vor
        zahlen = _ZAHL.findall(rest)
        if len(zahlen) < 10:
            continue
        # Spalten am Zeilenende: Brandlast, Kupferzahl, Außendurchmesser, Gewicht, Zug,
        # Biegeradius einm./mehrm., Regellänge, Trommelgröße, max. Fertigungslänge
        variante = re.sub(r"(\s+\d+(?:\.\d+)?){10}\s*$", "", rest)
        variante = re.sub(r"\(\d{8}\)|\(\.+\)|\b416\.\d{4}\b|\b[A-F]ca\b", "", variante)
        ergebnis.append(Kabel(
            hersteller="Bayka", bauart=bauart, typ=typ, variante=re.sub(r"\s+", " ", variante).strip(),
            durchmesser_mm=Decimal(zahlen[-8]), bestell_nr=bestell_nr, quelle=quelle,
        ))
    return ergebnis


def speichere(kabel: list[Kabel], pfad: Path = KATALOG_PFAD) -> None:
    with pfad.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(FELDER)
        for k in sorted(kabel, key=lambda k: (k.typ, k.bauart, k.variante)):
            w.writerow([getattr(k, f) for f in FELDER])


def lade(pfad: Path = KATALOG_PFAD) -> list[Kabel]:
    with pfad.open(encoding="utf-8") as f:
        return [
            Kabel(**{**z, "durchmesser_mm": Decimal(z["durchmesser_mm"])})
            for z in csv.DictReader(f, delimiter=";")
        ]


def grundtyp(typ: str) -> str:
    """'1x4x0,9S' / '200x1x0,9 S' -> '1x4x0,9': Aufbau ohne Zusatz."""
    m = re.match(r"(\d+x\d+x\d,\d+)", typ.replace(" ", ""))
    return m.group(1) if m else typ


def durchmesser(typ: str, katalog: list[Kabel], bauart: str = "") -> tuple[Decimal, Decimal] | None:
    """Kleinster und größter Außendurchmesser aller Bauarten mit diesem Aufbau.

    `bauart` grenzt ein: 'AJ' trifft alle AJ-Bauarten, 'AJ-2YOF(L)2YDB2Y' genau eine.
    """
    werte = [
        k.durchmesser_mm for k in katalog
        if k.typ == grundtyp(typ) and (not bauart or k.bauart == bauart or k.bauart.startswith(bauart + "-"))
    ]
    return (min(werte), max(werte)) if werte else None


if __name__ == "__main__":
    alle = [k for datei in sys.argv[1:] for k in lese_datenblatt(datei)]
    speichere(alle)
    print(f"{len(alle)} Kabel aus {len(sys.argv) - 1} Datenblättern -> {KATALOG_PFAD}")
