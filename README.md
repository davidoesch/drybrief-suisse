# DryBrief Suisse

Automatisiertes Trockenheitsbriefing für Schweizer Gemeinden und Kantone.

**Leitsatz:** Komplexe Trockenheitsdaten werden in verständliche und reproduzierbare Lagebeurteilungen übersetzt.

---

## Schnellstart (lokal)

```bash
# 1. Repository klonen und Abhängigkeiten installieren
git clone https://github.com/DEIN-USERNAME/drybrief-suisse.git
cd drybrief-suisse
pip install -r requirements.txt

# 2. Einmalig: Gemeinde/Kanton → Warnregion Lookups aufbauen (~5 min)
python pipeline/01_build_lookups.py

# 3. Aktuelle Trockenheitsdaten abrufen
python pipeline/02_fetch_data.py

# 4. Briefings generieren
python pipeline/03_generate_briefings.py

# 5. Frontend lokal starten
cd frontend && python -m http.server 8080
# → http://localhost:8080
```

---

## Projektstruktur

```
drybrief-suisse/
├── pipeline/
│   ├── 01_build_lookups.py      # Einmalig: Gemeinde/Kanton → Warnregion
│   ├── 02_fetch_data.py         # Täglich: CSV von STAC herunterladen
│   ├── 03_generate_briefings.py # Täglich: Regelengine → JSON
│   └── config/
│       └── thresholds.json      # CDI-Klassen, Textbausteine, Empfehlungen
├── data/
│   ├── lookups/
│   │   ├── gemeinden.json       # {bfs_nr, name, kanton, region_id}
│   │   ├── kantone.json         # {kt_nr, name, abbr, region_ids[]}
│   │   ├── regions_meta.json    # {id, name_de, name_fr, name_it, name_en}
│   │   └── search_index.json    # Flacher Suchindex für Frontend
│   ├── raw/                     # Rohdaten (nicht committen, gross)
│   └── briefings/
│       ├── index.json           # Alle Regionen mit Kurzstatus
│       └── regions/
│           └── {31..68}.json    # Briefing pro Region
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── .github/workflows/
    └── daily_pipeline.yml
```

---

## GitHub Pages Setup

1. Repository auf GitHub pushen
2. Settings → Pages → Source: **GitHub Actions**
3. Manuellen Pipeline-Run starten:
   - Actions → "DryBrief — Tägliche Datenpipeline" → "Run workflow"
   - `rebuild_lookups: true` setzen (nur einmalig nötig)
4. Ab dann läuft die Pipeline täglich automatisch um 09:00 MESZ

---

## Datenquellen

| Quelle | Beschreibung | Update |
|--------|-------------|--------|
| [BAFU Trockenheitsplattform](https://www.trockenheit.admin.ch) | CDI, Teilindizes, Prognosen | Wöchentlich |
| [MeteoSchweiz Open Data](https://opendatadocs.meteoswiss.ch) | Niederschlag, Klimadaten | Täglich |
| [BAFU Hydrodaten](https://www.hydrodaten.admin.ch) | Abfluss, Pegel, Grundwasser | Stündlich |
| [swisstopo SwissBOUNDARIES3D](https://opendata.swiss/de/dataset/swissboundaries3d) | Gemeinde- und Kantonsgrenzen | Jährlich |

---

## CDI-Klassifikation

| Wert | Bezeichnung | Farbe | Gefahrenstufe |
|------|------------|-------|---------------|
| 1 | Nicht trocken | Grün | 1 |
| 2 | Leicht trocken | Gelb | 2 |
| 3 | Trocken | Orange | 2 |
| 4 | Sehr trocken | Rot | 4 |
| 5 | Extrem trocken | Dunkelrot | 4 |

Gemäss: *Empfohlene Terminologie für Trockenheitsbulletin* (BAFU/MeteoSchweiz, 2024)

---

## Nutzungsbedingungen

Die verwendeten Bundesdaten stehen unter offenen Lizenzen zur Verfügung.
Quellenangabe obligatorisch: © Bundesbehörden der Schweizerischen Eidgenossenschaft.

Dieses Briefing ersetzt keine fachliche Beurteilung durch zuständige Behörden.
Für rechtsverbindliche Trockenheitswarnungen: [www.trockenheit.admin.ch](https://www.trockenheit.admin.ch)
