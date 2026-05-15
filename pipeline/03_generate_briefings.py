#!/usr/bin/env python3
"""03_generate_briefings.py v2 — Regelbasierte Textgenerierung"""
from __future__ import annotations
import argparse, json, logging, re, time
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

class _J(json.JSONEncoder):
    def default(self, o):
        import numpy as np
        if isinstance(o, np.integer): return int(o)
        if isinstance(o, np.floating): return float(o)
        return super().default(o)

ROOT         = Path(__file__).parent.parent
RAW_DIR      = ROOT / "data" / "raw"
LOOKUP_DIR   = ROOT / "data" / "lookups"
BRIEFING_DIR = ROOT / "data" / "briefings" / "regions"
BRIEFING_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR   = Path(__file__).parent / "config"
TROCKENHEIT_BASE = "https://www.trockenheit.admin.ch/de/regionen"

# ── Slug / URL ────────────────────────────────────────────────────────────────
def make_slug(region_id: int, name_de: str) -> str:
    s = name_de.lower().replace("ä","ae").replace("ö","oe").replace("ü","ue").replace("ß","ss")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return f"{region_id}-{s}"

def r_url(slug: str, anchor: str = "") -> str:
    base = f"{TROCKENHEIT_BASE}/{slug}/aktuelle-lage"
    return f"{base}#{anchor}" if anchor else base

# ── Textfunktionen (lesen aus thresholds.json) ────────────────────────────────
def spi_to_comparison(spi: float | None, T: dict) -> str:
    if spi is None:
        return T.get("spi_nodata", "Keine Vergleichsdaten verfügbar")
    for threshold, text in T.get("spi_comparisons", []):
        if spi > threshold:
            return text
    return T.get("spi_default", "Extrem wenig Niederschlag, historisch seltenes Ereignis")

def soil_to_plain(ufc: float | None, T: dict) -> str:
    if ufc is None:
        return T.get("soil_nodata", "Keine Messdaten verfügbar")
    for threshold, text in T.get("soil_plain_pct", []):
        if ufc >= threshold:
            return text
    return T.get("soil_default", "Böden sehr trocken, erheblicher Wassermangel")

def hydro_to_plain(idx: int, T: dict) -> str:
    return T.get("hydro_plain", {}).get(str(idx), "Keine Daten")

def hydro_flow_range(idx: int, T: dict) -> str:
    return T.get("hydro_flow_range", {}).get(str(idx), "keine Angabe")

def fc_summary(cdi: int, fc_cdi: int | None, T: dict, noun_de: str = "") -> str:
    tmpl = T.get("fc_summary_de", {})
    if fc_cdi is None:
        return tmpl.get("no_forecast", "Für die nächsten Wochen liegt aktuell keine Prognose vor.")
    d = fc_cdi - cdi
    if d >= 2:
        return tmpl.get("worsen_strong", "Die Lage wird sich deutlich verschlechtern, {noun} erwartet (Stufe {fc}).").format(noun=noun_de, fc=fc_cdi, cur=cdi)
    if d == 1:
        return tmpl.get("worsen", "Eine leichte Verschlechterung wird erwartet (Stufe {cur} nach {fc}).").format(cur=cdi, fc=fc_cdi)
    if d == 0:
        return tmpl.get("stable", "Die Lage bleibt voraussichtlich stabil (Stufe {cur}).").format(cur=cdi, fc=fc_cdi)
    if d == -1:
        return tmpl.get("improve", "Eine leichte Entspannung wird erwartet (Stufe {cur} nach {fc}).").format(cur=cdi, fc=fc_cdi)
    return tmpl.get("improve_strong", "Eine deutliche Entspannung wird erwartet (Stufe {cur} nach {fc}).").format(cur=cdi, fc=fc_cdi)

def parse_date_flex(series: "pd.Series") -> "pd.Series":
    """Parse dates supporting DD.MM.YYYY (BAFU CSV format) and ISO YYYY-MM-DD."""
    parsed = pd.to_datetime(series, format="%d.%m.%Y", errors="coerce")
    mask = parsed.isna() & series.notna()
    if mask.any():
        parsed = parsed.where(~mask, pd.to_datetime(series[mask], errors="coerce"))
    return parsed

def age_days(measured_at_str) -> int | None:
    try: return (date.today() - date.fromisoformat(str(measured_at_str))).days
    except: return None

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────
def _isnan(v):
    try: import math; return math.isnan(float(v))
    except: return False
def _si(v, default=1):
    try: return max(1, min(5, int(float(v)))) if not _isnan(v) else default
    except: return default
def _sf(v):
    try: return round(float(v),2) if not _isnan(v) else None
    except: return None

# ── Config ────────────────────────────────────────────────────────────────────
def load_thresholds(): return json.loads((CONFIG_DIR/"thresholds.json").read_text())
def load_meta() -> dict[int,dict]:
    p = LOOKUP_DIR/"regions_meta.json"
    if not p.exists(): return {r:{"id":r,"name_de":f"Region {r}","name_fr":f"Région {r}","name_it":f"Regione {r}","name_en":f"Region {r}"} for r in range(31,69)}
    return {int(k):v for k,v in json.loads(p.read_text()).items()}

# ── Engine ────────────────────────────────────────────────────────────────────
class Engine:
    def __init__(self, t): self.t = t
    def _cdi(self, c):     return self.t["cdi"].get(str(c), {})
    def cdi_label(self, c):   return self._cdi(c).get("label_de",   f"CDI {c}")
    def cdi_noun(self, c):    return self._cdi(c).get("noun_de",    f"Trockenheit Stufe {c}")
    def cdi_color(self, c):   return self._cdi(c).get("color",      "grey")
    def cdi_hex(self, c):     return self._cdi(c).get("color_hex",  "#6b6b6b")
    def cdi_bg(self, c):      return self._cdi(c).get("bg_hex",     "#f5f5f5")
    def cdi_fg(self, c):      return self._cdi(c).get("fg_hex",     "#1a1a1a")
    def cdi_emoji(self, c):   return self._cdi(c).get("emoji",      "")
    def cdi_sub(self, c):     return self._cdi(c).get("sub_de",     "")
    def warn_level(self, c):  return self._cdi(c).get("warning_level", c)
    def idx_label(self, t, v):return self.t.get(t, {}).get(str(v), f"Stufe {v}")
    def trend_key(self, cur, fc):
        if fc is None: return "unbekannt"
        d = fc - cur
        if d >= 2:  return "stark_verschlechternd"
        if d == 1:  return "verschlechternd"
        if d == 0:  return "stabil"
        if d == -1: return "verbessernd"
        return "stark_verbessernd"
    def trend_de(self, t):
        return self.t.get("trend", {}).get("labels_de", {}).get(t, t)
    def trend_arrow(self, t):
        return self.t.get("trend", {}).get("arrows", {}).get(t, "?")
    def summary(self, name, cdi):
        tmpl = self.t.get("cdi_summary_de", {}).get(str(cdi), f"Trockenheitsstufe {cdi}.")
        return tmpl.replace("{name}", name)
    def impacts(self, c): return self.t.get("impacts", {}).get(str(c), [])
    def recs(self, c): return self.t.get("recommendations", {}).get("all", []) + self.t.get("recommendations", {}).get(str(c), [])
    def recs_simple(self, c): return self.t.get("recommendations_simple", {}).get("all", []) + self.t.get("recommendations_simple", {}).get(str(c), [])
    def ind_icon(self, key): return self.t.get("indicators", {}).get(key, {}).get("icon", "")
    def ind_label(self, key): return self.t.get("indicators", {}).get(key, {}).get("label_de", key)

# ── Briefing-Zusammenbau ──────────────────────────────────────────────────────
def get_latest(df, rid, date_col):
    sub = df[df["drought_region_id"]==rid]
    return sub.sort_values(date_col, ascending=False).iloc[0].to_dict() if not sub.empty else None

def build(rid, curr, fc, meta, eng, profiles=None):
    T = eng.t
    cdi = _si(curr.get("cdi"))
    fc_cdi = None
    fc_valid = None
    if fc:
        p50 = fc.get("cdi_p50")
        if p50 is not None and not _isnan(p50):
            fc_cdi = max(1, min(5, int(float(p50))))
        fc_valid = str(fc.get("valid_at", "")) or None

    trend = eng.trend_key(cdi, fc_cdi)
    name  = meta.get("name_de", f"Region {rid}")
    slug  = make_slug(rid, name)
    measured = str(curr.get("measured_at", "unbekannt"))
    adys = age_days(measured)

    spi_1m = _sf(curr.get("spi_1m"))
    spi_3m = _sf(curr.get("spi_3m"))
    p1 = _si(curr.get("precip_1m_index"))
    p3 = _si(curr.get("precip_3m_index"))
    hydro_idx = _si(curr.get("hydro_index"))
    soil_idx  = _si(curr.get("soil_moisture_index"))
    soil_ufc  = _sf(curr.get("soil_moisture_ufc"))

    ind_meta = T.get("indicators", {})

    muni = eval_municipality_rules(profiles or [], cdi, T)

    indicators = {
        "precip": {
            "key":      "precip",
            "label_de": eng.ind_label("precip"),
            "icon":     eng.ind_icon("precip"),
            "link":     r_url(slug, ind_meta.get("precip", {}).get("link_anchor", "precipitation")),
            "short_term": {
                "days":           30,
                "index":          p1,
                "value_mm":       _sf(curr.get("precip_sum_1m")),
                "spi":            spi_1m,
                "comparison_de":  spi_to_comparison(spi_1m, T),
                "label_status_de": eng.idx_label("precip_index", p1),
            },
            "long_term": {
                "days":           90,
                "index":          p3,
                "value_mm":       _sf(curr.get("precip_sum_3m")),
                "spi":            spi_3m,
                "comparison_de":  spi_to_comparison(spi_3m, T),
                "label_status_de": eng.idx_label("precip_index", p3),
            },
        },
        "hydro": {
            "key":             "hydro",
            "label_de":        eng.ind_label("hydro"),
            "icon":            eng.ind_icon("hydro"),
            "index":           hydro_idx,
            "label_status_de": eng.idx_label("hydro_index", hydro_idx),
            "plain_de":        hydro_to_plain(hydro_idx, T),
            "flow_range_de":   hydro_flow_range(hydro_idx, T),
            # Actual flow data (m3/s) — populated when data is available
            "value_q_m3s":         None,
            "value_q_norm_min":    None,
            "value_q_norm_max":    None,
            "value_q_norm_period": None,
            # Relative flow ratio — tries several candidate column names
            "value_q_rel": _sf(
                curr.get("q_rel_mean") or curr.get("q_fraction") or
                curr.get("runoff_fraction") or curr.get("hydro_value")
            ),
            "link":   r_url(slug, ind_meta.get("hydro", {}).get("link_anchor", "discharge")),
            "source": "BAFU Hydrodaten",
        },
        "soil_moisture": {
            "key":             "soil_moisture",
            "label_de":        eng.ind_label("soil_moisture"),
            "icon":            eng.ind_icon("soil_moisture"),
            "index":           soil_idx,
            "value_pct":       soil_ufc,
            "label_status_de": eng.idx_label("soil_moisture_index", soil_idx),
            "plain_de":        soil_to_plain(soil_ufc, T),
            "link":   r_url(slug, ind_meta.get("soil_moisture", {}).get("link_anchor", "moisture")),
            "source": "MeteoSchweiz",
        },
    }

    return {
        "region_id":       rid,
        "region_name_de":  name,
        "region_name_fr":  meta.get("name_fr"),
        "region_name_it":  meta.get("name_it"),
        "region_name_en":  meta.get("name_en"),
        "region_slug":     slug,
        "region_url":      r_url(slug),
        "region_url_cdi":  r_url(slug, "index"),
        "measured_at":     measured,
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "data_age_days":   adys,
        "cdi":             cdi,
        "cdi_label_de":    eng.cdi_label(cdi),
        "cdi_noun_de":     eng.cdi_noun(cdi),
        "cdi_emoji":       eng.cdi_emoji(cdi),
        "cdi_bg_hex":      eng.cdi_bg(cdi),
        "cdi_fg_hex":      eng.cdi_fg(cdi),
        "cdi_sub_de":      eng.cdi_sub(cdi),
        "warning_level":   eng.warn_level(cdi),
        "color":           eng.cdi_color(cdi),
        "color_hex":       eng.cdi_hex(cdi),
        "summary_de":      eng.summary(name, cdi),
        "trend":           trend,
        "trend_label_de":  eng.trend_de(trend),
        "trend_arrow":     eng.trend_arrow(trend),
        "forecast_cdi_p50":    fc_cdi,
        "forecast_valid_at":   fc_valid,
        "forecast_summary_de": fc_summary(cdi, fc_cdi, T, eng.cdi_noun(fc_cdi) if fc_cdi else ""),
        "indicators":      indicators,
        "impacts_de":      eng.impacts(cdi) + muni.get("impact", []),
        "recommendations_de": eng.recs(cdi) + muni.get("recommendation", []),
        "recommendations_simple_de": eng.recs_simple(cdi) + muni.get("recommendation", []),
        "raw": {
            "precip_1m_index":    p1,
            "precip_3m_index":    p3,
            "precip_24m_index":   _si(curr.get("precip_24m_index")),
            "hydro_index":        hydro_idx,
            "soil_moisture_index": soil_idx,
            "spi_1m":             spi_1m,
            "spi_3m":             spi_3m,
            "soil_moisture_ufc":  soil_ufc,
            "vhi":                _sf(curr.get("vhi")),
        },
        "sources": [
            {"name": "Trockenheitsplattform BAFU", "url": "https://www.trockenheit.admin.ch", "data_date": measured},
            {"name": "MeteoSchweiz Open Data",      "url": "https://www.meteoswiss.admin.ch"},
            {"name": "BAFU Hydrodaten",             "url": "https://www.hydrodaten.admin.ch"},
        ],
        "disclaimer_de": "Automatisch aus offiziellen Bundesdaten generiert. Massgeblich ist die offizielle Warnung auf www.trockenheit.admin.ch.",
    }

# ── Gemeinde-Profile: laden und regelbasierte Empfehlungen ────────────────────

def load_profiles_index() -> dict[int, list[dict]]:
    """Lädt volle Profile {region_id: [profile, ...]} aus Einzel-JSON-Dateien.
    Index.json dient nur als Mapping bfs_nr -> region_id."""
    profiles_dir = ROOT / "data" / "profiles"
    index_path = profiles_dir / "index.json"
    if not index_path.exists():
        return {}
    try:
        entries = json.loads(index_path.read_text())
        result: dict[int, list[dict]] = {}
        for e in entries:
            rid = e.get("region_id")
            bfs_nr = e.get("bfs_nr")
            if rid is None or bfs_nr is None:
                continue
            p_path = profiles_dir / f"{bfs_nr}.json"
            if not p_path.exists():
                continue
            try:
                result.setdefault(int(rid), []).append(json.loads(p_path.read_text()))
            except Exception:
                pass
        log.debug("Profiles geladen: %d Gemeinden in %d Regionen", len(entries), len(result))
        return result
    except Exception as e:
        log.debug("profiles/index.json nicht geladen: %s", e)
        return {}


def _agg_profiles(profiles: list[dict]) -> dict:
    """Aggregiert Gemeinde-Profile einer Region (Mittelwert für Zahlen, any für bool, max für wildfire)."""
    if not profiles:
        return {}
    numeric = ["altitude_m", "agri_pct_est", "forest_pct_est",
               "alpine_pct_est", "built_pct_est", "water_pct_est"]
    bools   = ["in_groundwater_zone", "is_alpine", "is_agricultural",
               "has_forest", "is_urban", "is_sommerungsbetrieb"]
    agg: dict[str, Any] = {}
    for f in numeric:
        vals = [p[f] for p in profiles if p.get(f) is not None]
        agg[f] = round(sum(vals) / len(vals)) if vals else 0
    for f in bools:
        agg[f] = any(p.get(f) for p in profiles)
    wf = [p["wildfire_level_region"] for p in profiles if p.get("wildfire_level_region") is not None]
    agg["wildfire_level_region"] = max(wf) if wf else 0
    alts = [p["altitude_m"] for p in profiles if p.get("altitude_m") is not None]
    agg["altitude_m_max"] = max(alts) if alts else 0
    from collections import Counter
    zones = [p.get("agri_zone", "unbekannt") for p in profiles]
    agg["agri_zone"] = Counter(zones).most_common(1)[0][0]
    return agg


def eval_municipality_rules(profiles: list[dict], cdi: int, T: dict) -> dict[str, list[str]]:
    """Wertet municipality_rules aus thresholds.json aus. Gibt {'recommendation': [...], 'impact': [...]} zurück."""
    rules = T.get("municipality_rules", [])
    out: dict[str, list[str]] = {"recommendation": [], "impact": []}
    if not rules or not profiles:
        return out
    agg = _agg_profiles(profiles)
    fmt_vals = {k: v for k, v in agg.items() if v is not None}
    seen: set[str] = set()
    for rule in rules:
        rule_id = rule.get("id", "")
        if rule_id in seen:
            continue
        if cdi < rule.get("cdi_min", 1):
            continue
        match = True
        for cond in rule.get("conditions", []):
            field, op, val = cond.get("field",""), cond.get("op","=="), cond.get("value")
            fval = agg.get(field)
            if fval is None:
                match = False; break
            try:
                if op in (">",">=","<","<="):
                    fv, vv = float(fval), float(val)
                    if   op == ">":  match = fv > vv
                    elif op == ">=": match = fv >= vv
                    elif op == "<":  match = fv < vv
                    elif op == "<=": match = fv <= vv
                elif op == "==": match = fval == val
                elif op == "!=": match = fval != val
                else: match = False
            except (TypeError, ValueError):
                match = False
            if not match:
                break
        if not match:
            continue
        text = rule.get("text", "")
        try:
            text = text.format(**fmt_vals)
        except (KeyError, ValueError):
            pass
        rtype = rule.get("type", "recommendation")
        out.setdefault(rtype, []).append(text)
        seen.add(rule_id)
    return out


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="DryBrief Suisse — Briefing-Generierung")
    parser.add_argument("--date", metavar="YYYY-MM-DD",
                        help="Datum für historisches Briefing (YYYY-MM-DD). "
                             "Ohne Angabe wird der neueste vollständige Datenstand verwendet.")
    args = parser.parse_args()
    target_date: str | None = args.date

    t0 = time.time()
    log.info("="*60); log.info("DryBrief Suisse — Briefing-Generierung v2"); log.info("="*60)
    if target_date:
        log.info("Historisches Briefing für Datum: %s", target_date)

    T = load_thresholds(); meta = load_meta(); eng = Engine(T)
    profiles_index = load_profiles_index()
    if profiles_index:
        log.info("Gemeinde-Profile geladen: %d Regionen", len(profiles_index))
    cur_df = pd.read_csv(RAW_DIR/"weekly_current_regions.csv", sep=";")
    cur_df.columns = [c.replace(".", "_").strip() for c in cur_df.columns]
    log.info("Aktuelle Daten: %d Zeilen, Spalten: %s", len(cur_df), list(cur_df.columns))

    fc_df = None
    fc_path = RAW_DIR/"weekly_forecast_regions.csv"
    if fc_path.exists():
        fc_df = pd.read_csv(fc_path, sep=";")
        fc_df.columns = [c.replace(".", "_").strip() for c in fc_df.columns]
        log.info("Forecast-Spalten: %s", list(fc_df.columns))

    # ── Datum parsen (DD.MM.YYYY und ISO YYYY-MM-DD werden unterstützt) ──────
    cur_df["measured_at"] = parse_date_flex(cur_df["measured_at"]).dt.strftime("%Y-%m-%d")
    cur_df = cur_df[cur_df["measured_at"].notna()]

    available_dates = sorted(cur_df["measured_at"].dropna().unique(), reverse=True)
    log.info("Verfügbare Datumswerte (neueste 5): %s", available_dates[:5])

    if target_date:
        if target_date not in available_dates:
            log.error("Datum '%s' nicht in den Daten vorhanden. Verfügbare Daten: %s",
                      target_date, available_dates[:10])
            return
        lat = target_date
        write_latest = False
    else:
        full_dates = sorted(
            [d for d, g in cur_df.groupby("measured_at") if g.notna().values.all()],
            reverse=True,
        )
        lat = full_dates[0] if full_dates else available_dates[0]
        write_latest = True

    ady = age_days(lat)
    log.info("Datenstand: %s (%s Tage alt)", lat, ady)
    if write_latest and ady and ady > 7:
        log.warning("Daten sind %d Tage alt. Bitte 02_fetch_data.py erneut ausführen.", ady)

    cur_df = cur_df[cur_df["measured_at"] == lat]
    log.info("Gefiltert auf %s: %d Zeilen", lat, len(cur_df))

    if fc_df is not None:
        fc_df["valid_at"] = parse_date_flex(fc_df["valid_at"]).dt.strftime("%Y-%m-%d")
        fc_df = fc_df[fc_df["valid_at"].notna()]
        # Nächste Prognosewoche: kleinste vollständige valid_at >= Datenstand
        full_fc = sorted(
            [d for d, g in fc_df.groupby("valid_at") if g.notna().values.all()],
        )  # aufsteigend
        future_fc = [d for d in full_fc if d >= str(lat)]
        fc_lat = future_fc[0] if future_fc else (full_fc[0] if full_fc else fc_df["valid_at"].min())
        fc_df = fc_df[fc_df["valid_at"] == fc_lat]
        log.info("Nächste Prognosewoche: %s (%d Zeilen)", fc_lat, len(fc_df))

    all_ids = sorted(cur_df["drought_region_id"].dropna().astype(int).unique())
    log.info("Region-IDs im CSV: %s", all_ids)

    # ── Ausgabe-Verzeichnisse ─────────────────────────────────────────────────
    # Datumsspezifischer Ordner (immer): data/briefings/{YYYY-MM-DD}/regions/
    date_dir = ROOT / "data" / "briefings" / str(lat)
    date_region_dir = date_dir / "regions"
    date_region_dir.mkdir(parents=True, exist_ok=True)

    # "latest"-Ordner (nur bei normalem Lauf ohne --date): data/briefings/regions/
    if write_latest:
        BRIEFING_DIR.mkdir(parents=True, exist_ok=True)

    entries = []
    for rid in all_ids:
        c = get_latest(cur_df, rid, "measured_at")
        if not c: continue
        f = get_latest(fc_df, rid, "valid_at") if fc_df is not None else None
        m = meta.get(rid, {"id": rid, "name_de": f"Region {rid}", "name_fr": f"Région {rid}", "name_it": f"Regione {rid}"})
        b = build(rid, c, f, m, eng, profiles_index.get(rid))
        payload_str = json.dumps(b, ensure_ascii=False, indent=2, cls=_J)
        (date_region_dir / f"{rid}.json").write_text(payload_str)
        if write_latest:
            (BRIEFING_DIR / f"{rid}.json").write_text(payload_str)
        entries.append({
            "region_id":    rid,
            "name_de":      b["region_name_de"],
            "region_slug":  b["region_slug"],
            "cdi":          b["cdi"],
            "cdi_label_de": b["cdi_label_de"],
            "cdi_emoji":    b["cdi_emoji"],
            "warning_level":b["warning_level"],
            "color":        b["color"],
            "color_hex":    b["color_hex"],
            "trend":        b["trend"],
            "measured_at":  b["measured_at"],
            "data_age_days":b["data_age_days"],
        })
        log.info("  Region %3d (%s): CDI=%d [%s]  Alter=%sd", rid, b["region_name_de"], b["cdi"], b["color"], b["data_age_days"])

    now = datetime.now(timezone.utc).isoformat()
    index_payload = {"generated_at": now, "data_date": str(lat), "data_age_days": ady,
                     "n_regions": len(entries), "regions": sorted(entries, key=lambda x: x["region_id"])}
    index_str = json.dumps(index_payload, ensure_ascii=False, indent=2, cls=_J)
    (date_dir / "index.json").write_text(index_str)
    if write_latest:
        (ROOT/"data"/"briefings"/"index.json").write_text(index_str)
        (ROOT/"data"/"briefings"/"generated_at.json").write_text(
            json.dumps({"generated_at": now, "data_date": str(lat), "data_age_days": ady}, cls=_J))

    log.info("CDI-Verteilung: %s", {v: sum(1 for e in entries if e["cdi"]==v) for v in range(1, 6)})
    log.info("Briefings gespeichert unter: data/briefings/%s/", lat)
    if write_latest:
        log.info("Auch unter: data/briefings/ (aktuellster Stand)")
    log.info("Permalink-Beispiel: ?date=%s", lat)
    log.info("Laufzeit: %.1f s", time.time()-t0)

if __name__ == "__main__": main()
