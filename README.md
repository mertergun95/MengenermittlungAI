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
| `bautagebuch.py` – bau-mobil Bautagebuch PDF → günlük raporlar (sayfa/blok referanslı) | ✅ |
| `gaeb.py` – GAEB X83/X84 LV okuma, X83+X84 birleştirme, Nachtrag | ✅ |
| `extraktion.py` – kural tabanlı çıkarma: kablo, kanal, şaft, kanal temizliği + kontroller | ✅ |
| `kabelkatalog.py` – Bayka datasheet'lerinden kablo tipi → dış çap (`data/kabelkatalog.csv`, 158 kablo) | ✅ |
| `verlegeprotokoll.py` – Kabelverlegeprotokoll (Excel) okuma, Bautagebuch ile kablo kablo karşılaştırma | ✅ |
| LLM ile çıkarma (tanınmayan satırlar için, Ollama, yerel) | ⏳ |
| LV eşleştirme | ⏳ |
| Miktar hesabı + Excel çıktısı | ⏳ |

## Kurulum

```bash
pip install -e ".[dev]"
pytest
```

## LV okuma

```bash
python -m mengen.gaeb Basel_LV.x83 Basel_LV.x84
```

```python
from mengen.gaeb import lese_lv, verbinde, vereinige
haupt = verbinde(lese_lv("Basel_LV.x83"), lese_lv("Basel_LV.x84"))       # metin + fiyat
nt1 = verbinde(lese_lv("Basel_Nachtrag_LV.x83"), lese_lv("Basel_Nachtrag_LV.x84"))
positionen = vereinige(haupt, nt1)                                       # eşleştirme için tek liste
```

X84 sadece OZ ve fiyat içerir. Metin, birim ve LV miktarı X83'ten gelir. Sadece X84 varsa miktar `GP / EP` ile türetilir.

## Bautagebuch okuma

PDF'ler metin tabanlı (Crystal Reports), OCR gerekmez. `pdftotext` gerekir (`apt install poppler-utils`).

```bash
python -m mengen.bautagebuch Riehen_BTB.pdf
```

Her `Tagesbericht`: tarih, proje no, hava, personel/saat, makineler ve `taetigkeiten` satırları.
Her satır sayfa numarasını, son başlığı (`abschnitt`, ör. "Kabelkanal öffnen (Km 4,266 - 3,085)") ve
boş satırlarla ayrılmış grubu (`block`, ör. bir kablo) taşır. Çok sayfalı raporlar tek güne birleştirilir.

## Kural tabanlı çıkarma

```bash
python -m mengen.extraktion Basel_BTB.pdf
```

Çıktı: tanınma oranı, iş türü ve özelliğe göre toplamlar, tutarlılık uyarıları ve tanınmayan satırların en sık kalıpları.

Tanınan kalıplar:
- **Kablo blokları**, iki yazım şekli: `Kabel S… / Kabelanfang / Kabelende / Summe` ve `Kabelbezeichnung / Kabeltyp / Anfangsstand / Endstand / Gesamtlänge`
- **Kanal:** BKK, GFK, KK ve U-Kanal; `1er…5er` boyutu, iç/üst konum (innenliegend/aufliegend), km aralığı
- **Şaft açma/kapama:** boyut ve adet
- **Kanal temizliği**

Faturalama kuralları:
- Kanal "schließen" (kapama) ayrıca sayılmaz.
- Günlük özet (gesamt/INSGESAMT) varsa ayrıntılı satırlar iki kez sayılmaz.

Kontroller:
- Uzunluk ile |Endstand − Anfangsstand| farkı
- Uzunluk ile km aralığı farkı
- Günlük toplam ile kabloların toplamı arasındaki fark
- Açılan ve kapatılan kanal bakiyesi

## Kablo kataloğu

LV kabloları dış çapa göre faturalandırıyor ("D bis 25 mm" / "D über 25-40 mm"), raporlarda ise sadece yapı yazıyor ("160x1x0,9").
`data/kabelkatalog.csv` üretici datasheet'lerinden üretilir:

```bash
python -m mengen.kabelkatalog kataloge/*.pdf
```

Aynı yapının farklı tiplerde farklı çapı olabilir (ör. 200x1x0,9: A-2YOF 39 mm, AJ-2YOF 41 mm).
Bu yüzden `durchmesser()` bir aralık döner; `bauart` parametresi ile daraltılabilir.

## Verlegeprotokoll ve karşılaştırma

```bash
python -m mengen.verlegeprotokoll Verlegeprotokoll-*.xlsx Basel_BTB.pdf
```

- Protokol her kablo için resmi kaynak sayılır: tam tip ve ürün tipi (Bauart), tambur, km, Anfang/Ende, SOLL/IST, not.
- Kablo miktarı **protokoldeki IST** değerinden alınır. Bautagebuch bunu doğrular; protokolde olmayan kablolar Bautagebuch'tan gelir ve işaretlenir.
- Çap, protokoldeki ürün tipi (Bauart) ile katalogdan kesin olarak bulunur.

Karşılaştırma kablo numarası üzerinden yapılır. Tespit edilenler:
- Uzunluk, tambur, tip ve tarih farkları
- Bautagebuch'ta aynı kablo parçasının iki kez yazılması
- Kablo numarasındaki yazım hataları (aynı gün ve aynı uzunluk)
- "Kabel umverlegt" (yeniden döşeme) ayrı bir iş olarak sayılır

## Kısaltma sözlüğü

`data/glossar.csv` şirkete özel kısaltmaları tutar (`abkuerzung;bedeutung`). Yeni kısaltma eklemek kod değişikliği gerektirmez.

> Gerçek proje verileri (raporlar, LV, Aufmaß) `daten_privat/` altına konur ve **asla** commit edilmez.
