# MengenermittlungAI

Bautagesberichten, protokollerden ve Vermessungsdaten'dan **yerel yapay zekâ** ile Mengenermittlung taslağı üretir.
Hedef tam otomatik Aufmaß değil: insan onayına giden, kaynak gösterimli, %70–80'i hazır bir taslak.

## Pipeline

```
Belgeler ──► Anlama (LLM) ──► Leistungseintrag ──► LV eşleştirme ──► Miktar hesabı (klasik kod) ──► İnsan onayı ──► Excel / GAEB X31
             + normalizasyon                        (embedding+LLM)     (REB mantığı)
```

| Modül | Durum |
|---|---|
| `schema.py` – ortak veri modeli (Leistungseintrag, LVPosition, Mengenzeile) | ✅ |
| `normalisierung.py` – kısaltma sözlüğü, birim, km (`12+345`) | ✅ |
| Belge okuma (PDF/Word/Excel) | ⏳ |
| GAEB X83/X84 LV okuma | ⏳ |
| LLM ile çıkarma (Ollama, yerel) | ⏳ |
| LV eşleştirme | ⏳ |
| Miktar hesabı + Excel çıktısı | ⏳ |

## Kurulum

```bash
pip install -e ".[dev]"
pytest
```

## Kısaltma sözlüğü

`data/glossar.csv` şirkete özel kısaltmaları tutar (`abkuerzung;bedeutung`). Yeni kısaltma eklemek kod değişikliği gerektirmez.

> Gerçek proje verileri (raporlar, LV, Aufmaß) `daten_privat/` altına konur ve **asla** commit edilmez.
