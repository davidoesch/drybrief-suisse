[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/davidoesch/drybrief-suisse)
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

# 2. Einmalig: Gemeinde/Kanton -> Warnregion Lookups aufbauen (~5 min)
python pipeline/01_build_lookups.py

# 3. Aktuelle Trockenheitsdaten abrufen
python pipeline/02_fetch_data.py

# 4. Briefings generieren
python pipeline/03_generate_briefings.py

# 5. Optional einmalig: Gemeinde-Profile aufbauen (~10 min)
python pipeline/04_build_municipality_profiles.py

# 6. Frontend lokal starten
cd frontend && python -m http.server 8080
# -> http://localhost:8080
```

---

## Projektstruktur

```
drybrief-suisse/
├── pipeline/
│   ├── 01_build_lookups.py             # Einmalig: Gemeinde/Kanton -> Warnregion
│   ├── 02_fetch_data.py                # Täglich: CSV von STAC herunterladen
│   ├── 03_generate_briefings.py        # Täglich: Regelengine -> JSON
│   ├── 04_build_municipality_profiles.py  # Einmalig: Gemeinde-Profile aufbauen
│   └── config/
│       └── thresholds.json             # Alle Texte, Emojis, Schwellenwerte,
│                                       #   CDI-Klassen und Empfehlungen
├── data/
│   ├── lookups/
│   │   ├── gemeinden.json              # {bfs_nr, name, kanton, region_id}
│   │   ├── kantone.json                # {kt_nr, name, abbr, region_ids[]}
│   │   ├── regions_meta.json           # {id, name_de, name_fr, name_it, name_en}
│   │   ├── search_index.json           # Flacher Suchindex für Frontend
│   │   └── warnregionen.geojson        # Geometrien für Kartenansicht
│   ├── raw/                            # Rohdaten (nicht committen, gross)
│   ├── profiles/
│   │   ├── index.json                  # Leichtgewichtiger Index aller Profile
│   │   └── {bfs_nr}.json              # Volles Profil pro Gemeinde
│   └── briefings/
│       ├── index.json                  # Alle Regionen mit Kurzstatus (aktuell)
│       ├── generated_at.json           # Zeitstempel des letzten Laufs
│       ├── regions/
│       │   └── {31..68}.json           # Briefing pro Region (aktuellster Stand)
│       └── {YYYY-MM-DD}/              # Historische Briefings nach Datum
│           ├── index.json
│           └── regions/
│               └── {31..68}.json
├── frontend/
│   ├── index.html                      # Vollständiges Briefing mit Karte
│   ├── app.js
│   ├── styles.css
│   ├── simple.html                     # Vereinfachte Ansicht (Bürger, Jugendliche)
│   ├── simple.js
│   └── simple.css
└── .github/workflows/
    └── daily_pipeline.yml
```

---

## Historische Briefings (Permalink)

Jedes Briefing kann über einen Permalink mit Datum aufgerufen werden:

```
https://dein-domain/frontend/?date=YYYY-MM-DD
```

Das Frontend lädt automatisch die Daten aus `data/briefings/YYYY-MM-DD/`. Das Datum wird beim Laden eines Briefings in der URL gesetzt, so dass ein direkter Link zum aktuellen Stand entsteht.

**Historisches Briefing generieren:**

```bash
# Briefing für einen bestimmten Tag aus vorhandenen CSV-Daten erzeugen
python pipeline/03_generate_briefings.py --date 2026-04-13
```

Das Datum muss in den heruntergeladenen CSV-Rohdaten vorhanden sein (`data/raw/weekly_current_regions.csv`). Die Pipeline schreibt dann nach `data/briefings/2026-04-13/`.

---

## Gemeinde-Profile und sektorale Empfehlungen

Mit `04_build_municipality_profiles.py` werden einmalig Profile für alle Schweizer Gemeinden aufgebaut. Die Profile nutzen Höhenlagen (DHM200) und BLW-Zonen als Grundlage.

**Was ein Profil enthält:**

| Feld | Beschreibung |
|------|-------------|
| `altitude_m` | Mittlere Höhe der Gemeinde (m.ü.M.) |
| `altitude_class` | Klasse: Tieflagen / Mittelland / Voralpen / Alpen / Hochalpen / Extreme Höhenlage |
| `altitude_m_max` | Maximale Höhe (auf Regionsebene aggregiert) |
| `is_alpine` | Alpwirtschaft / hochalpine Lage |
| `has_forest` | Waldgemeinde |
| `agri_zone` | Landwirtschaftliche Zone (BLW) |

Die Profile werden bei der Briefing-Generierung aggregiert (pro Warnregion) und über `municipality_rules` in `thresholds.json` ausgewertet. Die daraus resultierenden Empfehlungen und Auswirkungen fliessen direkt in `recommendations_de` und `impacts_de` des Briefings ein. Ohne Profile läuft die Pipeline normal weiter.

---

## Textbausteine anpassen

Alle inhaltlichen Texte, Emojis und Schwellenwerte sind zentral in
`pipeline/config/thresholds.json` abgelegt:

| Abschnitt | Inhalt |
|-----------|--------|
| `cdi` | CDI-Stufen mit Labels, Farben, Emojis, Kurztexten |
| `cdi_summary_de` | Zusammenfassungstext pro CDI-Stufe (Platzhalter: `{name}`) |
| `trend` | Trend-Labels und Pfeilsymbole |
| `fc_summary_de` | Prognosetexte (Platzhalter: `{cur}`, `{fc}`, `{noun}`) |
| `indicators` | Icons und Labels der drei Kernindikatoren |
| `spi_comparisons` | SPI-Schwellenwerte und Vergleichstexte |
| `soil_plain_pct` | Bodenfeuchtestufen nach UFC-Prozent |
| `hydro_plain` | Gewässertext pro Hydro-Index-Stufe |
| `hydro_flow_range` | Typischer Abflussbereich pro Hydro-Stufe |
| `impacts` | Mögliche Auswirkungen pro CDI-Stufe |
| `recommendations` | Offizielle Empfehlungen pro CDI-Stufe |
| `recommendations_simple` | Vereinfachte Empfehlungen für die einfache Ansicht |
| `municipality_rules` | Höhenlage- und Geländebasierte Zusatzempfehlungen |

### municipality_rules

Regeln werden auf den aggregierten Profilen einer Warnregion ausgewertet. Jede Regel hat:

```json
{
  "id":        "lowland_heat",
  "type":      "recommendation",
  "cdi_min":   3,
  "conditions": [{"field": "altitude_m", "op": "<=", "value": 600}],
  "text":      "Flachlandlage: Hitze und Trockenheit ..."
}
```

| Feld | Beschreibung |
|------|-------------|
| `id` | Eindeutige ID (verhindert Duplikate) |
| `type` | `recommendation` oder `impact` |
| `cdi_min` | Mindest-CDI für das Auslösen der Regel |
| `conditions` | Liste von Bedingungen (AND-verknüpft) |
| `text` | Ausgabetext (Platzhalter `{feldname}` möglich) |

Verfügbare Felder in Bedingungen: `altitude_m` (Mittelwert), `altitude_m_max` (Maximum), `is_alpine` (bool, any), `has_forest` (bool, any), `wildfire_level_region` (int, max).

Unterstützte Operatoren: `>`, `>=`, `<`, `<=`, `==`, `!=`.

---

## Ansichten

### Vollständiges Briefing (`index.html`)

Enthält alle Informationen mit interaktiver Karte:
- Lagebeurteilung mit CDI, Trend und Prognose
- Drei Kernindikatoren (Niederschlag, Gewässer, Bodenfeuchte)
- Mögliche Auswirkungen
- Empfehlungen (generisch + geländebasiert)
- Trockenheitskarte (BAFU WMTS)
- Prognose
- Quellen und Disclaimer

Kanton-Auswahl: zeigt Median-CDI und alle Warnregionen im Überblick.

### Einfache Ansicht (`simple.html`)

Für Bürgerinnen und Bürger, mobil-optimiert:
- Grosse Emoji- und Farbdarstellung nach CDI
- Drei Mini-Cards (Niederschlag, Gewässer, Boden)
- Prognosetext
- Empfehlungen in einfacher Sprache (max. 5 Punkte)
- Link zur offiziellen BAFU-Warnung

---

## GitHub Pages Setup

1. Repository auf GitHub pushen
2. Settings -> Pages -> Source: **GitHub Actions**
3. Manuellen Pipeline-Run starten:
   - Actions -> "DryBrief -- Tägliche Datenpipeline" -> "Run workflow"
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
| [BLW Landwirtschaftliche Zonen](https://www.blw.admin.ch) | Zonen für Gemeinde-Profile | Jährlich |

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

## Hydro-Daten (m³/s)

Die Felder `value_q_m3s`, `value_q_norm_min`, `value_q_norm_max` und
`value_q_norm_period` im Hydro-Indikator sind für echte Abflusswerte
(z.B. aus BAFU Hydrodaten) vorbereitet. Sobald ein regionaler Datensatz
bereitsteht, können diese Werte befüllt werden. Das Frontend zeigt dann:

```
Abfluss aktuell deutlich tiefer als normal
Aktuell: 42 m³/s
Normal (Mitte Mai): 70-130 m³/s
```

---

## Nutzungsbedingungen

Die verwendeten Bundesdaten stehen unter offenen Lizenzen zur Verfügung.
Quellenangabe obligatorisch: © Bundesbehörden der Schweizerischen Eidgenossenschaft.

Dieses Briefing ersetzt keine fachliche Beurteilung durch zuständige Behörden.
Für rechtsverbindliche Trockenheitswarnungen: [www.trockenheit.admin.ch](https://www.trockenheit.admin.ch)
