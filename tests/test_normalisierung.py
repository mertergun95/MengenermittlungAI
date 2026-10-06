from decimal import Decimal

import pytest

from mengen.normalisierung import (
    expandiere_abkuerzungen,
    lade_glossar,
    normalisiere_einheit,
    parse_km,
)


@pytest.fixture(scope="module")
def glossar():
    return lade_glossar()


def test_abkuerzungen(glossar):
    text = "12 Schw. getauscht, KK verlegt am Bstg 2"
    assert expandiere_abkuerzungen(text, glossar) == (
        "12 Schwelle getauscht, Kabelkanal verlegt am Bahnsteig 2"
    )


def test_abkuerzung_nicht_in_wort(glossar):
    assert expandiere_abkuerzungen("KKW Schwellen", glossar) == "KKW Schwellen"


@pytest.mark.parametrize(
    "roh, erwartet",
    [("lfm", "m"), ("Stk.", "St"), ("cbm", "m3"), ("m²", "m2"), ("Std", "h"), ("xyz", None)],
)
def test_einheiten(roh, erwartet):
    assert normalisiere_einheit(roh) == erwartet


@pytest.mark.parametrize(
    "roh, erwartet",
    [
        ("12+345", "12.345"),
        ("km 12,345", "12.345"),
        ("12+45", "12.045"),
        ("12,5", "12.500"),
        ("7", "7"),
        ("abc", None),
    ],
)
def test_km(roh, erwartet):
    assert parse_km(roh) == (Decimal(erwartet) if erwartet else None)
