from datetime import date, datetime
from decimal import Decimal

import openpyxl
import pytest

from mengen.kabelkatalog import lade
from mengen.schema import Leistungseintrag, Quelle
from mengen.verlegeprotokoll import gleiche_ab, kabel_leistungen, kabelnummer, lese_protokoll, trommelnummer

KOPF = ["Kabel Nr.", "Datum", "Kabeltyp", "Trommel Nr.", "von\nBahnkm.", "bis\nBahnkm.", "von\nOrt", "bis\nOrt",
        "Kabelanfang", "Kabelende", "SOLL", "IST", "Verlegeart\nHand/Maschinel", "Bemerkungen/Besonderheiten"]


@pytest.fixture
def protokoll(tmp_path):
    """Zwei Blätter wie die echten Protokolle: Kopfbereich, Tabelle, Fußzeile 'Aussteller'."""
    wb = openpyxl.Workbook()
    blaetter = {
        "Verlegeprotokoll": [
            ["S1651", datetime(2026, 8, 4), "AJ–2YOF(L)2YDB2Y 200x1x0,9", "T180 535945", 270.396, 270.5,
             "ESTW-Z", "KS165", 2627, 2802, 175, 175, "Hand", None],
            ["S2505", datetime(2026, 8, 6), "AJ–2YOF(L)2YDB2Y 50x1x0,9", "188 02 3116", 270.396, 270.731,
             "ESTW-Z", "KS250", 6323, 6113, 700, 210, "Hand", None],
        ],
        "Verlegeprotokoll (2)": [
            ["S3031-2-2", datetime(2026, 7, 28), "A-2Y(L)2YB2Y 1x4x1,4", "148 01 1619", None, None,
             "ZV421 - LEU", "AW", 2067, 1783, 290, 284, "Hand", "Kabel liegt als Ring vor Endposition"],
            ["S3056-10", datetime(2026, 7, 28), "A–2Y(L)2YB2Y 1x4x0,9", "T120 648269", 4.151, None,
             "KS303", "W179/G1422", 10420, 10311, 109, 109, "Hand", None],
        ],
    }
    for i, (name, zeilen) in enumerate(blaetter.items()):
        ws = wb.active if i == 0 else wb.create_sheet()
        ws.title = name
        ws.append(["Kabelverlegeprotokoll"])
        ws.append(["Bauvorhaben:", "Test"])
        ws.append(KOPF)
        for z in zeilen:
            ws.append(z)
        ws.append(["Aussteller", None, None, "Bauleitung Auftragnehmer"])
    pfad = tmp_path / "Verlegeprotokoll-test.xlsx"
    wb.save(pfad)
    return lese_protokoll(pfad)


def btb(nr, tag, laenge, trommel="", anfang=None, ende=None, typ=""):
    return Leistungseintrag(
        datum=date(2026, 7, tag) if tag > 20 else date(2026, 8, tag), taetigkeit="Kabel einziehen",
        menge=Decimal(laenge), einheit="m",
        merkmale={k: str(v) for k, v in {"bezeichnung": nr, "trommel": trommel, "anfang": anfang,
                                         "ende": ende, "kabeltyp": typ}.items() if v},
        quelle=Quelle(dokument="btb.pdf", seite=1),
    )


def test_normalisierung():
    assert kabelnummer(" s 2501-5-9") == "S2501-5-9" and kabelnummer("2501-11") == "S2501-11"
    assert trommelnummer("T180 535945") == "180535945" and trommelnummer("128 05 6197") == "128056197"
    assert trommelnummer("DYD220S300668") == "DYD220S300668"


def test_lese_protokoll(protokoll):
    assert [v.kabel_nr for v in protokoll] == ["S1651", "S2505", "S3031-2-2", "S3056-10"]
    v = protokoll[0]
    assert (v.bauart, v.typ, v.trommel, v.datum) == ("AJ-2YOF(L)2YDB2Y", "200x1x0,9", "180535945", date(2026, 8, 4))
    assert (v.ort_von, v.ort_bis, v.ist, v.km_von) == ("ESTW-Z", "KS165", Decimal("175"), Decimal("270.396"))
    assert (v.blatt, v.zeile) == ("Verlegeprotokoll", 4)
    # Teilverlegung (IST < SOLL) ist normal und kein Hinweis
    assert protokoll[1].hinweise == []
    assert protokoll[2].hinweise == ["Bemerkung: Kabel liegt als Ring vor Endposition"]


def test_abgleich(protokoll):
    eintraege = [
        btb("S1651", 4, 175, trommel="T180535945", typ="200x1x0,9"),
        btb("S2505", 6, 200),                                   # Länge weicht ab
        btb("S3031-2-2", 28, 284, anfang=2067, ende=1783),
        btb("S3031-2-2", 31, 284, anfang=2067, ende=1783),      # doppelt eingetragen
        btb("S3036-10", 28, 109),                               # Tippfehler in der Kabelnummer
        btb("S9999", 29, 50),                                   # fehlt im Protokoll
    ]
    a = {x.kabel_nr: x for x in gleiche_ab(protokoll, eintraege)}
    assert a["S1651"].status == "bestätigt"
    assert a["S2505"].abweichungen == ["Länge: Protokoll 210 m, Bautagebuch 200 m"]
    assert a["S3031-2-2"].status == "Abweichung" and len(a["S3031-2-2"].btb) == 1
    assert "doppelt im Bautagebuch" in a["S3031-2-2"].abweichungen[0]
    assert "S3036-10" not in a and "vermutlich Tippfehler" in a["S3056-10"].abweichungen[0]
    assert a["S9999"].status == "nur Bautagebuch"


def test_kabel_leistungen_mit_durchmesser(protokoll):
    abgleiche = gleiche_ab(protokoll, [btb("S9999", 29, 50, typ="1x4x0,9")])
    e = {x.merkmale["bezeichnung"]: x for x in kabel_leistungen(abgleiche, lade())}
    # Protokoll hat Vorrang, Durchmesser aus Bauart + Aufbau
    assert (e["S1651"].menge, e["S1651"].merkmale["durchmesser_mm"]) == (Decimal("175"), "41.0")
    assert e["S2505"].merkmale["durchmesser_mm"] == "27.0"
    assert e["S3031-2-2"].merkmale["durchmesser_mm"] == "14.0"
    # Ohne Protokoll: Bautagebuch-Menge, Hinweis
    assert e["S9999"].menge == 50 and "nicht im Verlegeprotokoll" in e["S9999"].hinweise
