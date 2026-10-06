"""Regelbasierte Normalisierung: Abkürzungen, Einheiten, Kilometrierung.

Läuft vor und nach dem LLM. Was mit Regeln sicher geht, wird nicht dem LLM überlassen.
"""
import csv
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

GLOSSAR_PFAD = Path(__file__).resolve().parents[2] / "data" / "glossar.csv"

EINHEITEN = {
    "m": "m", "lfm": "m", "lfdm": "m", "lm": "m", "meter": "m",
    "m2": "m2", "m²": "m2", "qm": "m2",
    "m3": "m3", "m³": "m3", "cbm": "m3",
    "st": "St", "stk": "St", "stck": "St", "stück": "St",
    "t": "t", "to": "t", "tonne": "t", "tonnen": "t",
    "kg": "kg",
    "h": "h", "std": "h", "stunde": "h", "stunden": "h",
    "psch": "psch", "pauschal": "psch",
}


def lade_glossar(pfad: Path = GLOSSAR_PFAD) -> dict[str, str]:
    """Liest das firmenspezifische Abkürzungsverzeichnis (abkuerzung;bedeutung)."""
    with pfad.open(encoding="utf-8") as f:
        return {
            zeile["abkuerzung"].strip().lower(): zeile["bedeutung"].strip()
            for zeile in csv.DictReader(f, delimiter=";")
            if zeile["abkuerzung"].strip()
        }


def expandiere_abkuerzungen(text: str, glossar: dict[str, str]) -> str:
    """Ersetzt bekannte Abkürzungen als ganze Wörter, z. B. 'Schw.' -> 'Schwelle'."""
    # Längere Abkürzungen zuerst, damit 'Schw.' nicht von 'Schw' angefressen wird
    for abk in sorted(glossar, key=len, reverse=True):
        muster = r"(?<!\w)" + re.escape(abk) + (r"(?!\w)" if abk[-1].isalnum() else "")
        text = re.sub(muster, glossar[abk], text, flags=re.IGNORECASE)
    return text


def normalisiere_einheit(einheit: str) -> str | None:
    """'lfm' -> 'm', 'cbm' -> 'm3', 'Stk.' -> 'St'. Unbekannt -> None."""
    schluessel = einheit.strip().lower().rstrip(".").replace(" ", "")
    return EINHEITEN.get(schluessel)


_KM_MUSTER = re.compile(r"^(?:km\s*)?(\d+)\s*[+,.]\s*(\d{1,3})$", re.IGNORECASE)


def parse_km(text: str) -> Decimal | None:
    """Kilometrierung in km: '12+345', 'km 12,345', '12.345' -> Decimal('12.345').

    '12+45' wird als 12,045 km gelesen (Bahn-Notation: Meter nach dem '+').
    """
    treffer = _KM_MUSTER.match(text.strip())
    if not treffer:
        try:
            return Decimal(text.strip().replace(",", "."))
        except InvalidOperation:
            return None
    km, meter = treffer.groups()
    if "+" in text:
        meter = meter.zfill(3)
    else:
        meter = meter.ljust(3, "0")
    return Decimal(f"{km}.{meter}")
