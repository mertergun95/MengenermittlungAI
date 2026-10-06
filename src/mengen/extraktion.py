"""Regelbasierte Extraktion: Tätigkeitszeilen -> Leistungseinträge.

Erkennt die häufigen, fast strukturierten Muster der Bautagebücher:
Kabelblöcke (Bezeichnung, Typ, Trommel, Anfang/Ende, Länge), Kabelkanal-
und Schachtarbeiten. Alles andere bleibt als 'unerkannt' für LLM bzw. Mensch.

Abrechnungsregeln, die hier schon greifen:
- Kanal 'schließen' wird nicht gezählt (das 'öffnen' derselben Strecke zählt).
- Enthält ein Bericht eine Tagessumme für Schächte, zählen die Einzelangaben
  in den Kabelblöcken nicht zusätzlich.
"""
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field

from mengen.bautagebuch import AUFZAEHLUNG, lese_bautagebuch
from mengen.schema import Leistungseintrag, Quelle, Tagesbericht, Taetigkeitszeile

TOLERANZ_M = Decimal("2")

_I = re.IGNORECASE
_TYP = r"(\d+\s*[x×]\s*\d+\s*[x×]\s*\d+(?:[.,]\d+)?[A-Za-z]*)"
KABEL_START = re.compile(r"^(?:Kabelbezeichnung|Kabel)\s*:?\s*(S\s?\d[\w-]*|\d+(?:-\d+)+)\s*(?:/\s*" + _TYP + r"\.?)?\s*$", _I)
KABEL_START_LWL = re.compile(r"^Kabel\s+((?:DB\s+\w+\s+)?LWL.*?)\.?$", _I)
KABELTYP = re.compile(r"^Kabeltyp\s*:?\s*(?:([A-Z]+)\s*-\s*)?" + _TYP, _I)
TROMMEL = re.compile(r"^Trommel(?:nummer)?\s*[:/]\s*([A-Z]*\d+\w*)\s*(?:\((.*?)\))?", _I)
ANFANG = re.compile(r"^(?:Kabelanfang|Anfangs?stand|Anfang)\s*[:/]\s*(\d+)(?:\s*m\b)?\s*(.*)$", _I)
ENDE = re.compile(r"^(?:Kabelende|Endstand(?:\s*/\s*Teilverlegung[^:]*)?|Ende)\s*[:/]\s*(\d+)(?:\s*m\b)?\s*(.*)$", _I)
LAENGE = re.compile(r"^(?:Summe|Gesamtlänge|Länge)\s*[:/]?\s*([\d.,]+)\s*m?\.?$", _I)
DURCH_SCHACHT = re.compile(r"^Kabel durch (\d+)\s*[x×]?\s*Sch[aä]cht", _I)
DURCH_ROHR = re.compile(r"^Kabel durch ([\d.,]+)\s*m\s*Rohrtrasse", _I)
TAGESSUMME_KABEL = re.compile(r"^Gesamte Kabelverlegung\s*:?\s*([\d.,]+)\s*m", _I)
SUMMEN_INFO = re.compile(r"^(?:Rohrtrasse(?: gesamt)?|Schächte gesamt durchfahren)\s*:", _I)

KANAL_WORT = re.compile(r"\b(BKK|GFK|KK|U-Kanal)\b|\d\s*er\b|Kabelkanal", _I)
KM_BEREICH = re.compile(r"\(?\s*Km\s*(-?\s*[\d.,]+)\s*-\s*(-?\s*[\d.,]+)\s*\)?", _I)
LAENGE_IN_TEXT = re.compile(r"(?<![\d.,])(\d[\d.,]*)\s*m(?![a-zA-Zäöü²³])")
SCHACHT_WORT = re.compile(r"sch[aä]cht", _I)
# '6× öffnen', 'öffnen und schließen: 6', '16 Schächte', '15x großer Schacht'
SCHACHT_ANZAHL = re.compile(
    r"(\d+)\s*[x×]|[x×]\s*(\d+)\b|:\s*(\d+)\s*[x×]?\s*$|^(\d+)\s+(?:sch[aä]cht|gro|klei)", _I
)
SCHACHT_MASS = re.compile(r"\bGr(?:öße|\.)?\s*(\d+\s*[x×]\s*\d+)", _I)
SCHACHT_IN_LISTE = re.compile(r"^(?:Sch[aä]cht\w*\s*)?\(?(groß|klein)\)?\s*:?\s*\d+\s*[x×]?\s*$", _I)
REINIGEN = re.compile(r"Kabelkanal\s+(?:innen\s+)?(?:gereinigt|reinigen)\s*([\d.,]+)\s*m", _I)
KEIN_KANAL = re.compile(r"flexrohr|rohrtrasse|anbindung", _I)
OEFFNEN = re.compile(r"öffn|geöffnet|auf-/zu", _I)
SCHLIESSEN = re.compile(r"schließ|geschlossen|auf-/zu", _I)


def zahl(text: str) -> Decimal | None:
    """Deutsche Zahl: '1.884' -> 1884, '722,00' -> 722.00, '0.9' -> 0.9."""
    t = text.strip().rstrip(".").replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _typ(text: str) -> str:
    """'30 × 1 × 0.9' / '30x1x09' -> '30x1x0,9'."""
    t = re.sub(r"\s+", "", text).replace("×", "x").replace(".", ",").rstrip(",")
    return re.sub(r"x0(\d)([A-Za-z]*)$", r"x0,\1\2", t)


class Kabel(BaseModel):
    bezeichnung: str = ""
    typ: str = ""
    bauart: str = ""
    trommel: str = ""
    anfang: int | None = None
    anfang_ort: str = ""
    ende: int | None = None
    ende_ort: str = ""
    laenge: Decimal | None = None
    teilverlegung: bool = False
    umverlegt: bool = False
    schaechte: int | None = None
    rohrtrasse_m: Decimal | None = None
    zeilen: list[Taetigkeitszeile] = Field(default_factory=list)
    notizen: list[str] = Field(default_factory=list)


class Kanal(BaseModel):
    art: str = "Kabelkanal"
    groesse: int | None = None
    lage: str = ""
    laenge: Decimal
    km_von: Decimal | None = None
    km_bis: Decimal | None = None
    vorgang: str = ""
    aus_summe: bool = False
    zeile: Taetigkeitszeile


class Schacht(BaseModel):
    groesse: str = ""
    anzahl: int
    aus_summe: bool = False
    kabel: str = ""
    zeile: Taetigkeitszeile


class Tagesauswertung(BaseModel):
    bericht: Tagesbericht
    kabel: list[Kabel] = []
    kanal: list[Kanal] = []
    schacht: list[Schacht] = []
    reinigung: list[Kanal] = []
    tagessumme_kabel: Decimal | None = None
    erkannt: int = 0
    unerkannt: list[Taetigkeitszeile] = []
    hinweise: list[str] = []


def werte_aus(bericht: Tagesbericht) -> Tagesauswertung:
    a = Tagesauswertung(bericht=bericht)
    kabel: Kabel | None = None
    kontext = ""  # 'kanal:<vorgang>' oder 'schacht' aus der letzten Überschrift

    def schliesse() -> None:
        nonlocal kabel
        if kabel is not None:
            a.kabel.append(kabel)
            kabel = None

    def neues_kabel(z: Taetigkeitszeile) -> Kabel:
        schliesse()
        return Kabel(zeilen=[z])

    for z in bericht.taetigkeiten:
        t = z.text.lstrip(AUFZAEHLUNG).strip()
        erkannt = True
        uhrzeit = bool(re.search(r"\bUhr\b|\d:\d\d", t))

        if m := KABEL_START.match(t):
            kabel = neues_kabel(z)
            kabel.umverlegt = "umverleg" in z.abschnitt.lower()
            kabel.bezeichnung = m.group(1).replace(" ", "")
            kabel.typ = _typ(m.group(2)) if m.group(2) else ""
            kontext = ""
        elif m := KABEL_START_LWL.match(t):
            kabel = neues_kabel(z)
            kabel.typ = re.sub(r"LWL\s*", "LWL ", re.sub(r"\s+", " ", m.group(1)))
            kontext = ""
        elif m := KABELTYP.match(t):
            if kabel is None or kabel.typ:
                kabel = neues_kabel(z)
            else:
                kabel.zeilen.append(z)
            kabel.typ = _typ(m.group(2))
            kabel.bauart = (m.group(1) or "").upper()
        elif m := TROMMEL.match(t):
            if kabel is None or kabel.trommel:
                kabel = neues_kabel(z)
            else:
                kabel.zeilen.append(z)
            kabel.trommel = m.group(1)
            if m.group(2) and not kabel.typ and re.search(_TYP, m.group(2)):
                kabel.typ = _typ(re.search(_TYP, m.group(2)).group(1))
        elif not uhrzeit and (m := ANFANG.match(t)):
            if kabel is None or kabel.anfang is not None:
                kabel = neues_kabel(z)
            else:
                kabel.zeilen.append(z)
            kabel.anfang, kabel.anfang_ort = int(m.group(1)), m.group(2).strip(" ().")
        elif not uhrzeit and (m := ENDE.match(t)):
            if kabel is None or kabel.ende is not None:
                kabel = neues_kabel(z)
            else:
                kabel.zeilen.append(z)
            kabel.ende, kabel.ende_ort = int(m.group(1)), m.group(2).strip(" ().")
            kabel.teilverlegung = "teilverlegung" in t.lower()
        elif (m := LAENGE.match(t)) and kabel is not None:
            kabel.laenge = zahl(m.group(1))
            kabel.zeilen.append(z)
        elif (m := DURCH_SCHACHT.match(t)) and kabel is not None:
            kabel.schaechte = int(m.group(1))
            kabel.zeilen.append(z)
        elif (m := DURCH_ROHR.match(t)) and kabel is not None:
            kabel.rohrtrasse_m = zahl(m.group(1))
            kabel.zeilen.append(z)
        elif m := TAGESSUMME_KABEL.match(t):
            a.tagessumme_kabel = zahl(m.group(1))
        elif SUMMEN_INFO.match(t):
            pass
        elif m := REINIGEN.search(t):
            a.reinigung.append(Kanal(laenge=zahl(m.group(1)), vorgang="reinigen", zeile=z))
        elif kontext == "schacht" and SCHACHT_IN_LISTE.match(t):
            m = SCHACHT_IN_LISTE.match(t)
            a.schacht.append(Schacht(groesse=m.group(1).lower(), anzahl=int(re.findall(r"\d+", t)[0]),
                                     aus_summe=True, zeile=z))
        elif SCHACHT_WORT.search(t) and OEFFNEN.search(t) and not t.lower().startswith("kabel durch"):
            mass = SCHACHT_MASS.search(t)
            anzahl = SCHACHT_ANZAHL.search(SCHACHT_MASS.sub(" ", t))
            if anzahl:
                eintrag = _schacht(t, int(next(g for g in anzahl.groups() if g)), z, kontext, kabel)
                if mass:
                    eintrag.groesse = re.sub(r"\s+", "", mass.group(1)).replace("×", "x")
                a.schacht.append(eintrag)
                if kabel is not None:
                    kabel.zeilen.append(z)
            elif not re.search(r"\d", t):
                kontext = "schacht"
                schliesse()
            else:
                erkannt = False
        elif KANAL_WORT.search(t) and not KEIN_KANAL.search(t) \
                and (OEFFNEN.search(t) or SCHLIESSEN.search(t) or kontext.startswith("kanal")):
            eintrag = _kanal(t, z, kontext)
            if eintrag is not None:
                a.kanal.append(eintrag)
            elif not re.search(r"\d\s*m\b", t):
                kontext = "kanal:" + _vorgang(t)
                schliesse()
            else:
                erkannt = False
        else:
            erkannt = False
            if t.endswith(":"):
                kontext = ""
                schliesse()
            elif kabel is not None:
                kabel.notizen.append(t)

        if erkannt:
            a.erkannt += 1
        else:
            a.unerkannt.append(z)
    schliesse()
    _pruefe(a)
    return a


def _schacht(t: str, anzahl: int, z: Taetigkeitszeile, kontext: str, kabel: Kabel | None) -> Schacht:
    groesse = re.search(r"(groß|klein)", t, _I)
    summe = kontext == "schacht" or "gesamt" in t.lower() or "insgesamt" in z.abschnitt.lower() \
        or bool(re.match(r"^Schächte", t))
    return Schacht(
        groesse=groesse.group(1).lower() if groesse else "",
        anzahl=anzahl,
        aus_summe=summe and kabel is None,
        kabel=kabel.bezeichnung if kabel else "",
        zeile=z,
    )


def _vorgang(t: str) -> str:
    o, s = bool(OEFFNEN.search(t)), bool(SCHLIESSEN.search(t))
    return "öffnen und schließen" if o and s else "öffnen" if o else "schließen" if s else ""


def _kanal(t: str, z: Taetigkeitszeile, kontext: str) -> Kanal | None:
    km = KM_BEREICH.search(t)
    ohne_km = KM_BEREICH.sub(" ", t)
    laenge = LAENGE_IN_TEXT.search(ohne_km)
    if not laenge:
        return None
    art = re.search(r"\b(BKK|GFK|KK|U-Kanal)\b", t, _I)
    groesse = re.search(r"(\d)\s*-?\s*er\b", t)
    lage = re.search(r"(innen|auf)liegend", t, _I)
    vorgang = _vorgang(ohne_km) or kontext.removeprefix("kanal:")
    return Kanal(
        art=art.group(1) if art else "Kabelkanal",
        groesse=int(groesse.group(1)) if groesse else None,
        lage=lage.group(0).lower() if lage else "",
        laenge=zahl(laenge.group(1)),
        km_von=zahl(km.group(1)) if km else None,
        km_bis=zahl(km.group(2)) if km else None,
        vorgang=vorgang or "öffnen und schließen",
        aus_summe="gesamt" in t.lower(),
        zeile=z,
    )


def _pruefe(a: Tagesauswertung) -> None:
    for k in a.kabel:
        name = k.bezeichnung or k.trommel or "?"
        if k.anfang is not None and k.ende is not None:
            diff = Decimal(abs(k.ende - k.anfang))
            if k.laenge is None:
                k.laenge = diff
                k.notizen.append(f"Länge aus Meterangaben berechnet: |{k.ende} - {k.anfang}| = {diff} m")
            elif abs(diff - k.laenge) > TOLERANZ_M:
                a.hinweise.append(f"Kabel {name}: Länge {k.laenge} m ≠ |Ende {k.ende} - Anfang {k.anfang}| = {diff} m")
        if k.laenge is None:
            a.hinweise.append(f"Kabel {name}: keine Länge")
        if not k.typ:
            a.hinweise.append(f"Kabel {name}: kein Kabeltyp")
    for kn in a.kanal:
        if kn.km_von is not None and kn.km_bis is not None:
            strecke = abs(kn.km_bis - kn.km_von) * 1000
            if abs(strecke - kn.laenge) > TOLERANZ_M:
                a.hinweise.append(f"Kanal {kn.laenge} m ≠ Km-Bereich {kn.km_von}–{kn.km_bis} ({strecke:.0f} m): {kn.zeile.text}")
    einzeln = [kn for kn in a.kanal if not kn.aus_summe and kn.vorgang != "schließen"]
    for kn in (kn for kn in a.kanal if kn.aus_summe):
        if einzeln and abs(sum(e.laenge for e in einzeln) - kn.laenge) > TOLERANZ_M:
            a.hinweise.append(f"Kanal-Tagessumme {kn.laenge} m ≠ Summe der Einzelangaben "
                              f"{sum(e.laenge for e in einzeln)} m")
    if a.tagessumme_kabel is not None:
        summe = sum((k.laenge or 0) for k in a.kabel)
        if abs(summe - a.tagessumme_kabel) > TOLERANZ_M:
            a.hinweise.append(f"Tagessumme Kabel {a.tagessumme_kabel} m ≠ Summe der Kabel {summe} m")


def leistungen(a: Tagesauswertung) -> list[Leistungseintrag]:
    """Abrechnungsrelevante Einträge eines Tages, jeweils mit Quelle."""
    b = a.bericht

    def quelle(zeilen: list[Taetigkeitszeile]) -> Quelle:
        return Quelle(dokument=b.dokument, seite=zeilen[0].seite, zitat="\n".join(z.text for z in zeilen))

    ergebnis = []
    for k in a.kabel:
        if k.laenge is None:
            continue
        ergebnis.append(Leistungseintrag(
            datum=b.datum, ort=f"{k.anfang_ort} → {k.ende_ort}".strip(" →"),
            taetigkeit="Kabel umverlegen" if k.umverlegt else "Kabel einziehen",
            material=k.typ, menge=k.laenge, einheit="m",
            merkmale={x: str(v) for x, v in {
                "bezeichnung": k.bezeichnung, "kabeltyp": k.typ, "bauart": k.bauart, "trommel": k.trommel,
                "anfang": k.anfang, "ende": k.ende,
                "teilverlegung": "ja" if k.teilverlegung else "",
            }.items() if v},
            hinweise=k.notizen, quelle=quelle(k.zeilen),
        ))
    kanal_einzeln = any(not kn.aus_summe for kn in a.kanal)
    for kn in a.kanal:
        if kn.vorgang == "schließen" or (kn.aus_summe and kanal_einzeln):
            continue
        ergebnis.append(Leistungseintrag(
            datum=b.datum, km_von=kn.km_von, km_bis=kn.km_bis,
            taetigkeit="Kabelkanal öffnen und schließen", material=kn.art,
            menge=kn.laenge, einheit="m",
            merkmale={x: str(v) for x, v in {"art": kn.art, "groesse": kn.groesse, "lage": kn.lage}.items() if v},
            quelle=quelle([kn.zeile]),
        ))
    for r in a.reinigung:
        ergebnis.append(Leistungseintrag(
            datum=b.datum, taetigkeit="Kabelkanal reinigen", menge=r.laenge, einheit="m",
            quelle=quelle([r.zeile]),
        ))
    hat_summe = any(s.aus_summe for s in a.schacht)
    for s in a.schacht:
        if hat_summe and not s.aus_summe:
            continue
        ergebnis.append(Leistungseintrag(
            datum=b.datum, taetigkeit="Schacht öffnen und schließen", menge=Decimal(s.anzahl), einheit="St",
            merkmale={x: v for x, v in {"groesse": s.groesse, "kabel": s.kabel}.items() if v},
            quelle=quelle([s.zeile]),
        ))
    return ergebnis


def _zusammenfassung(auswertungen: list[Tagesauswertung]) -> None:
    zeilen = sum(a.erkannt + len(a.unerkannt) for a in auswertungen)
    erkannt = sum(a.erkannt for a in auswertungen)
    eintraege = [e for a in auswertungen for e in leistungen(a)]
    print(f"{len(auswertungen)} Berichte, {zeilen} Zeilen, davon {erkannt} erkannt ({erkannt / max(zeilen, 1):.0%})")
    print(f"{len(eintraege)} Leistungseinträge\n")

    summen: dict[tuple, Decimal] = defaultdict(Decimal)
    for e in eintraege:
        if e.taetigkeit == "Kabel einziehen":
            merkmal = e.merkmale.get("kabeltyp", "")
        elif e.taetigkeit.startswith("Kabelkanal"):
            g = e.merkmale.get("groesse")
            merkmal = " ".join(filter(None, [e.merkmale.get("art"), g and g + "er", e.merkmale.get("lage")]))
        else:
            merkmal = e.merkmale.get("groesse", "")
        summen[(e.taetigkeit, merkmal, e.einheit)] += e.menge
    for (taet, merkmal, einheit), menge in sorted(summen.items()):
        print(f"  {taet:<32} {merkmal or '-':<28} {menge:>10,.0f} {einheit}")

    kanal_bilanz: dict[str, Decimal] = defaultdict(Decimal)
    for a in auswertungen:
        for kn in a.kanal:
            schluessel = f"{kn.art} {kn.groesse or ''}er {kn.lage}".replace(" er", "")
            if kn.vorgang in ("öffnen", "öffnen und schließen"):
                kanal_bilanz[schluessel] += kn.laenge
            if kn.vorgang in ("schließen", "öffnen und schließen"):
                kanal_bilanz[schluessel] -= kn.laenge
    offen = {k: v for k, v in kanal_bilanz.items() if v}
    if offen:
        print("\nKanal geöffnet minus geschlossen (≠ 0 → prüfen):")
        for k, v in sorted(offen.items()):
            print(f"  {k:<30} {v:>8,.0f} m")

    hinweise = [(a.bericht.datum, h) for a in auswertungen for h in a.hinweise]
    print(f"\n{len(hinweise)} Hinweise:")
    for d, h in hinweise:
        print(f"  {d:%d.%m.%Y}  {h}")

    muster = Counter(
        re.sub(r"\d+([.,]\d+)?", "#", z.text.lstrip(AUFZAEHLUNG)) for a in auswertungen for z in a.unerkannt
    )
    print("\nHäufigste unerkannte Zeilen:")
    for text, n in muster.most_common(15):
        print(f"  {n:>3}× {text[:100]}")


if __name__ == "__main__":
    _zusammenfassung([werte_aus(b) for datei in sys.argv[1:] for b in lese_bautagebuch(datei)])
