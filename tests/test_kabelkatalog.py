from decimal import Decimal

import pytest

from mengen.kabelkatalog import durchmesser, grundtyp, lade, lese_zeilen

# Zeilen wie sie pdftotext -layout aus den Bayka-Datenblättern liefert
ZEILEN = [
    "5746168 00996941 BayRail® AJ-2YOF(L)2YDB2Y 160x1x0,9 S (H115) 600 416.0113 (00996941) Fca    27.0 1202.0 39.0"
    " 2500.00 555          295 390       1000 200 1500",
    "5745749 00996869 BayRail® A-2YOF(L)2YB2Y 2x1x0,9 S (H115) 416.0113 (00996869) Fca 4.0 13.0 13.0 160.00 25 100"
    " 130 1000 091 4000",
    "5773400 00128976 BayRail® A-2Y(St)YbY 20x4x1,4 S Lg (1B 0.3) (00128976)33.0 1391.0 46.5         3125.00 510"
    "      368     552    500 200       1000",
    "5784200 00128875 BayRail® CPR AJ-2Y(St)YbY 16x1x1,4 S (H120 / 2B 0,5) Eca 11.0 307.0 25.5 1170.00 120 260 380"
    " 1000 160 1000",
    "                                                                    Außen-           Zug-",
]


def test_datenblattzeilen():
    kabel = lese_zeilen(ZEILEN, "test.pdf")
    assert [(k.bauart, k.typ, k.variante, k.durchmesser_mm) for k in kabel] == [
        ("AJ-2YOF(L)2YDB2Y", "160x1x0,9", "S (H115) 600", Decimal("39.0")),
        ("A-2YOF(L)2YB2Y", "2x1x0,9", "S (H115)", Decimal("13.0")),
        ("A-2Y(St)YbY", "20x4x1,4", "S Lg (1B 0.3)", Decimal("46.5")),
        ("AJ-2Y(St)YbY", "16x1x1,4", "S (H120 / 2B 0,5)", Decimal("25.5")),
    ]


@pytest.mark.parametrize("roh, erwartet", [("1x4x0,9S", "1x4x0,9"), ("200x1x0,9 S", "200x1x0,9"), ("LWL 96", "LWL 96")])
def test_grundtyp(roh, erwartet):
    assert grundtyp(roh) == erwartet


def test_durchmesser_aus_katalog():
    katalog = lade()
    assert len(katalog) == 158
    # Gleicher Aufbau, verschiedene Bauarten -> Bereich
    assert durchmesser("200x1x0,9", katalog) == (Decimal("39.0"), Decimal("41.0"))
    assert durchmesser("200x1x0,9", katalog, bauart="AJ") == (Decimal("41.0"), Decimal("41.0"))
    assert durchmesser("200x1x0,9", katalog, bauart="A") == (Decimal("39.0"), Decimal("39.0"))
    assert durchmesser("1x4x0,9S", katalog) == (Decimal("12.0"), Decimal("12.0"))
    assert durchmesser("999x1x0,9", katalog) is None
