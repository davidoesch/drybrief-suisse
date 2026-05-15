#!/usr/bin/env python3
"""03_generate_briefings.py v2 — Regelbasierte Textgenerierung"""
from __future__ import annotations
import json, logging, re, time
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

# ── Klartextbeschreibungen ────────────────────────────────────────────────────
def spi_to_comparison(spi: float | None) -> str:
    if spi is None: return "Keine Vergleichsdaten verfügbar"
    if spi > 1.5:  return "Deutlich mehr Niederschlag als üblich"
    if spi > 0.5:  return "Etwas mehr Niederschlag als üblich"
    if spi >= -0.5:return "Etwa gleich viel Niederschlag wie üblich"
    if spi >= -1.0:return "Etwas weniger Niederschlag als üblich"
    if spi >= -1.5:return "Deutlich weniger Niederschlag als üblich"
    if spi >= -2.0:return "Sehr viel weniger Niederschlag als üblich"
    return "Extrem wenig Niederschlag — historisch seltenes Ereignis"

def soil_to_plain(ufc: float | None) -> str:
    if ufc is None:  return "Keine Messdaten verfügbar"
    if ufc >= 80:    return "Böden gut mit Wasser versorgt"
    if ufc >= 60:    return "Böden mässig feucht"
    if ufc >= 40:    return "Böden leicht trocken"
    if ufc >= 20:    return "Böden trocken — Pflanzen unter Stress"
    return "Böden sehr trocken — erheblicher Wassermangel"

def hydro_to_plain(idx: int) -> str:
    return {1:"Pegel und Abflüsse im normalen Bereich",
            2:"Pegel leicht unter dem Durchschnitt",
            3:"Pegel deutlich unter dem Durchschnitt",
            4:"Niedrigwassersituation — kritisch tiefe Pegel",
            5:"Extreme Niedrigwassersituation — historisch selten"}.get(idx,"Keine Daten")

def hydro_flow_range(idx: int) -> str:
    """Indicative percentage of long-term mean flow for each hydro index level."""
    return {1:"70–130 % des langjährigen Mittelabflusses",
            2:"40–70 % des langjährigen Mittelabflusses",
            3:"20–40 % des langjährigen Mittelabflusses",
            4:"10–20 % des langjährigen Mittelabflusses",
            5:"< 10 % des langjährigen Mittelabflusses"}.get(idx,"keine Angabe")

def fc_summary(cdi: int, fc_cdi: int | None) -> str:
    if fc_cdi is None: return "Für die nächsten Wochen liegt aktuell keine Prognose vor."
    d = fc_cdi - cdi
    names = {1:"keine",2:"leichte",3:"erhebliche",4:"grosse",5:"extreme"}
    if d >= 2: return f"Die Lage wird sich deutlich verschlechtern — {names.get(fc_cdi,'Stufe '+str(fc_cdi))} Trockenheit erwartet (Stufe {fc_cdi})."
    if d == 1: return f"Eine leichte Verschlechterung wird erwartet (Stufe {cdi} → {fc_cdi})."
    if d == 0: return f"Die Lage bleibt voraussichtlich stabil (Stufe {cdi})."
    if d == -1:return f"Eine leichte Entspannung wird erwartet (Stufe {cdi} → {fc_cdi})."
    return f"Eine deutliche Entspannung wird erwartet (Stufe {cdi} → {fc_cdi})."

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
    def cdi_label(self, c): return self.t["cdi"].get(str(c),{}).get("label_de",f"CDI {c}")
    def cdi_noun(self, c):  return self.t["cdi"].get(str(c),{}).get("noun_de",f"Trockenheit Stufe {c}")
    def cdi_color(self, c): return self.t["cdi"].get(str(c),{}).get("color","grey")
    def cdi_hex(self, c):   return self.t["cdi"].get(str(c),{}).get("color_hex","#6b6b6b")
    def warn_level(self, c):return self.t["cdi"].get(str(c),{}).get("warning_level",c)
    def idx_label(self, t, v): return self.t.get(t,{}).get(str(v),f"Stufe {v}")
    def trend_key(self, cur, fc):
        if fc is None: return "unbekannt"
        d = fc - cur
        if d>=2: return "stark_verschlechternd"
        if d==1: return "verschlechternd"
        if d==0: return "stabil"
        if d==-1:return "verbessernd"
        return "stark_verbessernd"
    def trend_de(self, t): return {"stark_verschlechternd":"Starke Verschlechterung erwartet","verschlechternd":"Verschlechterung erwartet","stabil":"Stabile Lage erwartet","verbessernd":"Entspannung erwartet","stark_verbessernd":"Deutliche Entspannung erwartet","unbekannt":"Keine Prognose verfügbar"}.get(t,t)
    def summary(self, name, cdi):
        return {1:f"In der Region {name} sind aktuell keine aussergewöhnlichen Trockenheitssignale feststellbar.",2:f"In der Region {name} zeichnet sich eine leichte Trockenheit ab.",3:f"Die Region {name} ist von erheblicher Trockenheit betroffen.",4:f"In der Region {name} herrscht eine grosse Trockenheit.",5:f"Die Region {name} ist von extremer Trockenheit betroffen."}.get(cdi,f"Trockenheitsstufe {cdi}.")
    def impacts(self, c): return self.t.get("impacts",{}).get(str(c),[])
    def recs(self, c): return self.t.get("recommendations",{}).get("all",[])+self.t.get("recommendations",{}).get(str(c),[])

# ── Briefing-Zusammenbau ──────────────────────────────────────────────────────
def get_latest(df, rid, date_col):
    sub = df[df["drought_region_id"]==rid]
    return sub.sort_values(date_col,ascending=False).iloc[0].to_dict() if not sub.empty else None

def build(rid, curr, fc, meta, eng):
    cdi = _si(curr.get("cdi"))
    fc_cdi = None
    fc_valid = None
    # HINWEIS: cdi_p10/p90 existieren NICHT im forecast CSV — nur cdi_p50
    if fc:
        p50 = fc.get("cdi_p50")
        if p50 is not None and not _isnan(p50):
            fc_cdi = max(1, min(5, int(float(p50))))
        fc_valid = str(fc.get("valid_at","")) or None

    trend = eng.trend_key(cdi, fc_cdi)
    name  = meta.get("name_de", f"Region {rid}")
    slug  = make_slug(rid, name)
    measured = str(curr.get("measured_at","unbekannt"))
    adys = age_days(measured)

    spi_1m = _sf(curr.get("spi_1m"))
    spi_3m = _sf(curr.get("spi_3m"))
    p1 = _si(curr.get("precip_1m_index"))
    p3 = _si(curr.get("precip_3m_index"))

    indicators = {
        "precip": {
            "key":"precip","label_de":"Niederschlag","icon":"🌧",
            "link": r_url(slug,"precipitation"),
            "short_term": {
                "days":30,"index":p1,"value_mm":_sf(curr.get("precip_sum_1m")),
                "spi":spi_1m,"comparison_de":spi_to_comparison(spi_1m),
                "label_status_de":eng.idx_label("precip_index",p1),
            },
            "long_term": {
                "days":90,"index":p3,"value_mm":_sf(curr.get("precip_sum_3m")),
                "spi":spi_3m,"comparison_de":spi_to_comparison(spi_3m),
                "label_status_de":eng.idx_label("precip_index",p3),
            },
        },
        "hydro": {
            "key":"hydro","label_de":"Gewässer und Pegel","icon":"💧",
            "index":_si(curr.get("hydro_index")),
            "label_status_de":eng.idx_label("hydro_index",_si(curr.get("hydro_index"))),
            "plain_de":hydro_to_plain(_si(curr.get("hydro_index"))),
            "flow_range_de":hydro_flow_range(_si(curr.get("hydro_index"))),
            # Try several candidate column names for actual runoff ratio
            "value_q_rel":_sf(
                curr.get("q_rel_mean") or curr.get("q_fraction") or
                curr.get("runoff_fraction") or curr.get("hydro_value")
            ),
            "link":r_url(slug,"discharge"),"source":"BAFU Hydrodaten",
        },
        "soil_moisture": {
            "key":"soil_moisture","label_de":"Bodenfeuchte","icon":"🌱",
            "index":_si(curr.get("soil_moisture_index")),
            "value_pct":_sf(curr.get("soil_moisture_ufc")),
            "label_status_de":eng.idx_label("soil_moisture_index",_si(curr.get("soil_moisture_index"))),
            "plain_de":soil_to_plain(_sf(curr.get("soil_moisture_ufc"))),
            "link":r_url(slug,"moisture"),"source":"MeteoSchweiz",
        },
    }

    return {
        "region_id":rid,"region_name_de":name,
        "region_name_fr":meta.get("name_fr"),"region_name_it":meta.get("name_it"),"region_name_en":meta.get("name_en"),
        "region_slug":slug,"region_url":r_url(slug),"region_url_cdi":r_url(slug,"index"),
        "measured_at":measured,"generated_at":datetime.now(timezone.utc).isoformat(),"data_age_days":adys,
        "cdi":cdi,"cdi_label_de":eng.cdi_label(cdi),"cdi_noun_de":eng.cdi_noun(cdi),
        "warning_level":eng.warn_level(cdi),"color":eng.cdi_color(cdi),"color_hex":eng.cdi_hex(cdi),
        "summary_de":eng.summary(name,cdi),
        "trend":trend,"trend_label_de":eng.trend_de(trend),
        "forecast_cdi_p50":fc_cdi,"forecast_valid_at":fc_valid,
        "forecast_summary_de":fc_summary(cdi,fc_cdi),
        "indicators":indicators,
        "impacts_de":eng.impacts(cdi),"recommendations_de":eng.recs(cdi),
        "raw":{"precip_1m_index":p1,"precip_3m_index":p3,"precip_24m_index":_si(curr.get("precip_24m_index")),"hydro_index":_si(curr.get("hydro_index")),"soil_moisture_index":_si(curr.get("soil_moisture_index")),"spi_1m":spi_1m,"spi_3m":spi_3m,"soil_moisture_ufc":_sf(curr.get("soil_moisture_ufc")),"vhi":_sf(curr.get("vhi"))},
        "sources":[{"name":"Trockenheitsplattform BAFU","url":"https://www.trockenheit.admin.ch","data_date":measured},{"name":"MeteoSchweiz Open Data","url":"https://www.meteoswiss.admin.ch"},{"name":"BAFU Hydrodaten","url":"https://www.hydrodaten.admin.ch"}],
        "disclaimer_de":"Automatisch aus offiziellen Bundesdaten generiert. Massgeblich ist die offizielle Warnung auf www.trockenheit.admin.ch.",
    }

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    t0=time.time()
    log.info("="*60); log.info("DryBrief Suisse — Briefing-Generierung v2"); log.info("="*60)
    T=load_thresholds(); meta=load_meta(); eng=Engine(T)
    cur_df=pd.read_csv(RAW_DIR/"weekly_current_regions.csv",sep=";")
    cur_df.columns=[c.replace(".","_").strip() for c in cur_df.columns]
    log.info("Aktuelle Daten: %d Zeilen, Spalten: %s", len(cur_df), list(cur_df.columns))

    fc_df=None
    fc_path=RAW_DIR/"weekly_forecast_regions.csv"
    if fc_path.exists():
        fc_df=pd.read_csv(fc_path,sep=";")
        fc_df.columns=[c.replace(".","_").strip() for c in fc_df.columns]
        log.info("Forecast-Spalten: %s", list(fc_df.columns))
        log.info("(Hinweis: cdi_p10/p90 existieren NICHT im CSV — nur cdi_p50)")

    cur_df["measured_at"] = pd.to_datetime(cur_df["measured_at"], errors="coerce").dt.strftime("%Y-%m-%d")
    cur_df = cur_df[cur_df["measured_at"].notna()]
    log.info("Spalten current: %s", list(cur_df.columns))

    full_dates = sorted(
        [d for d, g in cur_df.groupby("measured_at") if g.notna().values.all()],
        reverse=True,
    )
    lat = full_dates[0] if full_dates else cur_df["measured_at"].max()
    ady = age_days(lat)
    log.info("Neuester vollständiger Datenstand: %s (%s Tage alt, %d vollst. Daten)",
             lat, ady, len(full_dates))
    if ady and ady > 7:
        log.warning("⚠ DATEN SIND %d TAGE ALT! Bitte 02_fetch_data.py erneut ausführen.", ady)

    cur_df = cur_df[cur_df["measured_at"] == lat]
    log.info("Gefiltert auf %s: %d Zeilen", lat, len(cur_df))

    if fc_df is not None:
        fc_df["valid_at"] = pd.to_datetime(fc_df["valid_at"], errors="coerce").dt.strftime("%Y-%m-%d")
        fc_df = fc_df[fc_df["valid_at"].notna()]
        full_fc = sorted(
            [d for d, g in fc_df.groupby("valid_at") if g.notna().values.all()],
            reverse=True,
        )
        fc_lat = full_fc[0] if full_fc else fc_df["valid_at"].max()
        fc_df = fc_df[fc_df["valid_at"] == fc_lat]
        log.info("Forecast-Datum: %s (%d Zeilen)", fc_lat, len(fc_df))

    all_ids=sorted(cur_df["drought_region_id"].dropna().astype(int).unique())
    log.info("Region-IDs im CSV: %s", all_ids)

    entries=[]
    for rid in all_ids:
        c=get_latest(cur_df,rid,"measured_at")
        if not c: continue
        f=get_latest(fc_df,rid,"valid_at") if fc_df is not None else None
        m=meta.get(rid,{"id":rid,"name_de":f"Region {rid}","name_fr":f"Région {rid}","name_it":f"Regione {rid}"})
        b=build(rid,c,f,m,eng)
        (BRIEFING_DIR/f"{rid}.json").write_text(json.dumps(b,ensure_ascii=False,indent=2,cls=_J))
        entries.append({"region_id":rid,"name_de":b["region_name_de"],"region_slug":b["region_slug"],"cdi":b["cdi"],"cdi_label_de":b["cdi_label_de"],"warning_level":b["warning_level"],"color":b["color"],"color_hex":b["color_hex"],"trend":b["trend"],"measured_at":b["measured_at"],"data_age_days":b["data_age_days"]})
        log.info("  Region %3d (%s): CDI=%d [%s]  Alter=%sd", rid, b["region_name_de"], b["cdi"], b["color"], b["data_age_days"])

    now=datetime.now(timezone.utc).isoformat()
    payload={"generated_at":now,"data_date":str(lat),"data_age_days":ady,"n_regions":len(entries),"regions":sorted(entries,key=lambda x:x["region_id"])}
    (ROOT/"data"/"briefings"/"index.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2,cls=_J))
    (ROOT/"data"/"briefings"/"generated_at.json").write_text(json.dumps({"generated_at":now,"data_date":str(lat),"data_age_days":ady},cls=_J))

    log.info("CDI-Verteilung: %s", {v:sum(1 for e in entries if e["cdi"]==v) for v in range(1,6)})
    log.info("Laufzeit: %.1f s", time.time()-t0)

if __name__=="__main__": main()
