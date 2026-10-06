from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from mengen.bautagebuch import zerlege

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def berichte():
    seiten = (FIX / "riehen_auszug.txt").read_text(encoding="utf-8").split("\f")
    return {b.datum: b for b in zerlege(seiten, "riehen.pdf")}


def test_seiten_zu_tagen(berichte):
    assert {d: b.seiten for d, b in berichte.items()} == {
        date(2025, 8, 25): [1],
        date(2025, 8, 27): [2, 3],
        date(2025, 9, 11): [4],
        date(2025, 11, 24): [5, 6, 7],
    }


def test_kopfdaten(berichte):
    b = berichte[date(2025, 8, 25)]
    assert (b.projekt, b.projekt_nr) == ("DB Riehen (CH), KV; LST", "924761")
    assert b.wetter == "sonnig \\ min. 12°C - max. 27°C"
    assert (b.personal_anzahl, b.stunden_gesamt) == (7, Decimal("70.00"))
    assert b.maschinen == ["ZW23 - Liebherr Bagger A922"]


def test_mehrere_maschinen(berichte):
    assert berichte[date(2025, 8, 27)].maschinen == [
        "ZW23 - Liebherr Bagger A922",
        "ZW20/1 - Liebherr A922",
    ]


def test_taetigkeiten_mit_abschnitt(berichte):
    zeilen = berichte[date(2025, 8, 25)].taetigkeiten
    assert zeilen[0].text == "- Kabelverlegung :"
    bkk = next(z for z in zeilen if "aufliegend 1973" in z.text)
    assert bkk.abschnitt == "Kabelkanal öffnen (Km 4,266 - 3,085) / (Km 2,854 - 1,750)"
    assert zeilen[-1].text == "Einweisung"


def test_kabel_bloecke(berichte):
    zeilen = berichte[date(2025, 8, 27)].taetigkeiten
    bloecke = {}
    for z in zeilen:
        bloecke.setdefault(z.block, []).append(z.text)
    kabel = [b for b in bloecke.values() if b[0].startswith("- Kabel S")]
    assert [b[0] for b in kabel] == [
        "- Kabel S1140 / 50×1×1,4.",
        "- Kabel S1140 / 50×1×1,4.",
        "- Kabel S1115 / 80×1×1,4.",
        "- Kabel S1135 / 50×1×1,4.",
    ]
    assert "- Summe / 444,00m." in kabel[0]


def test_seitenumbruch_im_bericht(berichte):
    b = berichte[date(2025, 11, 24)]
    zeile = next(z for z in b.taetigkeiten if z.text == "- Kabel S1635-2 / 1×4×1,4.")
    assert zeile.seite == 6
    # Die erste Zeile auf Seite 6 setzt den Kabelblock von Seite 5 fort
    erste_s6 = next(z for z in b.taetigkeiten if z.seite == 6)
    letzte_s5 = [z for z in b.taetigkeiten if z.seite == 5][-1]
    assert erste_s6.block == letzte_s5.block
    assert not any("bau-mobil" in z.text or "SH01" in z.text for z in b.taetigkeiten)


def test_leerer_bericht(berichte):
    b = berichte[date(2025, 9, 11)]
    assert b.taetigkeiten == [] and b.stunden_gesamt == Decimal("70.00")
