from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from mengen.bautagebuch import zerlege
from mengen.extraktion import _typ, leistungen, werte_aus, zahl
from mengen.schema import Tagesbericht, Taetigkeitszeile

FIX = Path(__file__).parent / "fixtures"


def bericht(text: str) -> Tagesbericht:
    """Tagesbericht aus Zeilen; Leerzeilen trennen Blöcke wie im PDF."""
    zeilen, block = [], 0
    for z in text.strip().splitlines():
        if not z.strip():
            block += 1
            continue
        zeilen.append(Taetigkeitszeile(text=z.strip(), seite=1, block=block))
    return Tagesbericht(datum=date(2026, 9, 1), taetigkeiten=zeilen, dokument="test.pdf", seiten=[1])


def nach_taetigkeit(eintraege, taetigkeit):
    return [e for e in eintraege if e.taetigkeit == taetigkeit]


@pytest.mark.parametrize("roh, erwartet", [("1.884", "1884"), ("722,00", "722.00"), ("1.030,5", "1030.5"), ("15", "15")])
def test_zahl(roh, erwartet):
    assert zahl(roh) == Decimal(erwartet)


@pytest.mark.parametrize(
    "roh, erwartet",
    [("10×4×1,4", "10x4x1,4"), ("200x1x09", "200x1x0,9"), ("30 x 1 x 0.9", "30x1x0,9"), ("1x4x0,9S", "1x4x0,9S")],
)
def test_kabeltyp(roh, erwartet):
    assert _typ(roh) == erwartet


def test_riehen_kabelbloecke():
    seiten = (FIX / "riehen_auszug.txt").read_text(encoding="utf-8").split("\f")
    tag = next(b for b in zerlege(seiten, "riehen.pdf") if b.datum == date(2025, 8, 27))
    a = werte_aus(tag)
    assert [(k.bezeichnung, k.typ, k.laenge) for k in a.kabel] == [
        ("S1140", "50x1x1,4", Decimal("444.00")),
        ("S1140", "50x1x1,4", Decimal("529.00")),
        ("S1115", "80x1x1,4", Decimal("994.00")),
        ("S1135", "50x1x1,4", Decimal("539.00")),
    ]
    assert a.kabel[0].anfang_ort == "Muffe Km 2,108" and a.kabel[0].schaechte == 2
    assert a.hinweise == []
    schacht = nach_taetigkeit(leistungen(a), "Schacht öffnen und schließen")
    assert sum(e.menge for e in schacht) == 2 + 4 + 7


def test_basel_format_mit_tagessumme():
    a = werte_aus(bericht("""
        Kabelverlegung 08.09.
        Kabelbezeichnung: S2501
        Trommelnummer: DYD220S300668
        Kabeltyp: 160x1x0,9
        Anfangsstand: 3426 ESTW
        Endstand: 3619 Muffenstelle B4
        Gesamtlänge: 193m
        Kabel durch 1x Schacht verlegt.
        Schacht groß öffnen und schließen: 1
        ⁃
        Kabelbezeichnung: S2506-18
        Trommelnummer: 128039907
        Kabeltyp: AJ - 1x4x0,9
        Anfangsstand: 6135 W366/W375
        Endstand: 5929 KS250
        Gesamtlänge: 206m
        Schacht klein öffnen und schließen: 3

        Kabelkanal öffnen und schließen
        BKK (1er) aufliegend: 51m
        BKK (2er) innenliegend: 144m
        Kabelkanal (BKK) gesamt: 195 m
        Schächte öffnen und schließen
        Groß: 6x
        Klein: 5x
        Gesamte Kabelverlegung: 399m
        Arbeitszeit Anfang: 5:30 Uhr
    """))
    assert [(k.bezeichnung, k.typ, k.ende_ort, k.laenge) for k in a.kabel] == [
        ("S2501", "160x1x0,9", "Muffenstelle B4", Decimal("193")),
        ("S2506-18", "1x4x0,9", "KS250", Decimal("206")),
    ]
    assert a.hinweise == []
    e = leistungen(a)
    # Tagessumme Kanal nicht doppelt, Einzelangaben zählen
    assert [(x.merkmale, x.menge) for x in nach_taetigkeit(e, "Kabelkanal öffnen und schließen")] == [
        ({"art": "BKK", "groesse": "1", "lage": "aufliegend"}, Decimal("51")),
        ({"art": "BKK", "groesse": "2", "lage": "innenliegend"}, Decimal("144")),
    ]
    # Tagessumme Schächte ersetzt die Angaben in den Kabelblöcken
    assert [(x.merkmale["groesse"], x.menge) for x in nach_taetigkeit(e, "Schacht öffnen und schließen")] == [
        ("groß", 6), ("klein", 5)
    ]
    kabel = nach_taetigkeit(e, "Kabel einziehen")[0]
    assert kabel.quelle.seite == 1 and "Kabelbezeichnung: S2501" in kabel.quelle.zitat


def test_kanal_schliessen_zaehlt_nicht_und_km_pruefung():
    a = werte_aus(bericht("""
        - Kabelkanal öffnen :
        - BKK (2er) innenliegend 177,00m (Km 397,095 - 397,272).
        - Kabelkanal schließen :
        - BKK (2er) innenliegend 8,00m (Km 398,570 - 398,778).
        - Kabelkanal innen gereinigt 1110,00m.
    """))
    e = leistungen(a)
    assert [x.menge for x in nach_taetigkeit(e, "Kabelkanal öffnen und schließen")] == [Decimal("177.00")]
    assert [x.menge for x in nach_taetigkeit(e, "Kabelkanal reinigen")] == [Decimal("1110.00")]
    assert len(a.hinweise) == 1 and "208 m" in a.hinweise[0]


def test_laengenpruefung_und_berechnung():
    a = werte_aus(bericht("""
        Kabel:S1401
        Trommelnummer:180832053(200x1x09)
        Anfang:974(STW)
        Ende:480(MP-1401)
        Gesamtlänge:494,00m

        Kabelbezeichnung: S1301
        Kabeltyp: 30x1x0,9
        Anfangsstand: 2119
        Endstand: 2621
        Gesamtlänge: 414m

        Kabelbezeichnung: S2506-9
        Kabeltyp: 1x4x0,9
        Anfangsstand: 100
        Endstand: 250
    """))
    assert a.kabel[0].typ == "200x1x0,9"
    assert a.kabel[2].laenge == 150
    assert a.hinweise == ["Kabel S1301: Länge 414 m ≠ |Ende 2621 - Anfang 2119| = 502 m"]


def test_schacht_varianten():
    a = werte_aus(bericht("""
        Schacht Gr 80x80 öffnen und schließen x18.
        15x Großer Schacht geöffnet und geschlossen.
        16 schächte öffnen & schließen
        - Schacht 2× ausgepumpt.
    """))
    assert [(s.groesse, s.anzahl) for s in a.schacht] == [("80x80", 18), ("groß", 15), ("", 16)]
    assert [z.text for z in a.unerkannt] == ["- Schacht 2× ausgepumpt."]


def test_lagerliste_ist_keine_verlegung():
    a = werte_aus(bericht("""
        Basel Kabellager Platz
        A1 (20x1x0.9)
        1. 1030m (128066662)
        2. 2054m (168017026)
    """))
    assert a.kabel == [] and leistungen(a) == []


def test_umverlegt_ist_keine_neue_verlegung():
    a = werte_aus(bericht("""
        Kabel umverlegt:
        Kabelbezeichnung: S3031-2-1
        Kabeltyp: 1x4x0,8
        Anfangsstand:1676 (AW-412-E)
        Endstand: 1302 (LEU-ZV421)
        Gesamtlänge: 374m
    """))
    e = leistungen(a)
    assert [x.taetigkeit for x in e] == ["Kabel umverlegen"]
    assert (e[0].merkmale["anfang"], e[0].merkmale["ende"]) == ("1676", "1302")
