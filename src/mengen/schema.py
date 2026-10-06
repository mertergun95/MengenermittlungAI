"""Gemeinsames Datenmodell für alle Pipeline-Schritte.

Belegaufnahme -> Leistungseintrag -> LV-Zuordnung -> Mengenzeile
Jede Zeile trägt ihre Quelle mit, damit die Mengenermittlung prüfbar bleibt.
"""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class Quelle(BaseModel):
    """Woher eine Angabe stammt (Bericht, Seite, Foto)."""

    dokument: str
    seite: int | None = None
    foto: str | None = None
    zitat: str | None = Field(None, description="Originaltext aus dem Beleg")


class Leistungseintrag(BaseModel):
    """Eine strukturierte Tätigkeit aus einem Bericht (Ausgabe des LLM-Schritts)."""

    datum: date | None = None
    km_von: Decimal | None = None
    km_bis: Decimal | None = None
    gleis: str | None = None
    ort: str | None = None
    taetigkeit: str
    material: str | None = None
    menge: Decimal | None = None
    einheit: str | None = None
    quelle: Quelle


class LVPosition(BaseModel):
    """Position aus dem Leistungsverzeichnis (GAEB X83/X84)."""

    oz: str
    kurztext: str
    langtext: str = ""
    einheit: str
    menge_lv: Decimal | None = None


class Mengenzeile(BaseModel):
    """Vorschlag für eine Zeile der Mengenermittlung, wartet auf menschliche Freigabe."""

    oz: str
    menge: Decimal
    einheit: str
    rechenweg: str = Field(description="Nachvollziehbare Berechnung, z. B. 'km 12,345 - 12,100 = 245 m'")
    eintraege: list[Leistungseintrag]
    konfidenz: float = Field(ge=0, le=1)
    freigegeben: bool = False
