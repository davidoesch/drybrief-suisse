#!/usr/bin/env python3
"""
04_build_municipality_profiles.py
==================================
Einmalig: Baut Gemeinde-Profile aus mehreren offenen Bundesdaten.

Datenquellen
------------
  1. SwissBOUNDARIES3D GPKG (gecacht von 01_build_lookups.py)
       → Gemeindegrenzen, Fläche, Zentroid
  2. geo.admin.ch Height API
       → Mittlere Meereshöhe (Zentroid-Punkt)
  3. geo.admin.ch Identify: ch.blw.landwirtschaftliche-zonengrenzen
       → Landwirtschaftliche Zone (Talzone / Hügelzone / Bergzone I–IV /
         Sömmerungsgebiet / ausserhalb LW-Zone)
  4. geo.admin.ch Identify: ch.bafu.wasser-grundwasserschutzareale
       → Liegt Gemeindezentrum in einem Grundwasserschutzbezirk?
  5. geo.admin.ch WFS: ch.bfs.arealstatistik-bodenbedeckung-1997
       → Landnutzungsanteile per Polygon (falls WFS antwortet)
       Fallback: Schätzung aus Zone + Höhe (klar dokumentiert)
  6. Wildfire-Level aus data/raw/ (via 02_fetch_data.py)

Ausgabe
-------
  data/profiles/{bfs_nr}.json      — Ein Profil pro Gemeinde
  data/profiles/index.json         — Lightweight-Index aller Profile
  data/profiles/build_meta.json    — Laufzeit-Metadaten

Laufzeit: ca. 5–15 Minuten für ~2100 Gemeinden (rate-limited API-Calls).
Ergebnisse werden gecacht; wiederholter Aufruf ist idempotent.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import fiona
import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Pfade ─────────────────────────────────────────────────────────────────────
ROOT         = Path(__file__).parent.parent
LOOKUP_DIR   = ROOT / "data" / "lookups"
PROFILE_DIR  = ROOT / "data" / "profiles"
RAW_DIR      = ROOT / "data" / "raw"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

CACHE_GPKG   =  RAW_DIR / "_swissboundaries.gpkg"

# ── API-Endpunkte ─────────────────────────────────────────────────────────────
GEO_IDENTIFY = "https://api3.geo.admin.ch/rest/services/api/MapServer/identify"
GEO_HEIGHT   = "https://api3.geo.admin.ch/rest/services/height"
WFS_BASE     = "https://wfs.geo.admin.ch/"

# Layer-Namen
LAYER_BLW_ZONES = "ch.blw.landwirtschaftliche-zonengrenzen"
LAYER_GW_ZONES  = "ch.bafu.wasser-grundwasserschutzareale"
LAYER_AREALSTAT = "ch.bfs.arealstatistik-bodenbedeckung-1997"

# API Rate-Limit (Sekunden zwischen Calls pro Thread)
RATE_LIMIT_SEC = 0.08
MAX_WORKERS    = 4  # parallele API-Threads

# ── Landnutzungs-Schätzung aus Zone + Höhe ───────────────────────────────────
# Quellen: BFS Arealstatistik 2018, zonale Mittelwerte
# Ausgewiesen als Schätzung (estimation=True im Profil)

ZONE_LAND_USE_ESTIMATES = {
    # zone_key: {agri, forest, alpine, built, water}  (in %, Summe ~100)
    "Talzone":            {"agri": 55, "forest": 22, "alpine":  0, "built": 20, "water": 3},
    "Hügelzone":          {"agri": 50, "forest": 35, "alpine":  0, "built": 13, "water": 2},
    "Bergzone I":         {"agri": 38, "forest": 42, "alpine":  8, "built": 10, "water": 2},
    "Bergzone II":        {"agri": 26, "forest": 42, "alpine": 22, "built":  8, "water": 2},
    "Bergzone III":       {"agri": 16, "forest": 30, "alpine": 45, "built":  6, "water": 3},
    "Bergzone IV":        {"agri":  8, "forest": 20, "alpine": 66, "built":  3, "water": 3},
    "Sömmerungsgebiet":   {"agri":  3, "forest":  9, "alpine": 82, "built":  1, "water": 5},
    "ausserhalb LW-Zone": {"agri": 10, "forest": 25, "alpine": 30, "built": 30, "water": 5},
    "unbekannt":          {"agri": 30, "forest": 30, "alpine": 15, "built": 20, "water": 5},
}

# Altitudinale Korrekturen (additive %-Punkte)
def altitude_correction(base: dict, altitude_m: int) -> dict:
    """Passt Schätzung für sehr hohe/tiefe Lagen an."""
    d = dict(base)
    if altitude_m > 2000:
        d["alpine"] = min(90, d["alpine"] + 20)
        d["agri"]   = max(0,  d["agri"]   - 15)
        d["built"]  = max(0,  d["built"]  - 5)
    elif altitude_m > 1500:
        d["alpine"] = min(80, d["alpine"] + 10)
        d["agri"]   = max(0,  d["agri"]   - 8)
    elif altitude_m < 400:
        d["agri"]   = min(70, d["agri"]   + 8)
        d["alpine"] = max(0,  d["alpine"] - 5)
    # Normalisieren auf 100%
    total = sum(d.values())
    return {k: round(v * 100 / total, 1) for k, v in d.items()}


# ── Profil-Klassen und Empfehlungen ──────────────────────────────────────────
# Welche sektorialen Empfehlungen bei welchem Profil + CDI greifen

SECTOR_RULES = [
    # (Profilbedingung, CDI-Schwelle, Text)
    ("agri_est >= 40",  2, "Die Gemeinde hat bedeutende Landwirtschaftsflächen. "
                            "Landwirte sollten Bewässerungsbedarf prüfen und "
                            "Ertragsausfälle frühzeitig melden."),
    ("agri_est >= 40",  3, "Erhebliche Trockenheit für Ackerbau und Grünland: "
                            "Priorität Wasserversorgung Tier- und Pflanzenproduktion."),
    ("agri_est >= 25 and 'Talzone' in agri_zone", 2,
                           "Intensive Tallandwirtschaft: Bewässerungsrestriktionen "
                            "kantonal prüfen und kommunizieren."),
    ("alpine_est >= 30",  2, "Alpweiden und Sömmerungsgebiete vorhanden. "
                             "Wasserversorgung auf Maiensässen und Alpbetrieben prüfen."),
    ("alpine_est >= 50",  3, "Hoher Alpflächenanteil: Quellschüttungen und "
                             "Alpwasserversorgungen besonders überwachen."),
    ("'Sömmerungsgebiet' in agri_zone", 2,
                            "Sömmerungsbetrieb in der Gemeinde: Tränkewasser "
                             "für Weidetiere sicherstellen."),
    ("forest_est >= 35",  2, "Bedeutender Waldanteil: Waldbrandgefahr beachten, "
                             "offene Feuerstellen vermeiden."),
    ("forest_est >= 35",  3, "Erhebliche Trockenheit im Wald: Kantonsforstdienst "
                             "informieren, Borkenkäferbefall beobachten."),
    ("in_groundwater_zone", 3, "Grundwasserschutzzonen vorhanden: "
                                "Wasserversorgungsverantwortliche sollten "
                                "Reserven und Verbundleitungen prüfen."),
    ("altitude_m > 1800", 3, "Hochalpine Lage: Gletscherschmelze kann "
                              "Trockenheitssignale vorübergehend überlagern. "
                              "Pegel kleiner Zuflüsse besonders beachten."),
    ("altitude_m < 500 and agri_est >= 30", 3,
                            "Tieflagen mit intensiver Landwirtschaft: "
                             "Bewässerungswasser kann knapp werden — "
                             "Koordination Gemeinde / Wasserversorger empfohlen."),
    ("water_est >= 8",  3, "Bedeutende Wasserflächen in der Gemeinde: "
                            "Seepegel und Zuflüsse engmaschig überwachen."),
    ("built_est >= 40 and in_groundwater_zone", 3,
                            "Urbane Gemeinde mit Grundwasserbezug: "
                             "Wasserversorger über Lageeinschätzung informieren."),
]


def eval_sector_rules(profile: dict, cdi: int) -> list[str]:
    """Wertet SECTOR_RULES für ein Profil und einen CDI aus."""
    extra = []
    # Lokale Variablen für eval
    agri_est         = profile.get("agri_pct_est", 0)
    forest_est       = profile.get("forest_pct_est", 0)
    alpine_est       = profile.get("alpine_pct_est", 0)
    built_est        = profile.get("built_pct_est", 0)
    water_est        = profile.get("water_pct_est", 0)
    agri_zone        = profile.get("agri_zone", "unbekannt")
    in_groundwater_zone = profile.get("in_groundwater_zone", False)
    altitude_m       = profile.get("altitude_m", 0)

    for condition, cdi_threshold, text in SECTOR_RULES:
        if cdi < cdi_threshold:
            continue
        try:
            if eval(condition, {}, locals()):  # noqa: S307
                extra.append(text)
        except Exception:
            pass
    # Deduplizieren (Reihenfolge beibehalten)
    seen, out = set(), []
    for t in extra:
        if t not in seen:
            seen.add(t); out.append(t)
    return out


# ── GPKG: Gemeinden mit Zentroiden laden ─────────────────────────────────────

def load_gemeinden_with_centroids() -> gpd.GeoDataFrame:
    """Lädt Gemeindegrenzen aus gecachtem GPKG, berechnet LV95-Zentroide."""
    if not CACHE_GPKG.exists():
        raise FileNotFoundError(
            f"{CACHE_GPKG} nicht gefunden. "
            "Zuerst 01_build_lookups.py ausführen."
        )
    layers = fiona.listlayers(str(CACHE_GPKG))
    gem_layer = next(
        (l for l in layers if "HOHEITSGEBIET" in l.upper() or "GEMEINDE" in l.upper()),
        layers[0],
    )
    log.info("Lade Gemeinden aus GPKG-Layer '%s' …", gem_layer)
    gdf = gpd.read_file(str(CACHE_GPKG), layer=gem_layer)
    gdf["geometry"] = gdf.geometry.buffer(0)

    # Zentroide in LV95
    gdf["centroid_x"] = gdf.geometry.centroid.x
    gdf["centroid_y"] = gdf.geometry.centroid.y
    gdf["area_ha"]    = (gdf.geometry.area / 10_000).round(1)

    # Felder normalisieren
    cols = list(gdf.columns)
    f_bfs  = _best_field(cols, ["BFS_NUMMER", "BFS", "NUMMER"])
    f_name = _best_field(cols, ["NAME", "GEMEINDENAME"])
    f_obj  = _best_field(cols, ["OBJEKTART", "TYPE"])

    if f_obj:
        gdf = gdf[gdf[f_obj].astype(str).str.contains(
            "Gemeindegebiet|municipality|commune", case=False, na=False
        )]

    gdf = gdf.rename(columns={f_bfs: "bfs_nr", f_name: "name"})
    gdf["bfs_nr"] = pd.to_numeric(gdf["bfs_nr"], errors="coerce").dropna().astype(int)
    gdf = gdf.dropna(subset=["bfs_nr"])

    log.info("  %d Gemeinden mit Zentroiden geladen.", len(gdf))
    return gdf


def _best_field(cols: list[str], candidates: list[str]) -> str | None:
    for c in candidates:
        for col in cols:
            if c.upper() in col.upper():
                return col
    return None


# ── API-Calls ─────────────────────────────────────────────────────────────────

def get_altitude(x_lv95: float, y_lv95: float) -> int | None:
    """Fragt Meereshöhe am Zentroid via geo.admin.ch Height-API ab."""
    try:
        r = requests.get(
            GEO_HEIGHT,
            params={"easting": round(x_lv95), "northing": round(y_lv95), "sr": "2056"},
            timeout=10,
        )
        r.raise_for_status()
        h = r.json().get("height")
        return int(float(h)) if h is not None else None
    except Exception as e:
        log.debug("Height-API Fehler (%s, %s): %s", x_lv95, y_lv95, e)
        return None


def _identify(x_lv95: float, y_lv95: float, layers: str) -> list[dict]:
    """Generischer geo.admin.ch Identify-Call für LV95-Koordinaten."""
    try:
        r = requests.get(
            GEO_IDENTIFY,
            params={
                "geometry":      f"{round(x_lv95)},{round(y_lv95)}",
                "geometryType":  "esriGeometryPoint",
                "sr":            "2056",
                "layers":        f"all:{layers}",
                "tolerance":     "200",
                "mapExtent":     "2480000,1070000,2835000,1296000",
                "imageDisplay":  "1000,800,96",
                "returnGeometry":"false",
                "f":             "json",
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as e:
        log.debug("Identify-API Fehler (%s): %s", layers, e)
        return []


def get_blw_zone(x_lv95: float, y_lv95: float) -> dict:
    """Landwirtschaftliche Zone für einen Punkt."""
    results = _identify(x_lv95, y_lv95, LAYER_BLW_ZONES)
    if not results:
        return {"agri_zone": "ausserhalb LW-Zone", "agri_zone_code": None}

    attrs = results[0].get("attributes", {})

    # Zonennamen aus verschiedenen möglichen Feldern extrahieren
    zone_raw = (
        attrs.get("zone_name_de") or
        attrs.get("ZONE_NAME_DE") or
        attrs.get("zone") or
        attrs.get("ZONE") or
        attrs.get("label") or
        str(attrs)
    )

    # Standardisieren
    zone = _normalize_zone(str(zone_raw))
    return {
        "agri_zone":      zone,
        "agri_zone_code": attrs.get("zone_code") or attrs.get("ZONE_CODE"),
        "agri_zone_raw":  zone_raw,
    }


def _normalize_zone(raw: str) -> str:
    """Normalisiert verschiedene Schreibweisen auf Standardnamen."""
    raw_low = raw.lower()
    if "sömmerung" in raw_low or "sommerung" in raw_low or "alp" in raw_low:
        return "Sömmerungsgebiet"
    if "bergzone iv" in raw_low or "bergzone 4" in raw_low:
        return "Bergzone IV"
    if "bergzone iii" in raw_low or "bergzone 3" in raw_low:
        return "Bergzone III"
    if "bergzone ii" in raw_low or "bergzone 2" in raw_low:
        return "Bergzone II"
    if "bergzone i" in raw_low or "bergzone 1" in raw_low:
        return "Bergzone I"
    if "hügel" in raw_low or "hugel" in raw_low or "hügelzone" in raw_low:
        return "Hügelzone"
    if "talzone" in raw_low or "tal " in raw_low or "talgebiet" in raw_low:
        return "Talzone"
    if "ausserhalb" in raw_low or "keine" in raw_low or "not" in raw_low:
        return "ausserhalb LW-Zone"
    if raw_low in ("none", "null", "{}", ""):
        return "ausserhalb LW-Zone"
    # Unbekannter Wert — zurückgeben für spätere Analyse
    return f"unbekannt ({raw[:40]})"


def get_groundwater_zone(x_lv95: float, y_lv95: float) -> bool:
    """Prüft ob Zentroid in einem BAFU Grundwasserschutzbezirk liegt."""
    results = _identify(x_lv95, y_lv95, LAYER_GW_ZONES)
    return len(results) > 0


# ── Arealstatistik via WFS (optional) ────────────────────────────────────────

def try_fetch_arealstatistik_wfs() -> gpd.GeoDataFrame | None:
    """
    Versucht Arealstatistik-Polygone via WFS zu laden.
    Gibt None zurück wenn nicht verfügbar — Fallback auf Schätzung.
    """
    log.info("Versuche Arealstatistik WFS …")
    try:
        url = (
            f"{WFS_BASE}?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
            f"&TYPENAME={LAYER_AREALSTAT}&outputFormat=application/json&srsName=EPSG:2056"
            "&COUNT=1"  # Nur 1 Feature zum Test
        )
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        gdf = gpd.read_file(r.content.decode(), driver="GeoJSON")
        log.info("  WFS verfügbar: %d Features, Spalten: %s",
                 len(gdf), list(gdf.columns))
        return gdf
    except Exception as e:
        log.info("  Arealstatistik WFS nicht verfügbar: %s — verwende Schätzung.", e)
        return None


# ── Wildfire: aktuellen Stand aus gecachten Daten lesen ──────────────────────

def load_wildfire_levels() -> dict[int, int]:
    """
    Lädt Waldbrandgefahrenstufen aus dem BAFU-Layer, falls 02_fetch_data.py
    einen entsprechenden Cache angelegt hat. Sonst leer.
    Gibt {region_id: level} zurück.
    """
    path = RAW_DIR / "waldbrand_current.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    log.debug("Keine Waldbrand-Cache-Datei gefunden — übersprungen.")
    return {}


# ── Einzelnes Gemeinde-Profil aufbauen ────────────────────────────────────────

def build_profile(
    row: dict,
    wildfire_levels: dict[int, int],
) -> dict:
    """
    Baut ein vollständiges Profil für eine Gemeinde.
    Alle API-Calls passieren hier.
    """
    bfs_nr  = int(row["bfs_nr"])
    name    = str(row["name"])
    x, y    = float(row["centroid_x"]), float(row["centroid_y"])
    area_ha = float(row.get("area_ha", 0))
    region_id = row.get("region_id")

    # ── 1. Altitude ───────────────────────────────────────────────────────────
    time.sleep(RATE_LIMIT_SEC)
    altitude_m = get_altitude(x, y) or 600  # Fallback: Schweizer Mittel

    # ── 2. BLW Landwirtschaftliche Zone ──────────────────────────────────────
    time.sleep(RATE_LIMIT_SEC)
    zone_data = get_blw_zone(x, y)
    agri_zone = zone_data["agri_zone"]

    # ── 3. Grundwasserschutz ──────────────────────────────────────────────────
    time.sleep(RATE_LIMIT_SEC)
    in_gw_zone = get_groundwater_zone(x, y)

    # ── 4. Landnutzungsschätzung aus Zone + Altitude ──────────────────────────
    # Auflösung normierter Zone auf Schätz-Schlüssel
    zone_key = agri_zone if agri_zone in ZONE_LAND_USE_ESTIMATES else "unbekannt"
    base_lu  = ZONE_LAND_USE_ESTIMATES[zone_key]
    land_use = altitude_correction(base_lu, altitude_m)

    # ── 5. Waldbrand ──────────────────────────────────────────────────────────
    wildfire_level = wildfire_levels.get(region_id) if region_id else None

    # ── 6. Abgeleitete Flags ──────────────────────────────────────────────────
    is_alpine      = altitude_m > 1500 or agri_zone in ("Bergzone IV", "Sömmerungsgebiet")
    is_agricultural = land_use["agri"] >= 30
    has_forest     = land_use["forest"] >= 25
    is_urban       = land_use["built"] >= 40

    # ── Profil zusammenbauen ──────────────────────────────────────────────────
    return {
        # Identifikation
        "bfs_nr":     bfs_nr,
        "name":       name,
        "region_id":  region_id,
        "area_ha":    area_ha,

        # Topographie
        "altitude_m":       altitude_m,
        "altitude_class":   _altitude_class(altitude_m),

        # Landwirtschaft
        "agri_zone":        agri_zone,
        "agri_zone_code":   zone_data.get("agri_zone_code"),
        "is_sommerungsbetrieb": agri_zone == "Sömmerungsgebiet",

        # Landnutzung (Schätzung aus Zone + Höhe)
        "land_use_source":  "Schätzung aus BLW-Zone + Höhenlage (nicht BFS Arealstatistik)",
        "agri_pct_est":     land_use["agri"],
        "forest_pct_est":   land_use["forest"],
        "alpine_pct_est":   land_use["alpine"],
        "built_pct_est":    land_use["built"],
        "water_pct_est":    land_use["water"],

        # Wasser und Umwelt
        "in_groundwater_zone": in_gw_zone,
        "wildfire_level_region": wildfire_level,

        # Abgeleitete Profil-Flags
        "is_alpine":        is_alpine,
        "is_agricultural":  is_agricultural,
        "has_forest":       has_forest,
        "is_urban":         is_urban,

        # Koordinaten (für spätere Verwendung)
        "centroid_lv95": [round(x), round(y)],
    }


def _altitude_class(m: int) -> str:
    if m < 400:   return "Tieflagen"
    if m < 800:   return "Mittelland"
    if m < 1200:  return "Voralpen"
    if m < 1800:  return "Alpen"
    if m < 2500:  return "Hochalpen"
    return "Extreme Höhenlage"


# ── Parallele Verarbeitung ────────────────────────────────────────────────────

def process_all(gemeinden: gpd.GeoDataFrame, wildfire_levels: dict) -> list[dict]:
    """Verarbeitet alle Gemeinden parallel (rate-limited)."""
    rows = gemeinden.to_dict("records")
    total = len(rows)
    log.info("Verarbeite %d Gemeinden mit %d Threads …", total, MAX_WORKERS)

    results = []
    errors  = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(build_profile, row, wildfire_levels): row
            for row in rows
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            row = futures[future]
            try:
                profile = future.result()
                results.append(profile)
                if done % 100 == 0 or done == total:
                    log.info("  %d / %d Gemeinden (%d%%)",
                             done, total, 100 * done // total)
            except Exception as e:
                errors.append({"bfs_nr": row.get("bfs_nr"), "error": str(e)})
                log.debug("  Fehler Gemeinde %s: %s", row.get("name"), e)

    if errors:
        log.warning("%d Fehler bei der Verarbeitung (siehe error-Log).", len(errors))

    results.sort(key=lambda r: r["bfs_nr"])
    return results


# ── Export ────────────────────────────────────────────────────────────────────

def save_profiles(profiles: list[dict]) -> None:
    """Speichert ein JSON pro Gemeinde + einen Index."""
    saved = 0
    for p in profiles:
        bfs = p["bfs_nr"]
        path = PROFILE_DIR / f"{bfs}.json"
        # Nur schreiben wenn Profil sich geändert hat (idempotenz)
        new_content = json.dumps(p, ensure_ascii=False, indent=2)
        if path.exists() and path.read_text() == new_content:
            continue
        path.write_text(new_content)
        saved += 1

    log.info("  %d neue/geänderte Profile gespeichert, %d unverändert.",
             saved, len(profiles) - saved)


def save_index(profiles: list[dict]) -> None:
    """Leichtgewichtiger Index für das Frontend."""
    index = [
        {
            "bfs_nr":       p["bfs_nr"],
            "name":         p["name"],
            "region_id":    p["region_id"],
            "altitude_m":   p["altitude_m"],
            "agri_zone":    p["agri_zone"],
            "is_alpine":    p["is_alpine"],
            "is_agricultural": p["is_agricultural"],
            "has_forest":   p["has_forest"],
            "in_groundwater_zone": p["in_groundwater_zone"],
        }
        for p in profiles
    ]
    (PROFILE_DIR / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2)
    )


def save_build_meta(profiles: list[dict], duration_s: float) -> None:
    from datetime import datetime, timezone

    # Verteilung der Zonen
    zones: dict[str, int] = {}
    for p in profiles:
        z = p["agri_zone"]
        zones[z] = zones.get(z, 0) + 1

    meta = {
        "built_at":       datetime.now(timezone.utc).isoformat(),
        "n_municipalities": len(profiles),
        "duration_seconds": round(duration_s, 1),
        "zone_distribution": zones,
        "altitude_stats": {
            "min_m":  min(p["altitude_m"] for p in profiles),
            "max_m":  max(p["altitude_m"] for p in profiles),
            "mean_m": round(sum(p["altitude_m"] for p in profiles) / len(profiles)),
        },
        "data_sources": [
            "geo.admin.ch Height API",
            "ch.blw.landwirtschaftliche-zonengrenzen (Identify API)",
            "ch.bafu.wasser-grundwasserschutzareale (Identify API)",
            "Land use: Schätzung aus BLW-Zone + Höhenlage",
        ],
    }
    (PROFILE_DIR / "build_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2)
    )
    log.info("  Build-Metadaten gespeichert.")


# ── Statistik ─────────────────────────────────────────────────────────────────

def print_stats(profiles: list[dict]) -> None:
    zones: dict[str, int] = {}
    for p in profiles:
        z = p["agri_zone"]
        zones[z] = zones.get(z, 0) + 1

    log.info("")
    log.info("Zonenverteilung:")
    for zone, count in sorted(zones.items(), key=lambda x: -x[1]):
        bar = "█" * (count // 10)
        log.info("  %-30s %s %d", zone, bar, count)

    alpine    = sum(1 for p in profiles if p["is_alpine"])
    agri      = sum(1 for p in profiles if p["is_agricultural"])
    forest    = sum(1 for p in profiles if p["has_forest"])
    gw        = sum(1 for p in profiles if p["in_groundwater_zone"])

    log.info("")
    log.info("Profil-Flags:")
    log.info("  Alpine Gemeinden:            %d", alpine)
    log.info("  Landwirtschafts-Gemeinden:   %d", agri)
    log.info("  Waldreiche Gemeinden:        %d", forest)
    log.info("  Grundwasserschutz-Gemeinden: %d", gw)


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    import time as _time
    t0 = _time.time()

    log.info("=" * 60)
    log.info("DryBrief Suisse — Gemeinde-Profil-Builder")
    log.info("=" * 60)

    # ── Gemeinden laden ───────────────────────────────────────────────────────
    gemeinden_gdf = load_gemeinden_with_centroids()

    # region_id aus bestehendem Lookup ergänzen
    lookup_path = LOOKUP_DIR / "gemeinden.json"
    if lookup_path.exists():
        lookup = {e["bfs_nr"]: e for e in json.loads(lookup_path.read_text())
                  if e.get("bfs_nr")}
        gemeinden_gdf["region_id"] = gemeinden_gdf["bfs_nr"].map(
            lambda b: lookup.get(int(b), {}).get("region_id")
        )
    else:
        gemeinden_gdf["region_id"] = None

    # ── Optionaler Arealstatistik-WFS-Test ───────────────────────────────────
    try_fetch_arealstatistik_wfs()  # Nur zum Logging; Ergebnis noch nicht integriert

    # ── Waldbrand-Level ───────────────────────────────────────────────────────
    wildfire_levels = load_wildfire_levels()
    log.info("Waldbrand-Daten: %d Regionen.", len(wildfire_levels))

    # ── Profil-Build ──────────────────────────────────────────────────────────
    log.info("")
    log.info("Starte Gemeinde-Profiling …")
    log.info("  API Rate-Limit: %.2f s/Call, %d Threads", RATE_LIMIT_SEC, MAX_WORKERS)
    log.info("  Geschätzte Laufzeit: %.0f–%.0f Min.",
             len(gemeinden_gdf) * RATE_LIMIT_SEC * 3 / MAX_WORKERS / 60,
             len(gemeinden_gdf) * RATE_LIMIT_SEC * 3 / MAX_WORKERS / 60 * 2)
    log.info("")

    profiles = process_all(gemeinden_gdf, wildfire_levels)

    # ── Speichern ─────────────────────────────────────────────────────────────
    save_profiles(profiles)
    save_index(profiles)

    duration = _time.time() - t0
    save_build_meta(profiles, duration)

    print_stats(profiles)

    log.info("")
    log.info("Fertig in %.1f s", duration)
    log.info("Profile unter: %s", PROFILE_DIR)
    log.info("")
    log.info("Nächste Schritte:")
    log.info("  1. Briefings neu generieren: python pipeline/03_generate_briefings.py")
    log.info("  2. Frontend neu starten:     bash start.sh")


if __name__ == "__main__":
    main()
