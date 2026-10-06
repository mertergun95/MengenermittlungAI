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
    """Position aus dem Leistungsverzeichnis (Texte aus X83, Preise aus X84)."""

    oz: str
    kurztext: str = ""
    langtext: str = ""
    abschnitt: str = Field("", description="Titelpfad, z. B. 'Kabelverlegung > Stammkabel'")
    einheit: str | None = None
    menge_lv: Decimal | None = None
    ep: Decimal | None = Field(None, description="Einheitspreis")
    gp: Decimal | None = Field(None, description="Gesamtpreis")
    lv: str = Field("", description="Kennung des LV, z. B. Projektnummer oder Nachtrag")


class LV(BaseModel):
    """Ein eingelesenes Leistungsverzeichnis."""

    projekt: str
    bezeichnung: str = ""
    datenart: str = Field(description="GAEB-Datenaustauschphase, z. B. '83' oder '84'")
    positionen: list[LVPosition]

    def position(self, oz: str) -> LVPosition | None:
        return next((p for p in self.positionen if p.oz == oz), None)


class Mengenzeile(BaseModel):
    """Vorschlag für eine Zeile der Mengenermittlung, wartet auf menschliche Freigabe."""

    oz: str
    menge: Decimal
    einheit: str
    rechenweg: str = Field(description="Nachvollziehbare Berechnung, z. B. 'km 12,345 - 12,100 = 245 m'")
    eintraege: list[Leistungseintrag]
    konfidenz: float = Field(ge=0, le=1)
    freigegeben: bool = False
