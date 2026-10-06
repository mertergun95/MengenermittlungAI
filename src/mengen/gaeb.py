"""GAEB DA XML (X83 Leistungsverzeichnis, X84 Angebot) einlesen.

X83 liefert Texte, Einheiten und LV-Mengen, X84 nur OZ und Preise.
`verbinde` führt beide über die OZ zusammen.
"""
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from pathlib import Path

from mengen.schema import LV, LVPosition


def _name(el: ET.Element) -> str:
    """Tag ohne Namespace (GAEB-Namespace ändert sich je Version und DA)."""
    return el.tag.rsplit("}", 1)[-1]


def _kind(el: ET.Element, name: str) -> ET.Element | None:
    return next((k for k in el if _name(k) == name), None)


def _finde(el: ET.Element, *pfad: str) -> ET.Element | None:
    for name in pfad:
        if el is None:
            return None
        el = _kind(el, name)
    return el


def _wert(el: ET.Element, *pfad: str) -> str:
    ziel = _finde(el, *pfad)
    return (ziel.text or "").strip() if ziel is not None else ""


def _dezimal(text: str) -> Decimal | None:
    try:
        return Decimal(text) if text else None
    except InvalidOperation:
        return None


def _text(el: ET.Element | None) -> str:
    """Formatierten GAEB-Text (<p>, <span>, <br/>) in Klartext mit Zeilenumbrüchen wandeln."""
    if el is None:
        return ""
    zeilen = [""]

    def lauf(e: ET.Element) -> None:
        if _name(e) == "br" or (_name(e) == "p" and zeilen[-1]):
            zeilen.append("")
        if e.text and e.text.strip():
            zeilen[-1] += e.text
        for k in e:
            lauf(k)
            if k.tail and k.tail.strip():
                zeilen[-1] += k.tail

    lauf(el)
    return "\n".join(z.strip() for z in zeilen).strip()


def _kurztext(item: ET.Element) -> str:
    beschr = _kind(item, "Description")
    if beschr is None:
        return ""
    for pfad in (("CompleteText", "OutlineText"), ("OutlineText",)):
        outl = _finde(beschr, *pfad)
        if outl is not None:
            return _text(_finde(outl, "OutlTxt", "TextOutlTxt"))
    return ""


def _langtext(item: ET.Element) -> str:
    return _text(_finde(item, "Description", "CompleteText", "DetailTxt", "Text"))


def lese_lv(pfad: str | Path) -> LV:
    """Liest eine GAEB-XML-Datei (X81–X86) und liefert alle Positionen mit voller OZ."""
    wurzel = ET.parse(pfad).getroot()
    projekt = _wert(wurzel, "PrjInfo", "NamePrj")
    award = _kind(wurzel, "Award")
    boq = _kind(award, "BoQ")
    positionen: list[LVPosition] = []

    def durchlaufe(body: ET.Element, oz_teile: list[str], titel: list[str]) -> None:
        for el in body:
            if _name(el) == "BoQCtgy":
                titelname = _text(_kind(el, "LblTx"))
                durchlaufe(
                    _kind(el, "BoQBody"),
                    oz_teile + [el.get("RNoPart", "")],
                    titel + ([titelname] if titelname else []),
                )
            elif _name(el) == "Itemlist":
                for item in el:
                    if _name(item) == "Item":
                        positionen.append(_position(item, oz_teile, titel, projekt))

    durchlaufe(_kind(boq, "BoQBody"), [], [])
    return LV(
        projekt=projekt,
        bezeichnung=_wert(wurzel, "PrjInfo", "LblPrj"),
        datenart=_wert(award, "DP"),
        positionen=positionen,
    )


def _position(item: ET.Element, oz_teile: list[str], titel: list[str], projekt: str) -> LVPosition:
    oz = ".".join(oz_teile + [item.get("RNoPart", "")])
    if item.get("RNoIndex"):
        oz += item.get("RNoIndex")
    menge = _dezimal(_wert(item, "Qty"))
    ep = _dezimal(_wert(item, "UP"))
    gp = _dezimal(_wert(item, "IT"))
    # X84 enthält keine Menge: aus GP / EP zurückrechnen
    if menge is None and ep and gp is not None:
        menge = (gp / ep).quantize(Decimal("0.001"))
    return LVPosition(
        oz=oz,
        kurztext=_kurztext(item),
        langtext=_langtext(item),
        abschnitt=" > ".join(titel),
        einheit=_wert(item, "QU") or None,
        menge_lv=menge,
        ep=ep,
        gp=gp,
        lv=projekt,
    )


def verbinde(texte: LV, preise: LV) -> LV:
    """X83 (Texte, Mengen) mit X84 (Preise) über die OZ zusammenführen."""
    nach_oz = {p.oz: p for p in preise.positionen}
    fehlend = [p.oz for p in texte.positionen if p.oz not in nach_oz]
    if fehlend:
        raise ValueError(f"OZ ohne Preis in {preise.projekt}: {', '.join(fehlend)}")
    positionen = [
        p.model_copy(update={"ep": nach_oz[p.oz].ep, "gp": nach_oz[p.oz].gp})
        for p in texte.positionen
    ]
    return texte.model_copy(update={"positionen": positionen})


def vereinige(*lvs: LV) -> list[LVPosition]:
    """Haupt-LV und Nachträge zu einer Positionsliste zusammenfassen (OZ muss eindeutig sein)."""
    positionen = [p for lv in lvs for p in lv.positionen]
    gesehen: set[str] = set()
    doppelt = {p.oz for p in positionen if p.oz in gesehen or gesehen.add(p.oz)}
    if doppelt:
        raise ValueError(f"OZ mehrfach vergeben: {', '.join(sorted(doppelt))}")
    return positionen


def _zahl(d: Decimal | None) -> str:
    return "" if d is None else f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


if __name__ == "__main__":
    for datei in sys.argv[1:]:
        lv = lese_lv(datei)
        print(f"\n{lv.projekt} – {lv.bezeichnung} (X{lv.datenart}, {len(lv.positionen)} Positionen)")
        for p in lv.positionen:
            print(f"  {p.oz:<14} {_zahl(p.menge_lv):>12} {p.einheit or '':<3} {_zahl(p.ep):>8}  {p.kurztext}")
