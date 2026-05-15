#!/usr/bin/env python3
"""
03_generate_briefings.py
========================
Regelbasierte Textgenerierung: Trockenheitsdaten → Briefing-JSON

Für jede der 38 Warnregionen wird ein standardisiertes Briefing-JSON
erzeugt, das vom Frontend direkt geladen werden kann.

Terminologie gemäss: «Empfohlene Terminologie für Trockenheitsbulletin»
(BAFU/MeteoSchweiz, 2024)

Ausgabe (data/briefings/regions/)
----------------------------------
  {region_id}.json   z.B. 31.json, 32.json, …
  index.json         Übersicht aller Regionen mit Kurzstatus
  generated_at.json  Zeitstempel der letzten Generierung
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Pfade ─────────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).parent.parent
RAW_DIR       = ROOT / "data" / "raw"
LOOKUP_DIR    = ROOT / "data" / "lookups"
BRIEFING_DIR  = ROOT / "data" / "briefings" / "regions"
BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR    = Path(__file__).parent / "config"

# ── Konfiguration laden ───────────────────────────────────────────────────────

def load_thresholds() -> dict:
    path = CONFIG_DIR / "thresholds.json"
    return json.loads(path.read_text())


def load_regions_meta() -> dict[int, dict]:
    path = LOOKUP_DIR / "regions_meta.json"
    if not path.exists():
        log.warning("regions_meta.json fehlt — verwende Platzhalter.")
        return {rid: {"id": rid, "name_de": f"Region {rid}",
                      "name_fr": f"Région {rid}", "name_it": f"Regione {rid}",
                      "name_en": f"Region {rid}"}
                for rid in range(31, 69)}
    raw = json.loads(path.read_text())
    return {int(k): v for k, v in raw.items()}


# ── Daten laden ───────────────────────────────────────────────────────────────

def load_current() -> pd.DataFrame:
    path = RAW_DIR / "weekly_current_regions.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} fehlt. Zuerst 02_fetch_data.py ausführen."
        )
    df = pd.read_csv(path, sep=";")
    df.columns = [c.replace(".", "_").strip() for c in df.columns]
    log.info("Aktuelle Daten: %d Zeilen", len(df))
    return df


def load_forecast() -> pd.DataFrame | None:
    path = RAW_DIR / "weekly_forecast_regions.csv"
    if not path.exists():
        log.warning("Forecast-Datei fehlt — Prognose wird weggelassen.")
        return None
    df = pd.read_csv(path, sep=";")
    df.columns = [c.replace(".", "_").strip() for c in df.columns]
    log.info("Forecast-Daten: %d Zeilen", len(df))
    return df


# ── Regelengine ───────────────────────────────────────────────────────────────

class RuleEngine:
    """
    Deterministische Textgenerierung auf Basis definierter Schwellenwerte.
    Keine freie KI-Generierung. Alle Aussagen sind datenbasiert und
    nachvollziehbar.
    """

    def __init__(self, thresholds: dict):
        self.t = thresholds

    def cdi_label(self, cdi: int) -> str:
        return self.t["cdi"].get(str(cdi), {}).get("label_de", f"CDI {cdi}")

    def cdi_color(self, cdi: int) -> str:
        return self.t["cdi"].get(str(cdi), {}).get("color", "grey")

    def warning_level(self, cdi: int) -> int:
        return self.t["cdi"].get(str(cdi), {}).get("warning_level", cdi)

    def index_label(self, index_type: str, value: int) -> str:
        return self.t.get(index_type, {}).get(str(value), f"Stufe {value}")

    def trend_label(self, current_cdi: int, forecast_cdi: int | None) -> str:
        if forecast_cdi is None:
            return "unbekannt"
        diff = forecast_cdi - current_cdi
        if diff >= 2:
            return "stark_verschlechternd"
        elif diff == 1:
            return "verschlechternd"
        elif diff == 0:
            return "stabil"
        elif diff == -1:
            return "verbessernd"
        else:
            return "stark_verbessernd"

    def trend_label_de(self, trend: str) -> str:
        return {
            "stark_verschlechternd": "starke Verschlechterung erwartet",
            "verschlechternd":       "Verschlechterung erwartet",
            "stabil":                "stabile Lage erwartet",
            "verbessernd":           "Entspannung erwartet",
            "stark_verbessernd":     "deutliche Entspannung erwartet",
            "unbekannt":             "keine Prognose verfügbar",
        }.get(trend, trend)

    def generate_summary(
        self,
        region_name: str,
        cdi: int,
        indicators: dict,
        trend: str,
    ) -> str:
        """
        Generiert eine sachliche Kurzzusammenfassung.
        Ausschliesslich regelbasiert — keine freie Textgenerierung.
        """
        parts = []

        # Lagebeurteilung
        cdi_txt = self.cdi_label(cdi)
        if cdi == 1:
            parts.append(
                f"In der Region {region_name} sind aktuell keine aussergewöhnlichen "
                f"Trockenheitssignale feststellbar."
            )
        elif cdi == 2:
            parts.append(
                f"In der Region {region_name} zeichnet sich eine leichte Trockenheit ab."
            )
        elif cdi == 3:
            parts.append(
                f"Die Region {region_name} ist aktuell trocken."
            )
        elif cdi == 4:
            parts.append(
                f"Die Region {region_name} weist eine grosse Trockenheit auf."
            )
        elif cdi == 5:
            parts.append(
                f"In der Region {region_name} herrscht eine extreme Trockenheit."
            )

        # Auffällige Einzelindikatoren
        notable = []
        for ind_key, ind_val in indicators.items():
            idx = ind_val.get("index", 1)
            if idx >= 3:
                notable.append(ind_val.get("label_de", ""))
        if notable:
            parts.append(
                "Festgestellt wird: " + "; ".join(notable) + "."
            )

        # Prognose
        trend_txt = self.trend_label_de(trend)
        parts.append(f"Prognose: {trend_txt.capitalize()}.")

        return " ".join(parts)

    def generate_recommendations(self, cdi: int) -> list[str]:
        base = self.t.get("recommendations", {})
        recs = base.get("all", [])
        level_recs = base.get(str(cdi), [])
        return recs + level_recs

    def generate_impacts(self, cdi: int) -> list[str]:
        impacts_map = self.t.get("impacts", {})
        return impacts_map.get(str(cdi), [])


# ── Prognose-Block ────────────────────────────────────────────────────────────

def get_latest_row(df: pd.DataFrame, region_id: int, date_col: str) -> dict | None:
    """Gibt die neueste Zeile für eine Region zurück."""
    rid_col = "drought_region_id"
    sub = df[df[rid_col] == region_id]
    if sub.empty:
        return None
    latest = sub.sort_values(date_col, ascending=False).iloc[0]
    return latest.to_dict()


def extract_forecast_uncertainty(row: dict) -> str:
    """Beschreibt die Prognose-Unsicherheit via P10/P90-Spanne."""
    p10 = row.get("cdi_p10")
    p90 = row.get("cdi_p90")
    if p10 is None or p90 is None:
        return "Prognose-Unsicherheit nicht quantifizierbar."
    span = int(p90) - int(p10)
    if span == 0:
        return "Die Ensemble-Prognose zeigt eine einheitliche Entwicklung."
    elif span == 1:
        return "Die Ensemble-Prognose zeigt eine geringe Unsicherheit."
    elif span == 2:
        return "Die Ensemble-Prognose zeigt eine mässige Unsicherheit."
    else:
        return (
            "Die Ensemble-Prognose weist eine grosse Spannweite auf "
            f"(CDI P10={int(p10)}, P90={int(p90)}). "
            "Aktuelle Lage mit erhöhter Vorsicht interpretieren."
        )


# ── Briefing-Zusammenbau ──────────────────────────────────────────────────────

def build_briefing(
    region_id: int,
    current_row: dict,
    forecast_row: dict | None,
    meta: dict,
    engine: RuleEngine,
    thresholds: dict,
) -> dict:
    """Baut das vollständige Briefing-JSON für eine Region."""

    # CDI (aktuell)
    cdi_raw = current_row.get("cdi")
    cdi = int(float(cdi_raw)) if cdi_raw is not None and not _isnan(cdi_raw) else 1
    cdi = max(1, min(5, cdi))

    # Prognose-CDI
    forecast_cdi = None
    forecast_cdi_p10 = None
    forecast_cdi_p90 = None
    forecast_uncertainty = "Keine Prognosedaten verfügbar."
    if forecast_row:
        p50 = forecast_row.get("cdi_p50")
        if p50 is not None and not _isnan(p50):
            forecast_cdi = int(float(p50))
            forecast_cdi = max(1, min(5, forecast_cdi))
        p10 = forecast_row.get("cdi_p10")
        p90 = forecast_row.get("cdi_p90")
        if p10 and not _isnan(p10):
            forecast_cdi_p10 = int(float(p10))
        if p90 and not _isnan(p90):
            forecast_cdi_p90 = int(float(p90))
        forecast_uncertainty = extract_forecast_uncertainty(forecast_row)

    # Trend
    trend = engine.trend_label(cdi, forecast_cdi)

    # Kernindikatoren
    indicators = {
        "precip_1m": {
            "key":      "precip_1m",
            "label_de": "30-Tage-Niederschlag",
            "index":    _safe_int(current_row.get("precip_1m_index")),
            "label_status_de": engine.index_label(
                "precip_index", _safe_int(current_row.get("precip_1m_index"))
            ),
            "value_mm": _safe_float(current_row.get("precip_sum_1m")),
            "unit":     "mm",
            "source":   "MeteoSchweiz / BAFU",
        },
        "precip_3m": {
            "key":      "precip_3m",
            "label_de": "90-Tage-Niederschlag",
            "index":    _safe_int(current_row.get("precip_3m_index")),
            "label_status_de": engine.index_label(
                "precip_index", _safe_int(current_row.get("precip_3m_index"))
            ),
            "value_mm": _safe_float(current_row.get("precip_sum_3m")),
            "unit":     "mm",
            "source":   "MeteoSchweiz / BAFU",
        },
        "hydro": {
            "key":      "hydro",
            "label_de": "Abfluss / Pegel",
            "index":    _safe_int(current_row.get("hydro_index")),
            "label_status_de": engine.index_label(
                "hydro_index", _safe_int(current_row.get("hydro_index"))
            ),
            "value_mm": None,
            "unit":     None,
            "source":   "BAFU Hydrodaten",
        },
        "soil_moisture": {
            "key":      "soil_moisture",
            "label_de": "Bodenfeuchte",
            "index":    _safe_int(current_row.get("soil_moisture_index")),
            "label_status_de": engine.index_label(
                "soil_moisture_index",
                _safe_int(current_row.get("soil_moisture_index"))
            ),
            "value_pct": _safe_float(current_row.get("soil_moisture_ufc")),
            "unit":      "% nFK",
            "source":    "MeteoSchweiz / swisstopo",
        },
    }

    # Zusammenfassung
    region_name = meta.get("name_de", f"Region {region_id}")
    summary_de  = engine.generate_summary(region_name, cdi, indicators, trend)

    # Auswirkungen und Empfehlungen
    impacts         = engine.generate_impacts(cdi)
    recommendations = engine.generate_recommendations(cdi)

    # Quellen-Block
    sources = [
        {
            "name":      "Nationale Trockenheitsplattform (BAFU)",
            "url":       "https://www.trockenheit.admin.ch",
            "api_url":   (
                "https://data.geo.admin.ch/ch.bafu.trockenheitsdaten-numerisch/"
                "trockenheitsdaten-numerisch_current/"
                "trockenheitsdaten-numerisch_current.csv.zip"
            ),
            "data_date": str(current_row.get("measured_at", "unbekannt")),
        },
        {
            "name":    "MeteoSchweiz Open Data",
            "url":     "https://www.meteoswiss.admin.ch",
            "api_url": "https://data.geo.admin.ch/api/stac/v1/",
        },
        {
            "name":    "BAFU Hydrodaten",
            "url":     "https://www.hydrodaten.admin.ch",
        },
    ]

    return {
        # Identifikation
        "region_id":      region_id,
        "region_name_de": region_name,
        "region_name_fr": meta.get("name_fr"),
        "region_name_it": meta.get("name_it"),
        "region_name_en": meta.get("name_en"),

        # Zeitstempel
        "measured_at":    str(current_row.get("measured_at", "unbekannt")),
        "generated_at":   datetime.now(timezone.utc).isoformat(),

        # Lagebeurteilung
        "cdi":            cdi,
        "cdi_label_de":   engine.cdi_label(cdi),
        "warning_level":  engine.warning_level(cdi),
        "color":          engine.cdi_color(cdi),
        "summary_de":     summary_de,

        # Trend und Prognose
        "trend":               trend,
        "trend_label_de":      engine.trend_label_de(trend),
        "forecast_cdi_p50":    forecast_cdi,
        "forecast_cdi_p10":    forecast_cdi_p10,
        "forecast_cdi_p90":    forecast_cdi_p90,
        "forecast_valid_at":   str(forecast_row.get("valid_at")) if forecast_row else None,
        "forecast_uncertainty":forecast_uncertainty,

        # Kernindikatoren
        "indicators": indicators,

        # Auswirkungen (vordefinierte Textbausteine)
        "impacts_de":           impacts,

        # Empfehlungen
        "recommendations_de":   recommendations,

        # Rohdaten (für Transparenz und Weiterverarbeitung)
        "raw": {
            "precip_1m_index":    _safe_int(current_row.get("precip_1m_index")),
            "precip_3m_index":    _safe_int(current_row.get("precip_3m_index")),
            "precip_24m_index":   _safe_int(current_row.get("precip_24m_index")),
            "hydro_index":        _safe_int(current_row.get("hydro_index")),
            "soil_moisture_index":_safe_int(current_row.get("soil_moisture_index")),
            "spi_1m":             _safe_float(current_row.get("spi_1m")),
            "spi_3m":             _safe_float(current_row.get("spi_3m")),
            "soil_moisture_ufc":  _safe_float(current_row.get("soil_moisture_ufc")),
            "vhi":                _safe_float(current_row.get("vhi")),
        },

        # Quellen
        "sources": sources,

        # Haftungsausschluss
        "disclaimer_de": (
            "Dieses Briefing wird automatisch aus offiziellen Bundesdaten generiert. "
            "Es ersetzt keine fachliche Beurteilung durch zuständige Behörden. "
            "Für rechtsverbindliche Trockenheitswarnungen: "
            "www.trockenheit.admin.ch"
        ),
    }


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

class _JsonEncoder(json.JSONEncoder):
    def default(self, o: Any):
        import numpy as np
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        return super().default(o)


def _isnan(v: Any) -> bool:
    try:
        import math
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return False


def _safe_int(v: Any, default: int = 1) -> int:
    try:
        if _isnan(v):
            return default
        return max(1, min(5, int(float(v))))
    except (TypeError, ValueError):
        return default


def _safe_float(v: Any) -> float | None:
    try:
        if _isnan(v):
            return None
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    t0 = time.time()
    log.info("=" * 60)
    log.info("DryBrief Suisse — Briefing-Generierung")
    log.info("=" * 60)

    thresholds = load_thresholds()
    meta       = load_regions_meta()
    engine     = RuleEngine(thresholds)
    current_df = load_current()
    forecast_df= load_forecast()

    # Letzte Woche aus current (neuestes Datum)
    date_col_curr = "measured_at"
    latest_date   = current_df[date_col_curr].max()
    log.info("Aktuellster Datenstand: %s", latest_date)
    current_df    = current_df[current_df[date_col_curr] == latest_date]

    # Letzte Prognose
    if forecast_df is not None:
        fc_date_col  = "valid_at"
        fc_latest    = forecast_df[fc_date_col].max()
        forecast_df  = forecast_df[forecast_df[fc_date_col] == fc_latest]
        log.info("Prognose-Datum:        %s", fc_latest)

    # Pro Region ein Briefing
    all_region_ids = sorted(current_df["drought_region_id"].dropna().astype(int).unique())
    log.info("Generiere Briefings für %d Regionen …", len(all_region_ids))

    index_entries = []
    for region_id in all_region_ids:
        curr_row = get_latest_row(current_df, region_id, date_col_curr)
        if curr_row is None:
            log.warning("  Region %d: keine aktuellen Daten.", region_id)
            continue

        fc_row = None
        if forecast_df is not None:
            fc_row = get_latest_row(forecast_df, region_id, "valid_at")

        region_meta = meta.get(region_id, {
            "id": region_id, "name_de": f"Region {region_id}",
            "name_fr": f"Région {region_id}", "name_it": f"Regione {region_id}",
            "name_en": f"Region {region_id}",
        })

        briefing = build_briefing(
            region_id, curr_row, fc_row, region_meta, engine, thresholds
        )

        # Briefing-JSON speichern
        out = BRIEFING_DIR / f"{region_id}.json"
        out.write_text(json.dumps(briefing, ensure_ascii=False, indent=2, cls=_JsonEncoder))

        # Index-Eintrag
        index_entries.append({
            "region_id":      region_id,
            "name_de":        briefing["region_name_de"],
            "cdi":            briefing["cdi"],
            "cdi_label_de":   briefing["cdi_label_de"],
            "warning_level":  briefing["warning_level"],
            "color":          briefing["color"],
            "trend":          briefing["trend"],
            "measured_at":    briefing["measured_at"],
        })

        log.info("  Region %d (%s): CDI=%d %s, Trend=%s",
                 region_id, briefing["region_name_de"],
                 briefing["cdi"], briefing["color"], briefing["trend"])

    # Gesamt-Index
    index_path = ROOT / "data" / "briefings" / "index.json"
    index_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regions":      sorted(index_entries, key=lambda x: x["region_id"]),
    }, ensure_ascii=False, indent=2, cls=_JsonEncoder))
    log.info("Index gespeichert: %s", index_path)

    # Zeitstempel-Datei für GitHub Actions / Cache-Busting
    ts_path = ROOT / "data" / "briefings" / "generated_at.json"
    ts_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_regions":    len(index_entries),
    }, cls=_JsonEncoder))

    log.info("")
    log.info("CDI-Verteilung:")
    for cdi_val in range(1, 6):
        count = sum(1 for e in index_entries if e["cdi"] == cdi_val)
        label = engine.cdi_label(cdi_val)
        bar   = "█" * count
        log.info("  CDI %d (%s): %s %d", cdi_val, label, bar, count)

    log.info("Laufzeit: %.1f s", time.time() - t0)
    log.info("Briefing-Generierung abgeschlossen.")


if __name__ == "__main__":
    main()
