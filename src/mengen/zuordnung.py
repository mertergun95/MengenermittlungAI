"""LV-Zuordnung: Leistungseintrag -> LV-Position, projektunabhängig.

Keine projektspezifischen Regeln. Die Zuordnung stützt sich auf
1. Kennwerte, die aus dem LV-Text selbst gelesen werden ('D bis 25 mm',
   'Größe II', 'IV-V') und mit den Merkmalen des Eintrags verglichen werden,
2. Einheitenverträglichkeit,
3. Textähnlichkeit (Zeichen-n-Gramme nach Normalisierung von Abkürzungen und Synonymen),
4. ein Gedächtnis bestätigter Zuordnungen (lernt aus Korrekturen),
5. optional ein lokales LLM für unsichere Fälle.
Unklare Fälle werden nicht geraten, sondern mit niedriger Konfidenz zur Prüfung markiert.
"""
import json
import math
import re
import urllib.request
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from mengen.normalisierung import GLOSSAR_PFAD, expandiere_abkuerzungen, lade_glossar, normalisiere_einheit
from mengen.schema import Leistungseintrag, LVPosition

SYNONYM_PFAD = GLOSSAR_PFAD.parent / "synonyme.csv"
# Merkmale, die ein einzelnes Kabel/Stück identifizieren, aber nichts über die Position sagen
FLUECHTIG = {"bezeichnung", "trommel", "anfang", "ende", "abgleich", "teilverlegung", "kabel"}
MINDESTSCORE = 0.25
MINDESTKONFIDENZ = 0.3  # darunter kein Vorschlag, nur Alternativen zur Prüfung
ROEMISCH = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}

Bereich = tuple[Decimal | None, Decimal | None, bool]  # (von, bis, von_ausschließlich)
Pruefung = Literal["ja", "teils", "nein"]


# --- Kennwerte aus LV-Text ----------------------------------------------------------

_Z = r"(\d+(?:[.,]\d+)?)"
_D = r"(?:\bD\b|Durchmesser|Ø)\s*"
_DURCHMESSER = [
    (re.compile(_D + r"(?:über|>)\s*" + _Z + r"\s*-\s*" + _Z + r"\s*mm", re.I), lambda a, b: (a, b, True)),
    (re.compile(_D + r"(?:bis|<=|≤)\s*" + _Z + r"\s*mm", re.I), lambda a: (None, a, False)),
    (re.compile(_D + r"(?:über|>)\s*" + _Z + r"\s*mm", re.I), lambda a: (a, None, True)),
    (re.compile(_D + r"(?:ab|>=|≥)\s*" + _Z + r"\s*mm", re.I), lambda a: (a, None, False)),
    (re.compile(_D + _Z + r"\s*-\s*" + _Z + r"\s*mm", re.I), lambda a, b: (a, b, False)),
]
_GROESSE = re.compile(r"\b(?:Größe|Gr\.?)\s*([IVX]+|\d+)\b(?:\s*-\s*([IVX]+|\d+)\b)?")


def _d(text: str) -> Decimal:
    return Decimal(text.replace(",", "."))


def _stufe(text: str) -> Decimal:
    return Decimal(ROEMISCH.get(text, 0) or int(text))


def kennwerte(position: LVPosition) -> dict[str, Bereich]:
    """Abrechnungsrelevante Bereiche aus Kurz- und Langtext; der Kurztext hat Vorrang."""
    ergebnis: dict[str, Bereich] = {}
    for text in (position.kurztext, position.langtext):
        if "durchmesser_mm" not in ergebnis:
            for muster, bau in _DURCHMESSER:
                if m := muster.search(text):
                    ergebnis["durchmesser_mm"] = bau(*(_d(g) for g in m.groups()))
                    break
        if "groesse" not in ergebnis and (m := _GROESSE.search(text)):
            von = _stufe(m.group(1))
            ergebnis["groesse"] = (von, _stufe(m.group(2)) if m.group(2) else von, False)
    return ergebnis


def _wertebereich(wert: str) -> tuple[Decimal, Decimal] | None:
    """'41.0' -> (41, 41), '38.0–42.0' -> (38, 42), 'groß' -> None."""
    zahlen = re.findall(r"\d+(?:[.,]\d+)?", wert)
    if not zahlen or re.search(r"[a-zA-Zäöü]", wert):
        return None
    werte = [_d(z) for z in zahlen]
    return min(werte), max(werte)


def pruefe(bereich: Bereich, wert: tuple[Decimal, Decimal]) -> Pruefung:
    von, bis, exkl = bereich
    a, b = wert
    unten_ok = von is None or (a > von if exkl else a >= von)
    oben_ok = bis is None or b <= bis
    if unten_ok and oben_ok:
        return "ja"
    unten_raus = von is not None and (b <= von if exkl else b < von)
    oben_raus = bis is not None and a > bis
    return "nein" if unten_raus or oben_raus else "teils"


# --- Textähnlichkeit ------------------------------------------------------------------

class Normalisierer:
    def __init__(self, glossar: dict[str, str] | None = None, synonym_pfad: Path = SYNONYM_PFAD):
        self.glossar = lade_glossar() if glossar is None else glossar
        self.synonyme: dict[str, str] = {}
        if synonym_pfad.exists():
            for zeile in synonym_pfad.read_text(encoding="utf-8").splitlines():
                if zeile.strip() and not zeile.startswith("#"):
                    gruppe = [w.strip().lower() for w in zeile.split(";") if w.strip()]
                    self.synonyme.update({w: gruppe[0] for w in gruppe})

    def __call__(self, text: str) -> str:
        t = expandiere_abkuerzungen(text, self.glossar).lower()
        t = t.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
        woerter = re.findall(r"[a-z0-9-]+", t)
        norm = {k.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss"): v
                for k, v in self.synonyme.items()}
        return " ".join(norm.get(w, w) for w in woerter)


class NGrammAehnlichkeit:
    """TF-IDF über Zeichen-3-Gramme; robust gegen Tippfehler und Wortformen, ohne Modell."""

    def __init__(self, texte: list[str]):
        self.vektoren = [self._grams(t) for t in texte]
        df = Counter(g for v in self.vektoren for g in v)
        n = len(texte)
        self.idf = {g: math.log((n + 1) / (c + 1)) + 1 for g, c in df.items()}
        self.vektoren = [self._gewichte(v) for v in self.vektoren]

    @staticmethod
    def _grams(text: str) -> Counter:
        grams: Counter = Counter()
        for wort in text.split():
            w = f" {wort} "
            grams.update(w[i:i + 3] for i in range(len(w) - 2))
        return grams

    def _gewichte(self, grams: Counter) -> dict[str, float]:
        v = {g: c * self.idf.get(g, 1.0) for g, c in grams.items()}
        laenge = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {g: x / laenge for g, x in v.items()}

    def __call__(self, anfrage: str) -> list[float]:
        q = self._gewichte(self._grams(anfrage))
        return [sum(w * v.get(g, 0.0) for g, w in q.items()) for v in self.vektoren]


# --- LLM (optional) -----------------------------------------------------------------------

class LLM(Protocol):
    def json(self, prompt: str) -> dict: ...


class Ollama:
    """Lokales Modell über die Ollama-HTTP-API; Daten verlassen den Server nicht."""

    def __init__(self, modell: str = "qwen2.5:14b", url: str = "http://localhost:11434"):
        self.modell, self.url = modell, url.rstrip("/")

    def json(self, prompt: str) -> dict:
        daten = json.dumps({"model": self.modell, "prompt": prompt, "format": "json", "stream": False,
                            "options": {"temperature": 0}}).encode()
        anfrage = urllib.request.Request(f"{self.url}/api/generate", data=daten,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(anfrage, timeout=120) as antwort:
            return json.loads(json.loads(antwort.read())["response"])


PROMPT = """Du ordnest eine Bauleistung aus einem Bautagebuch einer Position des Leistungsverzeichnisses zu.

Leistung:
{leistung}

Kandidaten (nur diese OZ sind erlaubt):
{kandidaten}

Regeln:
- Einheit der Leistung und der Position müssen zusammenpassen.
- Bereiche im Positionstext (Durchmesser, Größe) müssen den Merkmalen der Leistung entsprechen.
- Passt keine Position, antworte mit "oz": null. Nicht raten.

Antworte nur mit JSON: {{"oz": "<OZ oder null>", "begruendung": "<ein Satz>"}}"""


# --- Zuordnung ---------------------------------------------------------------------------

class Kandidat(BaseModel):
    oz: str
    score: float
    gruende: list[str] = Field(default_factory=list)


class Zuordnung(BaseModel):
    eintrag: Leistungseintrag
    oz: str | None = None
    konfidenz: float = 0.0
    methode: Literal["regel", "gelernt", "llm", "keine"] = "keine"
    begruendung: str = ""
    kandidaten: list[Kandidat] = Field(default_factory=list)

    @property
    def zu_pruefen(self) -> bool:
        return self.oz is None or self.konfidenz < 0.6 or bool(self.eintrag.hinweise)


def schluessel(e: Leistungseintrag) -> str:
    """Wiedererkennungsmerkmal für das Gedächtnis: Tätigkeit + stabile Merkmale + Einheit."""
    teile = [e.taetigkeit, normalisiere_einheit(e.einheit or "") or ""]
    teile += [f"{k}={v}" for k, v in sorted(e.merkmale.items()) if k not in FLUECHTIG]
    return "|".join(teile)


class Gedaechtnis:
    """Bestätigte Zuordnungen je Projekt als JSON {schluessel: oz}."""

    def __init__(self, pfad: Path | None = None):
        self.pfad = pfad
        self.daten: dict[str, str] = json.loads(pfad.read_text(encoding="utf-8")) if pfad and pfad.exists() else {}

    def merke(self, schl: str, oz: str) -> None:
        self.daten[schl] = oz

    def speichere(self) -> None:
        if self.pfad:
            self.pfad.parent.mkdir(parents=True, exist_ok=True)
            self.pfad.write_text(json.dumps(self.daten, ensure_ascii=False, indent=1, sort_keys=True),
                                 encoding="utf-8")


class Zuordner:
    def __init__(self, positionen: list[LVPosition], gedaechtnis: Gedaechtnis | None = None,
                 llm: LLM | None = None, llm_schwelle: float = 0.8, normalisierer: Normalisierer | None = None):
        self.positionen = positionen
        self.gedaechtnis = gedaechtnis or Gedaechtnis()
        self.llm, self.llm_schwelle = llm, llm_schwelle
        self.norm = normalisierer or Normalisierer()
        self.kennwerte = [kennwerte(p) for p in positionen]
        self.aehnlichkeit = NGrammAehnlichkeit([self.norm(self._positionstext(p)) for p in positionen])

    @staticmethod
    def _positionstext(p: LVPosition) -> str:
        return f"{p.kurztext} {p.kurztext} {p.abschnitt} {p.langtext[:300]}"

    @staticmethod
    def _leistungstext(e: Leistungseintrag) -> str:
        werte = [v for k, v in e.merkmale.items() if k not in FLUECHTIG and not re.fullmatch(r"[\d.,–-]+", v)]
        return " ".join([e.taetigkeit, e.taetigkeit, e.material or "", *werte])

    def kandidaten(self, e: Leistungseintrag) -> list[Kandidat]:
        texte = self.aehnlichkeit(self.norm(self._leistungstext(e)))
        einheit = normalisiere_einheit(e.einheit or "")
        ergebnis = []
        for p, kw, text in zip(self.positionen, self.kennwerte, texte):
            gruende = [f"Text {text:.2f}"]
            kennwert_faktor = 0.5
            ausgeschlossen = False
            for name, bereich in kw.items():
                wert = _wertebereich(e.merkmale.get(name, ""))
                if wert is None:
                    # Position verlangt einen Kennwert, den die Leistung nicht (vergleichbar) angibt
                    ergebnis_pruefung = "teils"
                    gruende.append(f"{name} '{e.merkmale.get(name, '?')}' nicht prüfbar gegen "
                                   f"{_bereich_text(bereich)}: teils")
                else:
                    ergebnis_pruefung = pruefe(bereich, wert)
                    gruende.append(f"{name} {e.merkmale[name]} vs. {_bereich_text(bereich)}: {ergebnis_pruefung}")
                if ergebnis_pruefung == "nein":
                    ausgeschlossen = True
                kennwert_faktor = min(1.0 if kennwert_faktor == 0.5 else kennwert_faktor,
                                      1.0 if ergebnis_pruefung == "ja" else 0.3)
            if ausgeschlossen:
                continue
            score = 0.7 * text + 0.3 * kennwert_faktor
            p_einheit = normalisiere_einheit(p.einheit or "")
            if einheit and p_einheit and einheit != p_einheit:
                score *= 0.3
                gruende.append(f"Einheit {e.einheit} ≠ {p.einheit}")
            ergebnis.append(Kandidat(oz=p.oz, score=round(score, 3), gruende=gruende))
        return sorted(ergebnis, key=lambda k: -k.score)[:5]

    def ordne_zu(self, e: Leistungseintrag) -> Zuordnung:
        kand = self.kandidaten(e)
        gelernt = self.gedaechtnis.daten.get(schluessel(e))
        if gelernt and any(p.oz == gelernt for p in self.positionen):
            return Zuordnung(eintrag=e, oz=gelernt, konfidenz=0.95, methode="gelernt",
                             begruendung="früher bestätigte Zuordnung", kandidaten=kand)
        z = Zuordnung(eintrag=e, kandidaten=kand)
        if not kand or kand[0].score < MINDESTSCORE:
            z.begruendung = "keine passende Position gefunden"
        else:
            erster = kand[0]
            zweiter = kand[1].score if len(kand) > 1 else 0.0
            abstand = (erster.score - zweiter) / erster.score
            z.oz, z.methode = erster.oz, "regel"
            z.konfidenz = round(min(1.0, erster.score / 0.8) * (0.4 + 0.6 * min(1.0, abstand * 3)), 2)
            if any(g.endswith(": teils") for g in erster.gruende):
                z.konfidenz = min(z.konfidenz, 0.4)
            z.begruendung = "; ".join(erster.gruende)
            if z.konfidenz < MINDESTKONFIDENZ:
                z.begruendung = f"zu unsicher für einen Vorschlag (bester Kandidat {erster.oz}): {z.begruendung}"
                z.oz, z.methode = None, "keine"
        if self.llm and z.konfidenz < self.llm_schwelle and kand:
            self._frage_llm(z)
        return z

    def _frage_llm(self, z: Zuordnung) -> None:
        nach_oz = {p.oz: p for p in self.positionen}
        e = z.eintrag
        leistung = (f"{e.taetigkeit}; Material: {e.material or '-'}; Menge: {e.menge} {e.einheit}; "
                    f"Merkmale: {', '.join(f'{k}={v}' for k, v in e.merkmale.items() if k not in FLUECHTIG)}")
        kandidaten = "\n".join(
            f"- {k.oz}: {nach_oz[k.oz].kurztext} [{nach_oz[k.oz].einheit}] ({nach_oz[k.oz].abschnitt}) "
            f"{nach_oz[k.oz].langtext[:200].replace(chr(10), ' ')}"
            for k in z.kandidaten
        )
        try:
            antwort = self.llm.json(PROMPT.format(leistung=leistung, kandidaten=kandidaten))
        except Exception as fehler:  # LLM nicht erreichbar: Regelergebnis bleibt stehen
            z.begruendung += f"; LLM nicht verfügbar ({type(fehler).__name__})"
            return
        oz = antwort.get("oz")
        if oz in {k.oz for k in z.kandidaten}:
            z.konfidenz = max(z.konfidenz, 0.7) if oz == z.oz else 0.6
            z.oz, z.methode = oz, "llm"
            z.begruendung = str(antwort.get("begruendung", ""))
        elif oz in (None, "null", ""):
            z.oz, z.methode, z.konfidenz = None, "llm", 0.0
            z.begruendung = "LLM: " + str(antwort.get("begruendung", "keine Position passt"))


def _bereich_text(b: Bereich) -> str:
    von, bis, exkl = b
    if von is None:
        return f"≤{bis}"
    if bis is None:
        return f"{'>' if exkl else '≥'}{von}"
    return f"{'>' if exkl else ''}{von}–{bis}"
