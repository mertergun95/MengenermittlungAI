"""Gesamtablauf: LV + Bautagebücher + Verlegeprotokolle -> Mengenermittlung (Excel).

    python -m mengen.pipeline --lv LV.x83 LV.x84 NT.x83 NT.x84 --btb BTB.pdf \\
        --protokoll Verlegeprotokoll-*.xlsx --out aufmass.xlsx [--gedaechtnis projekt.json] [--llm qwen2.5:14b]
    python -m mengen.pipeline --lernen aufmass.xlsx --gedaechtnis projekt.json
"""
import argparse
from collections import Counter, defaultdict
from pathlib import Path

from mengen.aufmass import lerne, schreibe_excel
from mengen.bautagebuch import lese_bautagebuch
from mengen.extraktion import leistungen, werte_aus
from mengen.gaeb import lese_lv, verbinde, vereinige
from mengen.kabelkatalog import lade
from mengen.schema import LV, Leistungseintrag
from mengen.verlegeprotokoll import gleiche_ab, kabel_leistungen, lese_protokoll
from mengen.zuordnung import Gedaechtnis, Ollama, Zuordner


def lade_lvs(dateien: list[str]) -> list[LV]:
    """Dateien gleichen Namens (X83 + X84) werden zusammengeführt: Texte aus X83, Preise aus X84."""
    nach_name: dict[str, dict[str, LV]] = defaultdict(dict)
    for d in dateien:
        lv = lese_lv(d)
        nach_name[Path(d).stem][lv.datenart] = lv
    ergebnis = []
    for teile in nach_name.values():
        texte, preise = teile.get("83"), teile.get("84")
        ergebnis.append(verbinde(texte, preise) if texte and preise else texte or preise or next(iter(teile.values())))
    return ergebnis


def sammle_leistungen(btb_dateien: list[str], protokoll_dateien: list[str]) -> list[Leistungseintrag]:
    btb = [e for d in btb_dateien for b in lese_bautagebuch(d) for e in leistungen(werte_aus(b))]
    if not protokoll_dateien:
        return btb
    protokoll = [v for d in protokoll_dateien for v in lese_protokoll(d)]
    kabel = kabel_leistungen(gleiche_ab(protokoll, btb), lade())
    # Kabelverlegung kommt aus dem Abgleich (Protokoll vor Bautagebuch), alles andere aus dem Bautagebuch
    return kabel + [e for e in btb if e.taetigkeit != "Kabel einziehen"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lv", nargs="*", default=[])
    ap.add_argument("--btb", nargs="*", default=[])
    ap.add_argument("--protokoll", nargs="*", default=[])
    ap.add_argument("--out", default="mengenermittlung.xlsx")
    ap.add_argument("--gedaechtnis", type=Path)
    ap.add_argument("--llm", help="Ollama-Modell für unsichere Fälle, z. B. qwen2.5:14b")
    ap.add_argument("--lernen", help="geprüfte Excel-Datei: Korrekturen ins Gedächtnis übernehmen")
    a = ap.parse_args()

    gedaechtnis = Gedaechtnis(a.gedaechtnis)
    if a.lernen:
        print(f"{lerne(a.lernen, gedaechtnis)} Zuordnungen gelernt -> {a.gedaechtnis}")
        return

    lvs = lade_lvs(a.lv)
    positionen = vereinige(*lvs)
    eintraege = sammle_leistungen(a.btb, a.protokoll)
    zuordner = Zuordner(positionen, gedaechtnis, llm=Ollama(a.llm) if a.llm else None)
    zuordnungen = [zuordner.ordne_zu(e) for e in eintraege]
    schreibe_excel(a.out, zuordnungen, positionen, titel=" / ".join(f"{lv.projekt} {lv.bezeichnung}" for lv in lvs))

    print(f"{len(positionen)} LV-Positionen, {len(eintraege)} Leistungen -> {a.out}")
    print("Zuordnung:", dict(Counter(z.methode for z in zuordnungen)))
    print(f"zu prüfen: {sum(z.zu_pruefen for z in zuordnungen)}, ohne Position: {sum(not z.oz for z in zuordnungen)}")
    je_oz = defaultdict(lambda: [0, 0])
    for z in zuordnungen:
        if z.oz:
            je_oz[z.oz][0] += z.eintrag.menge or 0
            je_oz[z.oz][1] += 1
    nach_oz = {p.oz: p for p in positionen}
    for oz, (menge, n) in sorted(je_oz.items()):
        p = nach_oz[oz]
        print(f"  {oz:<12} {p.kurztext[:45]:<45} {menge:>10,.0f} {p.einheit or '':<3} ({n} Belege, LV {p.menge_lv:,.0f})")
    ohne = Counter(z.eintrag.taetigkeit for z in zuordnungen if not z.oz)
    if ohne:
        print("ohne Position:", dict(ohne))


if __name__ == "__main__":
    main()
