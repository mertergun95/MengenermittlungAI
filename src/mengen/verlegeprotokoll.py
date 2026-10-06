"""Kabelverlegeprotokolle (Excel) einlesen und mit den Bautagebüchern abgleichen.

Das Protokoll ist die formale Quelle je Kabel (vollständiger Kabeltyp mit Bauart,
Trommel, Meterangaben, SOLL/IST, Bemerkung). Das Bautagebuch bestätigt es.
Abgleich über die Kabelnummer.
"""
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import openpyxl
from pydantic import BaseModel, Field

from mengen.kabelkatalog import Kabel as Katalogkabel
from mengen.kabelkatalog import durchmesser, grundtyp, lade
from mengen.schema import Leistungseintrag, Quelle

TOLERANZ_M = Decimal("2")

SPALTEN = {
    "kabel nr.": "kabel_nr", "datum": "datum", "kabeltyp": "kabeltyp", "trommel nr.": "trommel",
    "von bahnkm.": "km_von", "bis bahnkm.": "km_bis", "von ort": "ort_von", "bis ort": "ort_bis",
    "kabelanfang": "anfang", "kabelende": "ende", "soll": "soll", "ist": "ist",
    "verlegeart hand/maschinel": "verlegeart", "bemerkungen/besonderheiten": "bemerkung",
}


class Verlegung(BaseModel):
    """Eine Zeile im Kabelverlegeprotokoll."""

    kabel_nr: str
    datum: date | None = None
    kabeltyp: str = ""
    bauart: str = ""
    typ: str = ""
    trommel: str = ""
    km_von: Decimal | None = None
    km_bis: Decimal | None = None
    ort_von: str = ""
    ort_bis: str = ""
    anfang: int | None = None
    ende: int | None = None
    soll: Decimal | None = None
    ist: Decimal | None = None
    verlegeart: str = ""
    bemerkung: str = ""
    dokument: str
    blatt: str
    zeile: int
    hinweise: list[str] = Field(default_factory=list)


def kabelnummer(text: str) -> str:
    """'s 2501-5-9 ' / '2501-5-9' -> 'S2501-5-9'."""
    t = re.sub(r"\s+", "", str(text)).upper()
    return t if t.startswith("S") else "S" + t


def trommelnummer(text: str) -> str:
    """'T180 535945' / '128 05 6197' / 'T220227033' -> nur Ziffern und Buchstaben ohne T-Präfix."""
    t = re.sub(r"\s+", "", str(text or "")).upper()
    return t[1:] if re.fullmatch(r"T\d+", t) else t


def _kopf(wert) -> str:
    return re.sub(r"\s+", " ", str(wert or "")).strip().lower()


def _dezimal(wert) -> Decimal | None:
    if wert is None or wert == "":
        return None
    try:
        return Decimal(str(wert).replace(",", "."))
    except ArithmeticError:
        return None


def lese_protokoll(pfad: str | Path) -> list[Verlegung]:
    """Alle Blätter einer Protokoll-Datei; Kopfzeile wird je Blatt über 'Kabel Nr.' gesucht."""
    wb = openpyxl.load_workbook(pfad, data_only=True, read_only=True)
    ergebnis = []
    for ws in wb.worksheets:
        spalten: dict[int, str] | None = None
        for nr, zeile in enumerate(ws.iter_rows(values_only=True), start=1):
            if zeile and _kopf(zeile[0]) == "kabel nr.":
                spalten = {i: SPALTEN[_kopf(w)] for i, w in enumerate(zeile) if _kopf(w) in SPALTEN}
                continue
            if spalten is None or not zeile or not zeile[0] or _kopf(zeile[0]) == "aussteller":
                continue
            werte = {feld: zeile[i] for i, feld in spalten.items() if i < len(zeile)}
            ergebnis.append(_verlegung(werte, Path(pfad).name, ws.title.strip(), nr))
    return ergebnis


def _verlegung(w: dict, dokument: str, blatt: str, nr: int) -> Verlegung:
    kabeltyp = re.sub(r"\s+", " ", str(w.get("kabeltyp") or "")).replace("–", "-").strip()
    m = re.match(r"(.*?)\s*(\d+\s*x\s*\d+\s*x\s*\d+[.,]\d+)", kabeltyp)
    datum = w.get("datum")
    v = Verlegung(
        kabel_nr=kabelnummer(w["kabel_nr"]),
        datum=datum.date() if isinstance(datum, datetime) else datum if isinstance(datum, date) else None,
        kabeltyp=kabeltyp,
        bauart=m.group(1).replace(" ", "") if m else "",
        typ=grundtyp(m.group(2).replace(" ", "").replace(".", ",")) if m else "",
        trommel=trommelnummer(w.get("trommel")),
        km_von=_dezimal(w.get("km_von")),
        km_bis=_dezimal(w.get("km_bis")),
        ort_von=str(w.get("ort_von") or "").strip(),
        ort_bis=str(w.get("ort_bis") or "").strip(),
        anfang=int(w["anfang"]) if isinstance(w.get("anfang"), (int, float)) else None,
        ende=int(w["ende"]) if isinstance(w.get("ende"), (int, float)) else None,
        soll=_dezimal(w.get("soll")),
        ist=_dezimal(w.get("ist")),
        verlegeart=str(w.get("verlegeart") or "").strip(),
        bemerkung=re.sub(r"\s+", " ", str(w.get("bemerkung") or "")).strip(),
        dokument=dokument, blatt=blatt, zeile=nr,
    )
    _pruefe(v)
    return v


def _pruefe(v: Verlegung) -> None:
    if v.ist is None:
        v.hinweise.append("keine IST-Länge")
        return
    if v.anfang is not None and v.ende is not None:
        diff = Decimal(abs(v.ende - v.anfang))
        if abs(diff - v.ist) > TOLERANZ_M:
            v.hinweise.append(f"IST {v.ist} m ≠ |Ende {v.ende} - Anfang {v.anfang}| = {diff} m")
    # SOLL ist die Planlänge: Abweichungen nach unten sind Teilverlegungen und normal,
    # deutlich mehr als geplant ist für die Abrechnung prüfenswert.
    if v.soll and v.ist > v.soll * Decimal("1.1") + TOLERANZ_M:
        v.hinweise.append(f"IST {v.ist} m deutlich über SOLL {v.soll} m")
    if not v.bauart:
        v.hinweise.append("Bauart fehlt im Kabeltyp")
    if v.bemerkung:
        v.hinweise.append(f"Bemerkung: {v.bemerkung}")


# --- Abgleich mit dem Bautagebuch -------------------------------------------------

class Abgleich(BaseModel):
    kabel_nr: str
    protokoll: list[Verlegung] = []
    btb: list[Leistungseintrag] = []
    abweichungen: list[str] = []

    @property
    def status(self) -> str:
        if not self.btb:
            return "nur Protokoll"
        if not self.protokoll:
            return "nur Bautagebuch"
        return "Abweichung" if self.abweichungen else "bestätigt"


def gleiche_ab(protokoll: list[Verlegung], btb: list[Leistungseintrag]) -> list[Abgleich]:
    """Kabel aus Protokoll und Bautagebuch über die Kabelnummer zusammenführen.

    Bautagebuch-Kabel außerhalb des Protokollzeitraums bleiben unberücksichtigt.
    """
    datumswerte = [v.datum for v in protokoll if v.datum]
    von, bis = (min(datumswerte), max(datumswerte)) if datumswerte else (date.min, date.max)
    gruppen: dict[str, Abgleich] = {}
    for v in protokoll:
        gruppen.setdefault(v.kabel_nr, Abgleich(kabel_nr=v.kabel_nr)).protokoll.append(v)
    for e in btb:
        name = e.merkmale.get("bezeichnung")
        if e.taetigkeit != "Kabel einziehen" or not name:
            continue
        nr = kabelnummer(name)
        if nr in gruppen or (e.datum and von <= e.datum <= bis):
            gruppen.setdefault(nr, Abgleich(kabel_nr=nr)).btb.append(e)
    _verbinde_tippfehler(gruppen)
    for a in gruppen.values():
        _doppelte_btb(a)
        if a.protokoll and a.btb:
            _vergleiche(a)
    return sorted(gruppen.values(), key=lambda a: a.kabel_nr)


def _verbinde_tippfehler(gruppen: dict[str, Abgleich]) -> None:
    """'nur Protokoll' und 'nur Bautagebuch' mit gleichem Datum und gleicher Länge zusammenlegen."""
    nur_p = [a for a in gruppen.values() if a.protokoll and not a.btb]
    nur_b = [a for a in gruppen.values() if a.btb and not a.protokoll]
    for p in nur_p:
        for b in nur_b:
            gleich = any(
                v.datum == e.datum and v.ist is not None and e.menge is not None and abs(v.ist - e.menge) <= TOLERANZ_M
                for v in p.protokoll for e in b.btb
            )
            if gleich and b.kabel_nr in gruppen:
                p.btb = b.btb
                p.abweichungen.append(f"Kabelnummer: Protokoll {p.kabel_nr}, Bautagebuch {b.kabel_nr} "
                                      "(gleiches Datum und gleiche Länge, vermutlich Tippfehler)")
                del gruppen[b.kabel_nr]
                break


def _doppelte_btb(a: Abgleich) -> None:
    """Dasselbe Kabelstück (gleiche Meterangaben) mehrfach im Bautagebuch -> nur einmal zählen."""
    gesehen: dict[tuple, Leistungseintrag] = {}
    eindeutig = []
    for e in a.btb:
        schluessel = (e.merkmale.get("anfang"), e.merkmale.get("ende"))
        if None not in schluessel and schluessel in gesehen:
            erst = gesehen[schluessel]
            a.abweichungen.append(f"doppelt im Bautagebuch: {erst.datum:%d.%m.} und {e.datum:%d.%m.} "
                                  f"({schluessel[0]}–{schluessel[1]}), einmal gezählt")
            continue
        gesehen[schluessel] = e
        eindeutig.append(e)
    a.btb = eindeutig


def _vergleiche(a: Abgleich) -> None:
    ist = sum((v.ist or 0) for v in a.protokoll)
    btb = sum((e.menge or 0) for e in a.btb)
    if abs(ist - btb) > TOLERANZ_M:
        a.abweichungen.append(f"Länge: Protokoll {ist} m, Bautagebuch {btb} m")
    trommeln_p = {v.trommel for v in a.protokoll if v.trommel}
    trommeln_b = {trommelnummer(e.merkmale["trommel"]) for e in a.btb if e.merkmale.get("trommel")}
    if trommeln_b and trommeln_p and not trommeln_b <= trommeln_p:
        a.abweichungen.append(f"Trommel: Protokoll {sorted(trommeln_p)}, Bautagebuch {sorted(trommeln_b)}")
    typen_p = {v.typ for v in a.protokoll if v.typ}
    typen_b = {grundtyp(e.merkmale["kabeltyp"]) for e in a.btb if e.merkmale.get("kabeltyp")}
    if typen_b and typen_p and not typen_b <= typen_p:
        a.abweichungen.append(f"Kabeltyp: Protokoll {sorted(typen_p)}, Bautagebuch {sorted(typen_b)}")
    daten_p = {v.datum for v in a.protokoll}
    daten_b = {e.datum for e in a.btb}
    if not daten_p & daten_b:
        a.abweichungen.append(
            "Datum: Protokoll " + ", ".join(f"{d:%d.%m.}" for d in sorted(filter(None, daten_p)))
            + ", Bautagebuch " + ", ".join(f"{d:%d.%m.}" for d in sorted(filter(None, daten_b)))
        )


def kabel_leistungen(abgleiche: list[Abgleich], katalog: list[Katalogkabel]) -> list[Leistungseintrag]:
    """Kabelmengen: Protokoll (IST) hat Vorrang, Bautagebuch nur für Kabel ohne Protokoll.

    Merkmal 'durchmesser_mm' kommt aus dem Katalog, eingegrenzt auf die Bauart des Protokolls.
    """
    ergebnis = []
    for a in abgleiche:
        for v in a.protokoll:
            if v.ist is None:
                continue
            d = durchmesser(v.typ, katalog, v.bauart)
            ergebnis.append(Leistungseintrag(
                datum=v.datum, km_von=v.km_von, km_bis=v.km_bis, ort=f"{v.ort_von} → {v.ort_bis}".strip(" →"),
                taetigkeit="Kabel einziehen", material=v.kabeltyp, menge=v.ist, einheit="m",
                merkmale={k: x for k, x in {
                    "bezeichnung": v.kabel_nr, "kabeltyp": v.typ, "bauart": v.bauart, "trommel": v.trommel,
                    "durchmesser_mm": _bereich(d), "abgleich": a.status,
                }.items() if x},
                hinweise=v.hinweise + a.abweichungen,
                quelle=Quelle(dokument=v.dokument, seite=None, zitat=f"{v.blatt}, Zeile {v.zeile}: {v.kabel_nr} "
                              f"{v.kabeltyp} {v.anfang}–{v.ende} IST {v.ist} m"),
            ))
        if not a.protokoll:
            for e in a.btb:
                d = durchmesser(e.merkmale.get("kabeltyp", ""), katalog, e.merkmale.get("bauart", ""))
                ergebnis.append(e.model_copy(update={
                    "merkmale": {**e.merkmale, "abgleich": a.status, **({"durchmesser_mm": _bereich(d)} if d else {})},
                    "hinweise": e.hinweise + ["nicht im Verlegeprotokoll"],
                }))
    return ergebnis


def _bereich(d: tuple[Decimal, Decimal] | None) -> str:
    if d is None:
        return ""
    return f"{d[0]}" if d[0] == d[1] else f"{d[0]}–{d[1]}"


def _bericht(protokoll: list[Verlegung], btb_pdf: list[str]) -> None:
    print(f"{len(protokoll)} Protokollzeilen, {len({v.kabel_nr for v in protokoll})} Kabel, "
          f"IST gesamt {sum(v.ist or 0 for v in protokoll):,.0f} m")
    hinweise = [v for v in protokoll if v.hinweise]
    teil = [v for v in protokoll if v.soll and v.ist is not None and v.ist < v.soll - TOLERANZ_M]
    print(f"{len(teil)} Teilverlegungen (IST < SOLL)")
    print(f"\n{len(hinweise)} Protokollzeilen mit Hinweisen:")
    for v in hinweise:
        print(f"  {v.kabel_nr:<14} {v.dokument[-21:-5]} {v.blatt:<22} Z.{v.zeile:<3} {'; '.join(v.hinweise)[:140]}")
    if not btb_pdf:
        return
    from mengen.bautagebuch import lese_bautagebuch
    from mengen.extraktion import leistungen, werte_aus
    btb = [e for p in btb_pdf for b in lese_bautagebuch(p) for e in leistungen(werte_aus(b))]
    abgleiche = gleiche_ab(protokoll, btb)
    status = defaultdict(list)
    for a in abgleiche:
        status[a.status].append(a)
    print("\nAbgleich mit Bautagebuch:")
    for s in ("bestätigt", "Abweichung", "nur Protokoll", "nur Bautagebuch"):
        print(f"  {s:<16} {len(status[s]):>4} Kabel")
    for s in ("Abweichung", "nur Protokoll", "nur Bautagebuch"):
        if status[s]:
            print(f"\n{s}:")
        for a in status[s]:
            info = "; ".join(a.abweichungen) or ", ".join(
                f"{e.datum:%d.%m.} {e.menge} m" for e in a.btb) or ", ".join(
                f"{v.datum:%d.%m.} {v.ist} m" for v in a.protokoll)
            print(f"  {a.kabel_nr:<14} {info[:150]}")


if __name__ == "__main__":
    dateien = [a for a in sys.argv[1:] if a.lower().endswith(".xlsx")]
    pdfs = [a for a in sys.argv[1:] if a.lower().endswith(".pdf")]
    _bericht([v for d in dateien for v in lese_protokoll(d)], pdfs)
