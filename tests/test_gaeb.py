from decimal import Decimal
from pathlib import Path

import pytest

from mengen.gaeb import lese_lv, verbinde, vereinige

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def haupt():
    return verbinde(lese_lv(FIX / "Basel_LV.x83"), lese_lv(FIX / "Basel_LV.x84"))


@pytest.fixture(scope="module")
def nachtrag():
    return verbinde(lese_lv(FIX / "Basel_Nachtrag_LV.x83"), lese_lv(FIX / "Basel_Nachtrag_LV.x84"))


def test_x83_kopf_und_oz():
    lv = lese_lv(FIX / "Basel_LV.x83")
    assert (lv.projekt, lv.datenart) == ("923512", "83")
    assert [p.oz for p in lv.positionen] == ["21.02.0010", "21.03.0010", "23.01.0010", "23.01.0020"]


def test_x83_texte(haupt):
    p = haupt.position("21.02.0010")
    assert p.kurztext == "Kabel einziehen, D über 25-40 mm"
    assert p.abschnitt == "Kabelverlegung > Stammkabel"
    assert p.langtext.splitlines()[:3] == [
        "MLV-KTB_01220060",
        "Vom AG beigestelltes Kabel",
        "Durchmesser über 25-40 mm",
    ]
    assert (p.einheit, p.menge_lv) == ("m", Decimal("60000"))


def test_x84_menge_aus_preisen():
    lv = lese_lv(FIX / "Basel_LV.x84")
    p = lv.position("21.03.0010")
    assert p.kurztext == "" and p.einheit is None
    assert (p.ep, p.gp, p.menge_lv) == (Decimal("11.5"), Decimal("1495000"), Decimal("130000"))


def test_verbinde(haupt):
    p = haupt.position("23.01.0020")
    assert (p.einheit, p.menge_lv, p.ep) == ("m", Decimal("130000"), Decimal("8.5"))
    assert sum(p.gp for p in haupt.positionen) == Decimal("3800000")


def test_nachtrag_nur_kurztext(nachtrag):
    p = nachtrag.position("99.01.0060")
    assert (p.kurztext, p.langtext, p.einheit) == ("Kabeschacht Gr V öffnen und schließen", "", "St")
    assert sum(p.gp for p in nachtrag.positionen) == Decimal("591696.25")


def test_verbinde_fehlende_preise():
    with pytest.raises(ValueError, match="OZ ohne Preis"):
        verbinde(lese_lv(FIX / "Basel_LV.x83"), lese_lv(FIX / "Basel_Nachtrag_LV.x84"))


def test_vereinige(haupt, nachtrag):
    assert len(vereinige(haupt, nachtrag)) == 11
    with pytest.raises(ValueError, match="mehrfach"):
        vereinige(haupt, haupt)
