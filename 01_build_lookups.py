#!/usr/bin/env python3
"""
01_build_lookups.py
===================
Einmalige Zuweisung: Gemeinde / Kanton → Trockenheits-Warnregion(en)

Datenquellen
------------
- SwissBOUNDARIES3D 2026 (GPKG, LV95/EPSG:2056) von swisstopo
- Trockenheits-Warnregionen via WFS geo.admin.ch  (Fallback: identify API)
- Regionsmetadaten (Name DE/FR/IT/EN) aus STAC Trockenheitsdaten

Ausgabe (data/lookups/)
-----------------------
  gemeinden.json     — {bfs_nr, name, kanton, region_id, region_name_de}
  kantone.json       — {kt_nr, name, abbr, region_ids[], region_shares[]}
  regions_meta.json  — {id, name_de, name_fr, name_it, name_en}

Laufzeit: ca. 3–8 Minuten (Netz + Geometrie).
Ergebnisse werden gecacht; wiederholter Aufruf ist idempotent.
"""

from __future__ import annotations

import json
import logging
import time
import zipfile
from io import BytesIO
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.errors import GEOSException

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Pfade ─────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent.parent
LOOKUP_DIR  = ROOT / "data" / "lookups"
LOOKUP_DIR.mkdir(parents=True, exist_ok=True)

CACHE_GPKG        = LOOKUP_DIR / "_swissboundaries.gpkg"
CACHE_WARNREGIONEN= LOOKUP_DIR / "_warnregionen.geojson"
CACHE_META        = LOOKUP_DIR / "_regions_meta.json"

# ── Externe URLs ──────────────────────────────────────────────────────────────
URL_BOUNDARIES = (
    "https://data.geo.admin.ch/ch.swisstopo.swissboundaries3d/"
    "swissboundaries3d_2026-01/"
    "swissboundaries3d_2026-01_2056_5728.gpkg.zip"
)

# WFS – Trockenheitswarnregionen (Polygone, EPSG:2056)
URL_WFS_WARNREGIONEN = (
    "https://wfs.geo.admin.ch/"
    "?SERVICE=WFS&VERSION=2.0.0&REQUEST=GetFeature"
    "&TYPENAME=ch.bafu.trockenheitsindex"
    "&outputFormat=application/json"
    "&srsName=EPSG:2056"
)

# Fallback: topo-satromo GitHub (SHP als ZIP)
URL_GITHUB_ASSETS_BASE = (
    "https://raw.githubusercontent.com/swisstopo/topo-satromo/main/assets/"
)
GITHUB_CANDIDATE_FILES = [
    "warnregionen.geojson",
    "warnregionen_ch.geojson",
    "Warnregionen.geojson",
    "drought_warning_regions.geojson",
]

# STAC – Regionsmetadaten (regions.csv)
URL_STAC_REFERENCE = (
    "https://data.geo.admin.ch/ch.bafu.trockenheitsdaten-numerisch/"
    "trockenheitsdaten-numerisch_reference/"
    "trockenheitsdaten-numerisch_reference.csv.zip"
)

# Identify-API Fallback (einzelne Punkte)
URL_IDENTIFY = (
    "https://api3.geo.admin.ch/rest/services/api/MapServer/identify"
)


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def download_bytes(url: str, label: str, timeout: int = 300) -> bytes:
    log.info("Download: %s", label)
    r = requests.get(url, timeout=timeout, stream=True)
    r.raise_for_status()
    buf, total = [], 0
    for chunk in r.iter_content(chunk_size=1 << 20):
        buf.append(chunk)
        total += len(chunk)
        if total % (20 << 20) == 0:
            log.info("  … %.0f MB empfangen", total / 1e6)
    log.info("  Fertig: %.1f MB", total / 1e6)
    return b"".join(buf)


def best_field(columns: list[str], candidates: list[str], label: str) -> str | None:
    """Gibt das erste Feld zurück, das einen der Kandidatennamen enthält."""
    upper = [c.upper() for c in columns]
    for cand in candidates:
        for i, u in enumerate(upper):
            if cand.upper() in u:
                log.debug("Feld '%s' → Kandidat '%s'", columns[i], cand)
                return columns[i]
    log.warning("Kein Feld für '%s' gefunden in: %s", label, columns)
    return None


# ── 1. SwissBOUNDARIES3D laden ────────────────────────────────────────────────

def load_boundaries() -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Lädt Gemeinde- und Kantonsgrenzen aus dem swissBOUNDARIES3D GPKG.
    Cached die GPKG-Datei lokal.
    """
    if not CACHE_GPKG.exists():
        data = download_bytes(URL_BOUNDARIES, "SwissBOUNDARIES3D GPKG (~300 MB)")
        with zipfile.ZipFile(BytesIO(data)) as z:
            gpkg_entries = [n for n in z.namelist() if n.endswith(".gpkg")]
            if not gpkg_entries:
                raise RuntimeError(f"Kein GPKG im ZIP gefunden: {z.namelist()}")
            name = gpkg_entries[0]
            log.info("Entpacke: %s", name)
            raw = z.read(name)
        CACHE_GPKG.write_bytes(raw)
        log.info("GPKG gecacht: %s", CACHE_GPKG)

    # Layer-Namen erkunden
    import fiona
    layers = fiona.listlayers(str(CACHE_GPKG))
    log.info("GPKG-Layer: %s", layers)

    # Gemeinden: Layer mit HOHEITSGEBIET oder GEMEINDE
    gem_layer = next(
        (l for l in layers if any(k in l.upper()
         for k in ["HOHEITSGEBIET", "GEMEINDE", "MUNICIPAL"])),
        layers[0],
    )
    # Kantone: Layer mit KANTON
    kt_layer = next(
        (l for l in layers if "KANTON" in l.upper()),
        layers[1] if len(layers) > 1 else layers[0],
    )

    log.info("Lade Gemeinden aus Layer '%s' …", gem_layer)
    gemeinden = gpd.read_file(str(CACHE_GPKG), layer=gem_layer)
    log.info("  %d Objekte geladen", len(gemeinden))

    log.info("Lade Kantone aus Layer '%s' …", kt_layer)
    kantone = gpd.read_file(str(CACHE_GPKG), layer=kt_layer)
    log.info("  %d Objekte geladen", len(kantone))

    # Nur echte Gemeindegebiete (kein See, kein Ausland)
    obj_field = best_field(list(gemeinden.columns), ["OBJEKTART", "TYPE", "ART"], "Objektart")
    if obj_field:
        before = len(gemeinden)
        gemeinden = gemeinden[gemeinden[obj_field].astype(str).str.contains(
            "Gemeindegebiet|municipality|commune", case=False, na=False
        )]
        log.info("  Nach Filterung Objektart: %d → %d", before, len(gemeinden))

    # Geometrien reparieren
    gemeinden["geometry"] = gemeinden.geometry.buffer(0)
    kantone["geometry"]   = kantone.geometry.buffer(0)

    return gemeinden, kantone


# ── 2. Warnregionen laden ─────────────────────────────────────────────────────

def load_warnregionen() -> gpd.GeoDataFrame:
    """
    Lädt die 38 Trockenheits-Warnregionen als Polygone.
    Strategie: WFS → GitHub Assets → Identify-API Fallback
    """
    if CACHE_WARNREGIONEN.exists():
        log.info("Warnregionen aus Cache: %s", CACHE_WARNREGIONEN)
        gdf = gpd.read_file(str(CACHE_WARNREGIONEN))
        log.info("  %d Regionen", len(gdf))
        return gdf

    # Strategie 1: WFS
    gdf = _try_wfs()
    if gdf is not None and len(gdf) > 0:
        _save_warnregionen(gdf)
        return gdf

    # Strategie 2: GitHub Assets
    gdf = _try_github()
    if gdf is not None and len(gdf) > 0:
        _save_warnregionen(gdf)
        return gdf

    # Strategie 3: Identify-API (langsam, aber zuverlässig)
    log.warning("WFS und GitHub fehlgeschlagen — verwende Identify-API Fallback.")
    log.warning("Dies dauert ca. 5–10 Minuten für alle Warnregionen.")
    gdf = _build_via_identify_api()
    if gdf is not None and len(gdf) > 0:
        _save_warnregionen(gdf)
        return gdf

    raise RuntimeError(
        "Warnregionen konnten nicht geladen werden.\n"
        "Bitte SHP manuell herunterladen von:\n"
        "https://github.com/swisstopo/topo-satromo/tree/main/assets\n"
        f"und als GeoJSON speichern unter: {CACHE_WARNREGIONEN}"
    )


def _try_wfs() -> gpd.GeoDataFrame | None:
    log.info("Versuche WFS: %s", URL_WFS_WARNREGIONEN[:80])
    try:
        r = requests.get(URL_WFS_WARNREGIONEN, timeout=60)
        r.raise_for_status()
        gdf = gpd.read_file(BytesIO(r.content))
        if len(gdf) > 0:
            log.info("  WFS erfolgreich: %d Features", len(gdf))
            return gdf
    except Exception as e:
        log.info("  WFS fehlgeschlagen: %s", e)
    return None


def _try_github() -> gpd.GeoDataFrame | None:
    for fname in GITHUB_CANDIDATE_FILES:
        url = URL_GITHUB_ASSETS_BASE + fname
        log.info("Versuche GitHub: %s", url)
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200:
                gdf = gpd.read_file(BytesIO(r.content))
                log.info("  GitHub erfolgreich: %d Features (%s)", len(gdf), fname)
                return gdf
        except Exception as e:
            log.debug("  %s: %s", fname, e)
    return None


def _build_via_identify_api() -> gpd.GeoDataFrame:
    """
    Fallback: Ermittelt die Warnregions-Grenzen, indem Gitterpunkte
    über die Schweiz gegen die ch.bafu.trockenheitsindex API abgefragt
    werden. Approximativ, aber funktionsfähig für den Lookup.

    Alternativ: lädt die aktuellen CDI-Daten und extrahiert die
    Region-IDs aus dem CSV (ohne Geometrie), was für die einfache
    Punkt-in-Region-Abfrage ausreicht.
    """
    log.info("Baue Warnregionen-Lookup via Identify-API …")

    # Schweizer Bounding Box (LV95): ca. 2480000–2835000, 1070000–1295000
    import numpy as np
    xs = np.linspace(2485000, 2833000, 60)
    ys = np.linspace(1075000, 1293000, 40)

    region_points: dict[int, list] = {}

    total = len(xs) * len(ys)
    done = 0
    for x in xs:
        for y in ys:
            done += 1
            if done % 200 == 0:
                log.info("  %d/%d Punkte abgefragt …", done, total)
            try:
                params = {
                    "geometry": f"{x},{y}",
                    "geometryType": "esriGeometryPoint",
                    "sr": "2056",
                    "layers": "all:ch.bafu.trockenheitsindex",
                    "tolerance": "100",
                    "mapExtent": "2480000,1070000,2835000,1295000",
                    "imageDisplay": "1000,800,96",
                    "returnGeometry": "false",
                    "f": "json",
                }
                r = requests.get(URL_IDENTIFY, params=params, timeout=10)
                r.raise_for_status()
                data = r.json()
                results = data.get("results", [])
                for res in results:
                    attrs = res.get("attributes", {})
                    rid = _extract_region_id(attrs)
                    if rid and 30 <= rid <= 70:
                        if rid not in region_points:
                            region_points[rid] = []
                        region_points[rid].append((x, y))
                time.sleep(0.05)  # Rate-Limiting
            except Exception:
                pass

    log.info("  Gefundene Regions-IDs: %s", sorted(region_points.keys()))

    # Aus Punkten Konvexe Hüllen bauen (Approximation)
    from shapely.geometry import MultiPoint
    rows = []
    for rid, pts in region_points.items():
        if len(pts) >= 3:
            geom = MultiPoint(pts).convex_hull
        elif len(pts) > 0:
            geom = MultiPoint(pts).buffer(5000)
        else:
            continue
        rows.append({"drought_region_id": rid, "geometry": geom})

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:2056")
    log.info("  Approximative Warnregionen erstellt: %d", len(gdf))
    return gdf


def _extract_region_id(attrs: dict) -> int | None:
    """Extrahiert die Regions-ID aus den Identify-Attributen."""
    for key in ["drought_region_id", "region_id", "id", "ID", "NR"]:
        val = attrs.get(key)
        if val is not None:
            try:
                v = int(float(str(val)))
                if 30 <= v <= 70:
                    return v
            except (ValueError, TypeError):
                pass
    return None


def _save_warnregionen(gdf: gpd.GeoDataFrame) -> None:
    # CRS normalisieren auf EPSG:2056
    if gdf.crs is None:
        log.warning("Warnregionen ohne CRS – nehme EPSG:2056 an.")
        gdf = gdf.set_crs("EPSG:2056")
    elif gdf.crs.to_epsg() != 2056:
        log.info("Konvertiere Warnregionen: %s → EPSG:2056", gdf.crs)
        gdf = gdf.to_crs("EPSG:2056")
    gdf.to_file(str(CACHE_WARNREGIONEN), driver="GeoJSON")
    log.info("Warnregionen gecacht: %s", CACHE_WARNREGIONEN)


# ── 3. Regions-Metadaten aus STAC ─────────────────────────────────────────────

def load_regions_meta() -> dict[int, dict]:
    """
    Lädt regions.csv aus dem STAC Reference-Datensatz.
    Enthält die offiziellen Region-Namen in DE/FR/IT/EN/RM.
    """
    if CACHE_META.exists():
        log.info("Regionsmetadaten aus Cache.")
        return {int(k): v for k, v in json.loads(CACHE_META.read_text()).items()}

    log.info("Lade Regionsmetadaten aus STAC …")
    data = download_bytes(URL_STAC_REFERENCE, "STAC Reference CSV")
    meta = {}

    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            csv_name = next(
                (n for n in z.namelist()
                 if "region" in n.lower() and n.endswith(".csv")),
                None,
            )
            if not csv_name:
                raise FileNotFoundError(f"regions.csv nicht im ZIP: {z.namelist()}")
            log.info("  Lese: %s", csv_name)
            df = pd.read_csv(z.open(csv_name), sep=";", comment="#")

        log.info("  Spalten: %s", list(df.columns))

        id_col = next(
            (c for c in df.columns if "id" in c.lower() and "region" in c.lower()),
            df.columns[0],
        )
        for _, row in df.iterrows():
            try:
                rid = int(float(str(row[id_col])))
            except (ValueError, TypeError):
                continue
            meta[rid] = {
                "id":       rid,
                "name_de":  str(row.get("name_de", f"Region {rid}")),
                "name_fr":  str(row.get("name_fr", f"Région {rid}")),
                "name_it":  str(row.get("name_it", f"Regione {rid}")),
                "name_en":  str(row.get("name_en", f"Region {rid}")),
                "name_rm":  str(row.get("name_rm", f"Regiun {rid}")),
            }

    except Exception as e:
        log.warning("STAC-Metadaten konnten nicht geladen werden: %s", e)
        log.warning("Erstelle Platzhalter-Metadaten für Region-IDs 31–68.")
        for rid in range(31, 69):
            meta[rid] = {
                "id": rid, "name_de": f"Region {rid}",
                "name_fr": f"Région {rid}", "name_it": f"Regione {rid}",
                "name_en": f"Region {rid}", "name_rm": f"Regiun {rid}",
            }

    CACHE_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    log.info("  %d Regionen gespeichert.", len(meta))
    return meta


# ── 4. Regions-ID-Feld identifizieren ────────────────────────────────────────

def find_region_id_field(gdf: gpd.GeoDataFrame) -> str:
    """
    Findet das Feld in den Warnregionen, das die IDs 31–68 enthält.
    """
    log.info("Warnregionen-Felder: %s", list(gdf.columns))

    # Direkte Kandidaten
    for col in gdf.columns:
        cu = col.upper()
        if any(k in cu for k in ["DROUGHT_REGION", "REGION_ID", "WARN_ID", "WARNREGION"]):
            vals = pd.to_numeric(gdf[col], errors="coerce").dropna()
            if len(vals) > 0 and vals.between(30, 70).mean() > 0.5:
                log.info("  Regions-ID-Feld: '%s'", col)
                return col

    # Alle numerischen Felder prüfen
    for col in gdf.columns:
        if col == "geometry":
            continue
        vals = pd.to_numeric(gdf[col], errors="coerce").dropna()
        if len(vals) > 0 and vals.between(30, 70).mean() > 0.7:
            log.info("  Regions-ID-Feld (heuristisch): '%s'  "
                     "Stichprobe: %s", col, vals.head(5).tolist())
            return col

    # Letzte Chance: erstes nicht-geometrisches Feld
    non_geom = [c for c in gdf.columns if c != "geometry"]
    if non_geom:
        log.warning("Kein eindeutiges ID-Feld – verwende '%s'", non_geom[0])
        return non_geom[0]

    raise ValueError("Kein brauchbares ID-Feld in Warnregionen gefunden.")


# ── 5. Räumliche Verschneidungen ──────────────────────────────────────────────

def align_crs(base: gpd.GeoDataFrame, other: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if base.crs is None or other.crs is None:
        return other
    if base.crs.to_epsg() != other.crs.to_epsg():
        log.info("CRS-Anpassung: %s → %s", other.crs, base.crs)
        return other.to_crs(base.crs)
    return other


def join_gemeinden(
    gemeinden: gpd.GeoDataFrame,
    warnregionen: gpd.GeoDataFrame,
    rid_field: str,
    meta: dict[int, dict],
) -> list[dict]:
    """
    Jede Gemeinde erhält genau eine Warnregions-ID (via Zentroid).
    """
    log.info("Verschneide %d Gemeinden mit Warnregionen (Zentroid) …", len(gemeinden))
    warnregionen = align_crs(gemeinden, warnregionen)

    # Felder ermitteln
    cols = list(gemeinden.columns)
    f_bfs   = best_field(cols, ["BFS_NUMMER", "BFS", "NUMMER", "NUMBER", "ID"], "BFS-Nr")
    f_name  = best_field(cols, ["NAME", "GEMEINDENAME", "GEM_NAME"],            "Name")
    f_kanton= best_field(cols, ["KANTONSNUM", "KT_KZ", "KANTON", "CANTON"],     "Kanton")

    if not f_bfs:
        f_bfs = cols[0]
    if not f_name:
        f_name = cols[1] if len(cols) > 1 else cols[0]

    log.info("  BFS=%s  Name=%s  Kanton=%s", f_bfs, f_name, f_kanton)

    # Zentroide berechnen
    centroids = gemeinden[[f_bfs, f_name, f_kanton or "geometry", "geometry"]].copy()
    centroids["geometry"] = gemeinden.geometry.centroid

    # Spatial Join
    joined = gpd.sjoin(
        centroids,
        warnregionen[[rid_field, "geometry"]],
        how="left",
        predicate="within",
    )

    # Eindeutigkeit: bei mehrfacher Überlappung (z.B. Grenzpunkte) erste Zeile
    joined = joined.drop_duplicates(subset=[f_bfs])

    records = []
    missing = 0
    for _, row in joined.iterrows():
        rid_val = row.get(rid_field + "_right") or row.get(rid_field)
        if pd.isna(rid_val) if not isinstance(rid_val, str) else False:
            rid_val = None
            missing += 1

        rid = int(float(rid_val)) if rid_val is not None else None
        region_meta = meta.get(rid, {}) if rid else {}

        bfs = row[f_bfs]
        records.append({
            "bfs_nr":         int(bfs) if not pd.isna(bfs) else None,
            "name":           str(row[f_name]),
            "kanton":         str(row[f_kanton]) if f_kanton else None,
            "region_id":      rid,
            "region_name_de": region_meta.get("name_de"),
        })

    records.sort(key=lambda r: r["name"])
    if missing > 0:
        log.warning("  %d Gemeinden ohne Regions-Zuweisung (ausserhalb der Warnregionen).", missing)
    log.info("  %d Gemeinden verarbeitet.", len(records))
    return records


def join_kantone(
    kantone: gpd.GeoDataFrame,
    warnregionen: gpd.GeoDataFrame,
    rid_field: str,
    meta: dict[int, dict],
) -> list[dict]:
    """
    Jeder Kanton erhält eine Liste von Warnregionen mit Flächenanteil.
    Kantone überspan­nen häufig mehrere Warnregionen.
    """
    log.info("Verschneide %d Kantone mit Warnregionen (Fläche) …", len(kantone))
    warnregionen = align_crs(kantone, warnregionen)

    cols = list(kantone.columns)
    f_nr   = best_field(cols, ["KANTONSNUM", "KT_NR", "NUMMER", "NR", "ID"],   "KT-Nr")
    f_name = best_field(cols, ["NAME", "KANTONSNAME", "KT_NAME"],               "Name")
    f_abbr = best_field(cols, ["KUERZEL", "ABBR", "ABK", "KZ", "ABKUERZUNG"], "Kürzel")

    if not f_nr:
        f_nr = cols[0]
    if not f_name:
        f_name = cols[1] if len(cols) > 1 else cols[0]

    log.info("  Nr=%s  Name=%s  Kürzel=%s", f_nr, f_name, f_abbr)

    records = []
    for idx, kt_row in kantone.iterrows():
        kt_poly = kt_row.geometry
        if kt_poly is None or kt_poly.is_empty:
            continue

        kt_area = kt_poly.area
        region_shares = []

        for _, wr_row in warnregionen.iterrows():
            try:
                intersection = kt_poly.intersection(wr_row.geometry)
                share = intersection.area / kt_area if kt_area > 0 else 0
                if share < 0.005:  # < 0.5% ignorieren
                    continue
                rid_val = wr_row[rid_field]
                rid = int(float(rid_val)) if not pd.isna(rid_val) else None
                if rid:
                    region_shares.append({
                        "region_id":   rid,
                        "area_share":  round(share, 4),
                    })
            except (GEOSException, ValueError):
                pass

        region_shares.sort(key=lambda x: -x["area_share"])
        region_ids    = [r["region_id"] for r in region_shares]
        primary_id    = region_ids[0] if region_ids else None
        primary_meta  = meta.get(primary_id, {}) if primary_id else {}

        kt_nr = kt_row[f_nr]
        records.append({
            "kt_nr":                  int(kt_nr) if not pd.isna(kt_nr) else None,
            "name":                   str(kt_row[f_name]),
            "abbr":                   str(kt_row[f_abbr]) if f_abbr else None,
            "region_ids":             region_ids,
            "region_shares":          region_shares,
            "primary_region_id":      primary_id,
            "primary_region_name_de": primary_meta.get("name_de"),
        })

        log.debug("  %s: %d Regionen", kt_row[f_name], len(region_ids))

    records.sort(key=lambda r: r["name"])
    log.info("  %d Kantone verarbeitet.", len(records))
    return records


# ── 6. Suchhilfe: Name-Index ──────────────────────────────────────────────────

def build_search_index(
    gemeinden: list[dict],
    kantone: list[dict],
    meta: dict[int, dict],
) -> dict:
    """
    Erstellt einen flachen Suchindex für das Frontend:
    name_lower → {type, region_id, display_name, kanton}
    """
    index = {}

    for g in gemeinden:
        if g["region_id"]:
            key = g["name"].lower()
            index[key] = {
                "type":         "gemeinde",
                "display_name": g["name"],
                "kanton":       g.get("kanton"),
                "region_id":    g["region_id"],
                "bfs_nr":       g.get("bfs_nr"),
            }

    for k in kantone:
        key = k["name"].lower()
        index[key] = {
            "type":         "kanton",
            "display_name": k["name"],
            "abbr":         k.get("abbr"),
            "region_ids":   k["region_ids"],
            "primary_region_id": k["primary_region_id"],
        }
        # Auch Kürzel als Sucheintrag
        if k.get("abbr"):
            index[k["abbr"].lower()] = index[key]

    return index


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    t0 = time.time()
    log.info("=" * 60)
    log.info("DryBrief Suisse — Lookup-Builder")
    log.info("=" * 60)

    # 1. Geodaten laden
    gemeinden, kantone = load_boundaries()
    warnregionen       = load_warnregionen()
    meta               = load_regions_meta()

    log.info("")
    log.info("Übersicht:")
    log.info("  Gemeinden:    %d", len(gemeinden))
    log.info("  Kantone:      %d", len(kantone))
    log.info("  Warnregionen: %d", len(warnregionen))
    log.info("  Regionen-Meta:%d", len(meta))
    log.info("")

    # 2. Regions-ID-Feld
    rid_field = find_region_id_field(warnregionen)

    # 3. Verschneidungen
    gemeinde_lookup = join_gemeinden(gemeinden, warnregionen, rid_field, meta)
    kanton_lookup   = join_kantone(kantone, warnregionen, rid_field, meta)

    # 4. Suchindex
    search_index = build_search_index(gemeinde_lookup, kanton_lookup, meta)

    # 5. Ausgabe
    paths = {
        "gemeinden.json":    gemeinde_lookup,
        "kantone.json":      kanton_lookup,
        "regions_meta.json": meta,
        "search_index.json": search_index,
    }
    for fname, payload in paths.items():
        out = LOOKUP_DIR / fname
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        log.info("Gespeichert: %s  (%d Einträge)", out, len(payload))

    # Statistiken
    assigned = sum(1 for g in gemeinde_lookup if g["region_id"])
    total    = len(gemeinde_lookup)
    log.info("")
    log.info("Abdeckung Gemeinden: %d / %d (%.1f%%)",
             assigned, total, 100 * assigned / total if total else 0)

    region_counts: dict[int, int] = {}
    for g in gemeinde_lookup:
        if g["region_id"]:
            region_counts[g["region_id"]] = region_counts.get(g["region_id"], 0) + 1
    log.info("Gemeinden pro Region (Top 10): %s",
             sorted(region_counts.items(), key=lambda x: -x[1])[:10])

    log.info("")
    log.info("Gesamtlaufzeit: %.1f s", time.time() - t0)
    log.info("Lookup-Builder abgeschlossen.")


if __name__ == "__main__":
    main()
