/**
 * DryBrief Suisse — app.js
 * Lädt vorberechnete Briefing-JSONs und rendert das Trockenheitsbriefing.
 * Kein Build-Schritt, kein Framework — reines Vanilla JS.
 */

'use strict';

// ── Konfiguration ─────────────────────────────────────────────────────────────
// Pfad zu den Datendateien relativ zu index.html
// GitHub Pages (_site/): ./data   |   Lokal via /frontend/: ../data
const DATA_BASE = window.location.pathname.includes('/frontend') ? '../data' : './data';

// ── Protokoll-Check: file:// funktioniert nicht (CORS) ───────────────────────
if (window.location.protocol === 'file:') {
  document.body.innerHTML = `
    <div style="
      font-family: 'DM Sans', system-ui, sans-serif;
      max-width: 560px; margin: 80px auto; padding: 32px;
      background: #fff8f0; border: 2px solid #f97316;
      border-radius: 12px; color: #1a1a1a; line-height: 1.7;
    ">
      <h2 style="color:#ea580c;margin-bottom:12px;font-size:18px">
        ⚠ Lokaler Server erforderlich
      </h2>
      <p style="margin-bottom:16px">
        DryBrief Suisse kann nicht direkt über <code>file://</code> geöffnet werden,
        da der Browser aus Sicherheitsgründen keine lokalen JSON-Dateien lädt (CORS).
      </p>
      <p style="font-weight:600;margin-bottom:8px">So starten (Terminal):</p>
      <pre style="
        background:#1a1a1a; color:#f5f5f3; padding:16px 20px;
        border-radius:8px; font-size:13px; overflow-x:auto;
        margin-bottom:16px;
      ">cd /media/menas/data/projects/drybrief-suisse
python -m http.server 8080</pre>
      <p>
        Dann im Browser öffnen:<br>
        <a href="http://localhost:8080/frontend/" style="color:#ea580c;font-weight:600">
          http://localhost:8080/frontend/
        </a>
      </p>
      <p style="margin-top:16px;font-size:13px;color:#6b6b6b">
        Alternativ: <code>start.sh</code> im Projektordner ausführen.
      </p>
    </div>`;
  throw new Error('file:// Protokoll nicht unterstützt — lokalen Server starten.');
}

const CDI_CONFIG = {
  1: { hex: '#16a34a', bg: '#f0fdf4', border: '#bbf7d0', label: 'Nicht trocken'  },
  2: { hex: '#ca8a04', bg: '#fefce8', border: '#fde68a', label: 'Leicht trocken' },
  3: { hex: '#ea580c', bg: '#fff7ed', border: '#fed7aa', label: 'Trocken'        },
  4: { hex: '#dc2626', bg: '#fef2f2', border: '#fca5a5', label: 'Sehr trocken'   },
  5: { hex: '#7f1d1d', bg: '#fef2f2', border: '#f87171', label: 'Extrem trocken' },
};

const TREND_ARROWS = {
  stark_verschlechternd: '↑↑',
  verschlechternd:       '↑',
  stabil:                '→',
  verbessernd:           '↓',
  stark_verbessernd:     '↓↓',
  unbekannt:             '?',
};

const INDICATOR_META = {
  precip_1m:     { icon: '🌧', label: '30-Tage-Niederschlag' },
  precip_3m:     { icon: '🌧', label: '90-Tage-Niederschlag' },
  hydro:         { icon: '💧', label: 'Abfluss / Pegel'      },
  soil_moisture: { icon: '🌱', label: 'Bodenfeuchte'         },
};

// ── State ─────────────────────────────────────────────────────────────────────
let searchIndex     = {};
let currentEntry    = null;   // gewählter Suchtreffer
let leafletMap          = null;
let regionLayer         = null;   // Highlight aktuelle Region
let allRegionsLayer     = null;   // Klickbare Regionen-Überlagerung
let warnregionenGeoJSON = null;
let briefingIndex       = null;   // index.json als {region_id: entry}
let suggestionIdx   = -1;

// ── DOM ───────────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const searchInput   = $('searchInput');
const clearBtn      = $('clearBtn');
const suggList      = $('suggestions');
const generateBtn   = $('generateBtn');
const loadingState  = $('loadingState');
const errorState    = $('errorState');
const errorMsg      = $('errorMsg');
const briefingEl    = $('briefingOutput');

// ── Bootstrap ─────────────────────────────────────────────────────────────────
async function init() {
  try {
    const r = await fetch(`${DATA_BASE}/lookups/search_index.json`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    searchIndex = await r.json();
    searchInput.disabled      = false;
    searchInput.placeholder   = 'z.B. Bern, Graubünden, ZH, Davos …';
    bindEvents();
  } catch (e) {
    searchInput.placeholder = 'Suchindex nicht geladen — Pipeline ausführen';
    searchInput.disabled    = true;
    console.error('Init-Fehler:', e);
  }
}

// ── Events ────────────────────────────────────────────────────────────────────
function bindEvents() {
  searchInput.addEventListener('input',   onInput);
  searchInput.addEventListener('keydown', onKeyDown);
  clearBtn.addEventListener('click',      onClear);
  generateBtn.addEventListener('click',   onGenerate);
  document.addEventListener('click', e => {
    if (!e.target.closest('.search-wrap')) hideSuggestions();
  });
}

// ── Suche ─────────────────────────────────────────────────────────────────────
function onInput() {
  const q = searchInput.value.trim().toLowerCase();
  clearBtn.hidden = q === '';

  if (q.length === 0) {
    hideSuggestions();
    setSelection(null);
    return;
  }

  // Treffer: prefix-first, dann contains
  const results = Object.entries(searchIndex)
    .filter(([k]) => k.startsWith(q) || k.includes(q))
    .sort(([a], [b]) => (a.startsWith(q) ? 0 : 1) - (b.startsWith(q) ? 0 : 1) || a.localeCompare(b, 'de'))
    .slice(0, 9);

  results.length ? showSuggestions(results) : hideSuggestions();
}

function onKeyDown(e) {
  const items = [...suggList.querySelectorAll('li')];
  if (!items.length) return;
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    suggestionIdx = Math.min(suggestionIdx + 1, items.length - 1);
    items.forEach((el, i) => el.classList.toggle('active', i === suggestionIdx));
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    suggestionIdx = Math.max(suggestionIdx - 1, -1);
    items.forEach((el, i) => el.classList.toggle('active', i === suggestionIdx));
  } else if (e.key === 'Enter') {
    const target = suggestionIdx >= 0 ? items[suggestionIdx] : (items.length === 1 ? items[0] : null);
    target?.click();
  } else if (e.key === 'Escape') {
    hideSuggestions();
  }
}

function onClear() {
  searchInput.value = '';
  clearBtn.hidden   = true;
  hideSuggestions();
  setSelection(null);
  briefingEl.hidden  = true;
  errorState.hidden  = true;
  searchInput.focus();
}

function showSuggestions(results) {
  suggestionIdx = -1;
  suggList.innerHTML = '';
  suggList.hidden    = false;

  results.forEach(([, entry]) => {
    const li   = document.createElement('li');
    li.role    = 'option';
    const icon = entry.type === 'kanton' ? '🏛' : '📍';
    const sub  = entry.type === 'kanton'
      ? `Kanton · ${(entry.region_ids || []).length} Warnregion(en)`
      : `Gemeinde${entry.kanton ? ` · Kanton ${entry.kanton}` : ''}`;

    li.innerHTML = `
      <span class="sug-icon" aria-hidden="true">${icon}</span>
      <span class="sug-body">
        <strong class="sug-name">${escHtml(entry.display_name)}</strong>
        <span  class="sug-meta">${escHtml(sub)}</span>
      </span>`;
    li.addEventListener('click', () => {
      setSelection(entry);
      hideSuggestions();
      searchInput.value = entry.display_name;
    });
    suggList.appendChild(li);
  });
}

function hideSuggestions() {
  suggList.hidden = true;
  suggestionIdx   = -1;
}

function setSelection(entry) {
  currentEntry          = entry;
  generateBtn.disabled  = !entry;
}

// ── Briefing laden ────────────────────────────────────────────────────────────
async function onGenerate() {
  if (!currentEntry) return;

  const regionId = currentEntry.primary_region_id ?? currentEntry.region_id;
  if (!regionId) { showErr('Keine Warnregion für diese Auswahl gefunden.'); return; }

  briefingEl.hidden  = true;
  errorState.hidden  = true;
  loadingState.hidden= false;
  generateBtn.disabled = true;

  try {
    const r = await fetch(`${DATA_BASE}/briefings/regions/${regionId}.json`);
    if (!r.ok) throw new Error(
      `Briefing für Region ${regionId} nicht gefunden (HTTP ${r.status}).\n` +
      `Bitte Pipeline ausführen: python pipeline/03_generate_briefings.py`
    );
    render(await r.json());
  } catch (e) {
    showErr(e.message);
  } finally {
    loadingState.hidden  = true;
    generateBtn.disabled = false;
  }
}

// ── Rendering ─────────────────────────────────────────────────────────────────
function render(b) {
  const cdi  = Math.max(1, Math.min(5, b.cdi ?? 1));
  const cfg  = CDI_CONFIG[cdi];

  // Lagebeurteilung
  $('regionName').textContent =
    currentEntry.type === 'kanton'
      ? `Kanton · ${b.region_name_de}`
      : `${currentEntry.display_name} · ${b.region_name_de}`;
  $('dataDate').textContent     = `Datenstand ${fmt(b.measured_at)}`;
  $('cdiLabel').textContent     = b.cdi_label_de ?? '–';
  $('cdiLabel').style.color     = cfg.hex;
  $('summaryText').textContent  = b.summary_de ?? '';
  $('cdiBadgeNum').textContent  = cdi;
  $('cdiBadgeNum').style.color  = cfg.hex;

  // Status-Dot
  const dot = $('statusDot');
  dot.style.backgroundColor = cfg.hex;
  dot.title = b.cdi_label_de;

  // Statuscard Hintergrund
  const card = document.querySelector('.status-card');
  card.style.background   = cfg.bg;
  card.style.borderColor  = cfg.border;

  // Trend
  const trendKey = b.trend ?? 'unbekannt';
  $('trendText').textContent =
    `${TREND_ARROWS[trendKey] ?? '?'} ${b.trend_label_de ?? '–'}`;

  // Indikatoren
  renderIndicators(b.indicators ?? {});

  // Auswirkungen
  const impacts     = b.impacts_de ?? [];
  const impactsSec  = $('impactsSection');
  if (impacts.length) {
    $('impactsList').innerHTML = impacts.map(i => `<li>${escHtml(i)}</li>`).join('');
    impactsSec.hidden = false;
  } else {
    impactsSec.hidden = true;
  }

  // Empfehlungen
  const recs    = b.recommendations_de ?? [];
  $('recList').innerHTML = recs.length
    ? recs.map(r => `<li>${escHtml(r)}</li>`).join('')
    : '<li>Keine besonderen Massnahmen erforderlich.</li>';

  // Prognose
  renderForecast(b);

  // Quellen
  renderSources(b.sources ?? [], b.generated_at);
  $('disclaimerText').textContent = b.disclaimer_de ?? '';

  // Karte
  initOrUpdateMap(b.region_id);

  // Sektionsnummern sectionHeading-Farbe
  document.querySelectorAll('.section-num').forEach(el => {
    el.style.color = cfg.hex;
  });

  briefingEl.hidden = false;
  briefingEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderIndicators(indicators) {
  const grid = $('indicatorsGrid');
  grid.innerHTML = '';

  const order = ['precip_1m', 'precip_3m', 'hydro', 'soil_moisture'];
  order.forEach(key => {
    const ind  = indicators[key];
    if (!ind) return;
    const idx  = Math.max(1, Math.min(5, ind.index ?? 1));
    const cfg  = CDI_CONFIG[idx];
    const meta = INDICATOR_META[key] ?? { icon: '📊', label: key };

    // Wert
    let valHtml = '';
    if (ind.value_mm != null)
      valHtml = `<span class="ind-val">${ind.value_mm}&thinsp;<small>${escHtml(ind.unit ?? '')}</small></span>`;
    else if (ind.value_pct != null)
      valHtml = `<span class="ind-val">${ind.value_pct}&thinsp;<small>${escHtml(ind.unit ?? '')}</small></span>`;

    const card = document.createElement('div');
    card.className = 'ind-card';
    card.style.setProperty('--ind-color', cfg.hex);
    card.style.setProperty('--ind-bg',    cfg.bg);
    card.innerHTML = `
      <div class="ind-top">
        <span class="ind-icon" aria-hidden="true">${meta.icon}</span>
        <span class="ind-label">${escHtml(ind.label_de ?? meta.label)}</span>
        <span class="ind-pill" style="background:${cfg.hex}">${idx}</span>
      </div>
      <p class="ind-status">${escHtml(ind.label_status_de ?? '–')}</p>
      ${valHtml}
      <span class="ind-src">${escHtml(ind.source ?? '')}</span>`;
    grid.appendChild(card);
  });
}

function renderForecast(b) {
  const fc = $('forecastBlock');
  const p50 = b.forecast_cdi_p50;

  if (!p50) {
    fc.innerHTML = '<p class="muted">Keine Prognosedaten verfügbar.</p>';
    return;
  }

  const cfgP50 = CDI_CONFIG[p50] ?? CDI_CONFIG[1];
  const cfgP10 = CDI_CONFIG[b.forecast_cdi_p10] ?? CDI_CONFIG[1];
  const cfgP90 = CDI_CONFIG[b.forecast_cdi_p90] ?? CDI_CONFIG[1];

  fc.innerHTML = `
    <div class="fc-row">
      <div class="fc-cell fc-main">
        <span class="fc-cell-label">Wahrscheinlichste Entwicklung (P50)</span>
        <span class="fc-cell-val" style="color:${cfgP50.hex}">CDI ${p50}</span>
        <span class="fc-cell-sub">${escHtml(b.forecast_valid_at ? `Gültig ab ${fmt(b.forecast_valid_at)}` : '')}</span>
      </div>
      <div class="fc-cell">
        <span class="fc-cell-label">Günstiges Szenario (P10)</span>
        <span class="fc-cell-val" style="color:${cfgP10.hex}">CDI ${b.forecast_cdi_p10 ?? '–'}</span>
      </div>
      <div class="fc-cell">
        <span class="fc-cell-label">Ungünstiges Szenario (P90)</span>
        <span class="fc-cell-val" style="color:${cfgP90.hex}">CDI ${b.forecast_cdi_p90 ?? '–'}</span>
      </div>
    </div>
    <p class="fc-uncertainty">${escHtml(b.forecast_uncertainty ?? '')}</p>
    <p class="fc-note muted">P10/P50/P90: Ensemble-Perzentile. Quelle: BAFU / MeteoSchweiz.</p>`;
}

function renderSources(sources, generatedAt) {
  const list = $('sourcesList');
  list.innerHTML = sources.map(s => `
    <div class="src-row">
      <span class="src-name">${escHtml(s.name)}</span>
      ${s.data_date ? `<span class="src-date">Datenstand: ${fmt(s.data_date)}</span>` : ''}
      <a class="src-link" href="${escAttr(s.url)}" target="_blank" rel="noopener noreferrer">
        ${escHtml(s.url)}&nbsp;↗
      </a>
      ${s.api_url ? `<code class="src-api">${escHtml(s.api_url)}</code>` : ''}
    </div>`).join('');

  if (generatedAt) {
    list.insertAdjacentHTML('beforeend', `
      <div class="src-row src-generated">
        <span class="src-name">Briefing generiert</span>
        <span class="src-date">${fmtDt(generatedAt)}</span>
      </div>`);
  }
}

// ── Leaflet-Karte (EPSG:2056 / LV95) ─────────────────────────────────────────
// Swisstopo WMTS tile resolutions für das 2056-Tile-Matrix-Set
const RESOLUTIONS_2056 = [
  4000, 3750, 3500, 3250, 3000, 2750, 2500, 2250, 2000, 1750,
  1500, 1250, 1000,  750,  650,  500,  250,  100,   50,   20,
    10,    5,  2.5,    2,  1.5,    1,  0.5,
];

const CRS_2056 = new L.Proj.CRS(
  'EPSG:2056',
  '+proj=somerc +lat_0=46.9524055555556 +lon_0=7.43958333333333 +k_0=1 ' +
  '+x_0=2600000 +y_0=1200000 +ellps=bessel ' +
  '+towgs84=674.374,15.056,405.346,0,0,0,0 +units=m +no_defs',
  {
    resolutions: RESOLUTIONS_2056,
    origin:      [2420000, 1350000],
    bounds:      L.bounds([2420000, 1030000], [2900000, 1350000]),
  }
);

function initOrUpdateMap(regionId) {
  const container = $('map');

  if (!leafletMap) {
    leafletMap = L.map(container, {
      crs:              CRS_2056,
      center:           [46.82, 8.22],
      zoom:             3,
      minZoom:          0,
      maxZoom:          26,
      zoomControl:      true,
      attributionControl: true,
    });

    // Basiskarte: swisstopo Pixelkarte grau im LV95-Tile-Matrix-Set
    L.tileLayer(
      'https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-grau/default/current/2056/{z}/{x}/{y}.jpeg',
      {
        attribution: '© <a href="https://www.swisstopo.admin.ch" target="_blank">swisstopo</a>',
        maxZoom:     26,
        tileSize:    256,
      }
    ).addTo(leafletMap);

    // Trockenheitsindex WMS (halbtransparent über Basiskarte)
    L.tileLayer.wms('https://wms.geo.admin.ch/', {
      layers:      'ch.bafu.trockenheitsindex',
      format:      'image/png',
      transparent: true,
      opacity:     0.72,
      version:     '1.3.0',
      crossOrigin: true,
      attribution: '© <a href="https://www.bafu.admin.ch" target="_blank">BAFU</a>',
    }).addTo(leafletMap);

    // Warnregionsgrenzen
    L.tileLayer.wms('https://wms.geo.admin.ch/', {
      layers:      'ch.bafu.trockenheitswarnungen',
      format:      'image/png',
      transparent: true,
      opacity:     0.6,
      version:     '1.3.0',
      crossOrigin: true,
    }).addTo(leafletMap);
  }

  // Karte neu zeichnen, dann auf Region zoomen
  setTimeout(() => {
    leafletMap.invalidateSize();
    if (regionId) zoomToRegion(regionId);
  }, 120);

  // Klickbare Regionen-Schicht (einmalig)
  if (!allRegionsLayer) addRegionClickLayer();
}

async function addRegionClickLayer() {
  if (!warnregionenGeoJSON) {
    try {
      const r = await fetch(`${DATA_BASE}/lookups/warnregionen.geojson`);
      if (r.ok) warnregionenGeoJSON = await r.json();
    } catch (e) { return; }
  }

  try {
    const r = await fetch(`${DATA_BASE}/briefings/index.json`);
    if (r.ok) {
      const d = await r.json();
      briefingIndex = {};
      for (const reg of d.regions) briefingIndex[reg.region_id] = reg;
    }
  } catch (e) { /* Briefings noch nicht generiert */ }

  if (!warnregionenGeoJSON) return;

  allRegionsLayer = L.geoJSON(warnregionenGeoJSON, {
    style: { color: '#999', weight: 0.8, fillOpacity: 0 },
    onEachFeature(feature, layer) {
      layer.on('click', e => {
        const rid  = Number(feature.properties.region_id);
        const info = briefingIndex?.[rid];
        const name = feature.properties.Name ?? `Region ${rid}`;
        const cfg  = CDI_CONFIG[info?.cdi] ?? CDI_CONFIG[1];

        let html = `<strong>${escHtml(name)}</strong>`;
        if (info) {
          html += `
            <table class="map-popup">
              <tr><td>Trockenheitsindex</td>
                  <td><b style="color:${cfg.hex}">${escHtml(info.cdi_label_de)}</b></td></tr>
              <tr><td>Gültig ab</td><td>${fmt(info.measured_at)}</td></tr>
              <tr><td>Gültig bis</td><td>${fmtAddDays(info.measured_at, 6)}</td></tr>
            </table>`;
        } else {
          html += '<br><em>Noch keine Briefing-Daten.</em>';
        }

        L.popup({ maxWidth: 280 })
          .setLatLng(e.latlng)
          .setContent(html)
          .openOn(leafletMap);
      });
    },
  }).addTo(leafletMap);
}

async function zoomToRegion(regionId) {
  if (!warnregionenGeoJSON) {
    try {
      const r = await fetch(`${DATA_BASE}/lookups/warnregionen.geojson`);
      if (!r.ok) return;
      warnregionenGeoJSON = await r.json();
    } catch (e) {
      console.warn('warnregionen.geojson nicht geladen:', e);
      return;
    }
  }

  const feature = warnregionenGeoJSON.features.find(
    f => Number(f.properties.region_id) === Number(regionId)
  );
  if (!feature) return;

  if (regionLayer) leafletMap.removeLayer(regionLayer);
  regionLayer = L.geoJSON(feature, {
    style: { color: '#1a1a1a', weight: 2, fillOpacity: 0.08, fillColor: '#1a1a1a' },
  }).addTo(leafletMap);

  // getBounds() ist auf Custom-CRS-Karten unzuverlässig — Bounds aus Koordinaten berechnen
  const bounds = coordsBounds(feature.geometry.coordinates);
  if (bounds) leafletMap.fitBounds(bounds, { padding: [32, 32] });
}

function coordsBounds(coords) {
  const pts = [];
  const collect = c => (typeof c[0] === 'number' ? pts.push(c) : c.forEach(collect));
  collect(coords);
  if (!pts.length) return null;
  const lngs = pts.map(p => p[0]);
  const lats  = pts.map(p => p[1]);
  return L.latLngBounds(
    [Math.min(...lats), Math.min(...lngs)],
    [Math.max(...lats), Math.max(...lngs)]
  );
}

// ── Hilfsfunktionen ───────────────────────────────────────────────────────────
function fmt(ds) {
  if (!ds || ds === 'unbekannt') return 'unbekannt';
  try {
    return new Intl.DateTimeFormat('de-CH', {
      day: '2-digit', month: 'long', year: 'numeric',
    }).format(new Date(ds));
  } catch { return ds; }
}

function fmtAddDays(ds, days) {
  if (!ds || ds === 'unbekannt') return '–';
  try {
    const d = new Date(ds);
    d.setDate(d.getDate() + days);
    return new Intl.DateTimeFormat('de-CH', { day: '2-digit', month: 'long', year: 'numeric' }).format(d);
  } catch { return ds; }
}

function fmtDt(ds) {
  if (!ds) return '–';
  try {
    return new Intl.DateTimeFormat('de-CH', {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
      timeZone: 'Europe/Zurich',
    }).format(new Date(ds)) + ' MEZ';
  } catch { return ds; }
}

function escHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function escAttr(s) { return escHtml(s); }

function showErr(msg) {
  errorMsg.textContent  = msg;
  errorState.hidden     = false;
  loadingState.hidden   = true;
  briefingEl.hidden     = true;
}

// ── Start ─────────────────────────────────────────────────────────────────────
init();