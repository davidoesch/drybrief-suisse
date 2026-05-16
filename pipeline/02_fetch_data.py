#!/usr/bin/env python3
"""
02_fetch_data.py
================
Täglicher Download der aktuellen Trockenheitsdaten von der STAC-Plattform.

Quellen
-------
- trockenheitsdaten-numerisch_current.csv.zip
  → weekly_current_regions.csv   (aktueller CDI + Teilindizes)
  → weekly_forecast_regions.csv  (Ensemble-Prognose p10/p50/p90)

Ausgabe (data/raw/)
-------------------
  weekly_current_regions.csv
  weekly_forecast_regions.csv
  fetch_meta.json  — Zeitstempel und Datenstand
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
import zipfile
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Pfade ─────────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).parent.parent
RAW_DIR = ROOT / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

# ── URLs ──────────────────────────────────────────────────────────────────────
URL_CURRENT = (
    "https://data.geo.admin.ch/ch.bafu.trockenheitsdaten-numerisch/"
    "trockenheitsdaten-numerisch_current/"
    "trockenheitsdaten-numerisch_current.csv.zip"
)

# Dateien im ZIP die wir brauchen
TARGET_FILES = {
    "weekly_current_regions.csv":  "weekly_current_regions.csv",
    "weekly_forecast_regions.csv": "weekly_forecast_regions.csv",
    "monthly_current_regions.csv": "monthly_current_regions.csv",
}


def download_current_zip() -> bytes:
    log.info("Download: Trockenheitsdaten aktuell …")
    r = requests.get(URL_CURRENT, timeout=120)
    r.raise_for_status()
    log.info("  Empfangen: %.1f MB", len(r.content) / 1e6)
    return r.content


def extract_csv(zip_data: bytes, filename: str) -> pd.DataFrame | None:
    """Extrahiert eine CSV aus dem ZIP und liest sie als DataFrame."""
    with zipfile.ZipFile(BytesIO(zip_data)) as z:
        # Dateiname kann im ZIP ohne Pfadpräfix oder mit Pfadpräfix sein
        matches = [n for n in z.namelist() if n.endswith(filename)]
        if not matches:
            log.warning("  '%s' nicht im ZIP gefunden. Inhalt: %s",
                        filename, z.namelist())
            return None
        name = matches[0]
        log.info("  Lese: %s", name)
        raw = z.read(name)

    # Header-Kommentare überspringen (Zeilen die mit # beginnen)
    lines = raw.decode("utf-8").splitlines()
    data_lines = [l for l in lines if not l.startswith("#")]
    content    = "\n".join(data_lines)

    df = pd.read_csv(BytesIO(content.encode("utf-8")), sep=";")
    log.info("    %d Zeilen, %d Spalten", len(df), len(df.columns))
    log.info("    Spalten: %s", list(df.columns))
    return df


def validate_current(df: pd.DataFrame) -> bool:
    """Minimale Plausibilitätsprüfung."""
    required = ["drought_region_id", "measured_at", "cdi"]
    # Spaltenname kann "drought_region.id" oder "drought_region_id" sein
    cols_lower = [c.lower().replace(".", "_") for c in df.columns]
    for req in required:
        if req not in cols_lower:
            log.error("Pflichtfeld '%s' fehlt in weekly_current. Felder: %s",
                      req, list(df.columns))
            return False

    # Wertebereich CDI
    cdi_col = df.columns[[c.lower().replace(".", "_") == "cdi" for c in df.columns]][0]
    valid_cdi = df[cdi_col].dropna().between(1, 5).all()
    if not valid_cdi:
        log.warning("CDI-Werte ausserhalb 1–5: %s", df[cdi_col].unique())

    return True


def validate_forecast(df: pd.DataFrame) -> bool:
    required = ["cdi_p50"]
    cols_lower = [c.lower() for c in df.columns]
    for req in required:
        if req not in cols_lower:
            log.error("Pflichtfeld '%s' fehlt in weekly_forecast.", req)
            return False
    return True


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalisiert Spaltennamen (z.B. 'drought_region.id' → 'drought_region_id')."""
    df.columns = [c.replace(".", "_").strip() for c in df.columns]
    return df


def get_latest_date(df: pd.DataFrame, date_col: str) -> str | None:
    if date_col not in df.columns:
        return None
    try:
        return str(df[date_col].max())
    except Exception:
        return None


def save_fetch_meta(current_df: pd.DataFrame, forecast_df: pd.DataFrame) -> None:
    meta = {
        "fetched_at":           datetime.now(timezone.utc).isoformat(),
        "current_valid_at":     get_latest_date(current_df,  "measured_at"),
        "forecast_valid_from":  get_latest_date(forecast_df, "valid_at"),
        "source_url":           URL_CURRENT,
        "n_current_rows":       len(current_df),
        "n_forecast_rows":      len(forecast_df),
        "region_ids_current":   sorted(
            current_df["drought_region_id"].dropna().astype(int).unique().tolist()
        ) if "drought_region_id" in current_df.columns else [],
    }
    out = RAW_DIR / "fetch_meta.json"
    out.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    log.info("Metadaten: %s", out)
    log.info("  Aktueller Datenstand: %s", meta["current_valid_at"])
    log.info("  Prognose ab:          %s", meta["forecast_valid_from"])


def cleanup_old_briefings(max_age_days: int = 28) -> None:
    """Löscht datierte Briefing-Ordner (YYYY-MM-DD) die älter als max_age_days sind."""
    briefings_dir = ROOT / "data" / "briefings"
    if not briefings_dir.exists():
        return
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=max_age_days)
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    removed = 0
    for entry in briefings_dir.iterdir():
        if not entry.is_dir() or not date_pattern.match(entry.name):
            continue
        try:
            folder_date = datetime.strptime(entry.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if folder_date < cutoff:
            shutil.rmtree(entry)
            log.info("  Gelöscht (älter als %d Tage): %s", max_age_days, entry.name)
            removed += 1
    if removed:
        log.info("Aufgeräumt: %d alte Briefing-Ordner gelöscht.", removed)
    else:
        log.info("Aufräumen: keine alten Briefing-Ordner gefunden.")


def main() -> None:
    t0 = time.time()
    log.info("=" * 60)
    log.info("DryBrief Suisse — Datenabruf")
    log.info("=" * 60)

    # Alte Briefings aufräumen (> 4 Wochen)
    cleanup_old_briefings(max_age_days=28)

    # Download
    zip_data = download_current_zip()

    # CSV extrahieren und validieren
    frames = {}
    for zip_name, save_name in TARGET_FILES.items():
        df = extract_csv(zip_data, zip_name)
        if df is None:
            if "forecast" in zip_name:
                log.warning("Forecast-Datei fehlt – weiter ohne Prognose.")
                continue
            else:
                raise RuntimeError(f"Pflichtdatei '{zip_name}' fehlt im ZIP.")
        df = normalize_columns(df)
        frames[save_name] = df

        # Speichern
        out = RAW_DIR / save_name
        df.to_csv(out, index=False, sep=";")
        log.info("Gespeichert: %s", out)

    # Validierung
    if "weekly_current_regions.csv" in frames:
        validate_current(frames["weekly_current_regions.csv"])
    if "weekly_forecast_regions.csv" in frames:
        validate_forecast(frames["weekly_forecast_regions.csv"])

    # Metadaten
    save_fetch_meta(
        frames.get("weekly_current_regions.csv", pd.DataFrame()),
        frames.get("weekly_forecast_regions.csv", pd.DataFrame()),
    )

    log.info("Laufzeit: %.1f s", time.time() - t0)
    log.info("Datenabruf abgeschlossen.")


if __name__ == "__main__":
    main()
