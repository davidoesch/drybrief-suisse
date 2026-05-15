/**
 * DryBrief Suisse — app.js v2
 * Auto-generate, plain language, staleness banner, indicator links
 */
'use strict';

const DATA_BASE = window.location.pathname.includes('/frontend') ? '../data' : './data';

// ── Permalink: ?date=YYYY-MM-DD ───────────────────────────────────────────────
function parseDateParam() {
  const raw = new URLSearchParams(window.location.search).get('date') ?? '';
  if(/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  // Accept DD-MM-YYYY as fallback
  const m = raw.match(/^(\d{2})-(\d{2})-(\d{4})$/);
  if(m) return `${m[3]}-${m[2]}-${m[1]}`;
  return null;
}
const DATE_PARAM = parseDateParam();
// Historical: load from data/briefings/YYYY-MM-DD/; latest: data/briefings/
const BRIEFING_BASE = DATE_PARAM ? `${DATA_BASE}/briefings/${DATE_PARAM}` : `${DATA_BASE}/briefings`;

if (window.location.protocol === 'file:') {
  document.body.innerHTML = `<div style="font-family:system-ui;max-width:520px;margin:80px auto;padding:28px;background:#fff8f0;border:2px solid #f97316;border-radius:12px;line-height:1.7"><h2 style="color:#ea580c;margin-bottom:10px">⚠ Lokaler Server erforderlich</h2><p>DryBrief kann nicht via <code>file://</code> geöffnet werden (CORS).</p><p style="margin-top:12px"><b>Terminal:</b></p><pre style="background:#111;color:#f5f5f3;padding:14px;border-radius:8px;font-size:13px">cd /path/to/drybrief-suisse
python -m http.server 8080</pre><p style="margin-top:10px">Dann: <a href="http://localhost:8080/frontend/" style="color:#ea580c">http://localhost:8080/frontend/</a></p></div>`;
  throw new Error('file:// nicht unterstützt');
}

const CDI = {
  1:{hex:'#16a34a',bg:'#f0fdf4',border:'#bbf7d0',emoji:'💚',word:'nicht trocken'},
  2:{hex:'#ca8a04',bg:'#fefce8',border:'#fde68a',emoji:'⚠️', word:'leicht trocken'},
  3:{hex:'#ea580c',bg:'#fff7ed',border:'#fed7aa',emoji:'🌵',word:'trocken'},
  4:{hex:'#dc2626',bg:'#fef2f2',border:'#fca5a5',emoji:'🔥',word:'sehr trocken'},
  5:{hex:'#7f1d1d',bg:'#fef2f2',border:'#f87171',emoji:'🚨',word:'extrem trocken'},
};

const TREND_ARROWS={stark_verschlechternd:'↑↑',verschlechternd:'↑',stabil:'→',verbessernd:'↓',stark_verbessernd:'↓↓',unbekannt:'?'};

let searchIndex={}, currentEntry=null, suggIdx=-1;
let leafletMap=null, regionLayer=null, allRegionsLayer=null;
let warnregionenGeoJSON=null, briefingIndex=null;

const $=id=>document.getElementById(id);
const searchInput=$('searchInput'), clearBtn=$('clearBtn'), suggList=$('suggestions');
const generateBtn=$('generateBtn'), loadingState=$('loadingState');
const errorState=$('errorState'), errorMsg=$('errorMsg'), briefingEl=$('briefingOutput');
const stalenessBanner=$('stalenessBanner'), historyBanner=$('historyBanner');

// ── Init ──────────────────────────────────────────────────────────────────────
async function init(){
  if(DATE_PARAM){
    historyBanner.innerHTML=`Historisches Briefing: Daten vom <strong>${fmt(DATE_PARAM)}</strong> &middot; <a href="${window.location.pathname}">Aktuelles Briefing anzeigen</a>`;
    historyBanner.hidden=false;
  }
  try{
    const r=await fetch(`${DATA_BASE}/lookups/search_index.json`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    searchIndex=await r.json();
    searchInput.disabled=false;
    bindEvents();
  } catch(e){
    searchInput.placeholder='Suchindex nicht geladen, Pipeline ausführen';
    searchInput.disabled=true;
  }
}

// ── Events ────────────────────────────────────────────────────────────────────
function bindEvents(){
  searchInput.addEventListener('input', onInput);
  searchInput.addEventListener('keydown', onKeyDown);
  clearBtn.addEventListener('click', onClear);
  generateBtn.addEventListener('click', onGenerate);
  document.addEventListener('click', e=>{ if(!e.target.closest('.search-wrap')) hideSugg(); });
}

// ── Suche ─────────────────────────────────────────────────────────────────────
function onInput(){
  const q=searchInput.value.trim().toLowerCase();
  clearBtn.hidden=q==='';
  if(q.length===0){ hideSugg(); setEntry(null); return; }
  const res=Object.entries(searchIndex)
    .filter(([k])=>k.startsWith(q)||k.includes(q))
    .sort(([a],[b])=>(a.startsWith(q)?0:1)-(b.startsWith(q)?0:1)||a.localeCompare(b,'de'))
    .slice(0,9);
  res.length?showSugg(res):hideSugg();
}

function onKeyDown(e){
  const items=[...suggList.querySelectorAll('li')];
  if(!items.length) return;
  if(e.key==='ArrowDown'){e.preventDefault();suggIdx=Math.min(suggIdx+1,items.length-1);}
  else if(e.key==='ArrowUp'){e.preventDefault();suggIdx=Math.max(suggIdx-1,-1);}
  else if(e.key==='Enter'){const t=suggIdx>=0?items[suggIdx]:items.length===1?items[0]:null;t?.click();}
  else if(e.key==='Escape'){hideSugg();}
  items.forEach((el,i)=>el.classList.toggle('active',i===suggIdx));
}

function onClear(){searchInput.value='';clearBtn.hidden=true;hideSugg();setEntry(null);briefingEl.hidden=true;errorState.hidden=true;stalenessBanner.hidden=true;searchInput.focus();}

function showSugg(results){
  suggIdx=-1; suggList.innerHTML=''; suggList.hidden=false;
  results.forEach(([,e])=>{
    const li=document.createElement('li'); li.role='option';
    const icon=e.type==='kanton'?'🏛':'📍';
    const sub=e.type==='kanton'?`Kanton · ${(e.region_ids||[]).length} Warnregion(en)`:`Gemeinde${e.kanton?` · Kanton ${e.kanton}`:''}`;
    li.innerHTML=`<span class="sug-icon">${icon}</span><span class="sug-body"><strong class="sug-name">${escHtml(e.display_name)}</strong><span class="sug-meta">${escHtml(sub)}</span></span>`;
    li.addEventListener('click',()=>{ setEntry(e); hideSugg(); searchInput.value=e.display_name; onGenerate(); });
    suggList.appendChild(li);
  });
}

function hideSugg(){ suggList.hidden=true; suggIdx=-1; }

function setEntry(e){ currentEntry=e; generateBtn.disabled=!e; }

// ── Briefing laden ────────────────────────────────────────────────────────────
async function onGenerate(){
  if(!currentEntry) return;
  briefingEl.hidden=true; errorState.hidden=true; stalenessBanner.hidden=true;
  loadingState.hidden=false; generateBtn.disabled=true;

  try{
    if(currentEntry.type==='kanton'){
      await onGenerateKanton();
    } else {
      const rid=currentEntry.primary_region_id??currentEntry.region_id;
      if(!rid){ showErr('Keine Warnregion für diese Auswahl gefunden.'); return; }
      const r=await fetch(`${BRIEFING_BASE}/regions/${rid}.json`);
      if(!r.ok) throw new Error(DATE_PARAM
        ? `Kein Briefing für ${DATE_PARAM} vorhanden.\nZuerst generieren:\npython pipeline/03_generate_briefings.py --date ${DATE_PARAM}`
        : `Briefing nicht gefunden (HTTP ${r.status}).\nPipeline ausführen: python pipeline/03_generate_briefings.py`);
      const briefing=await r.json();
      render(briefing);
    }
  } catch(e){ showErr(e.message); }
  finally{ loadingState.hidden=true; generateBtn.disabled=false; }
}

async function onGenerateKanton(){
  const ids=currentEntry.region_ids??[];
  if(!ids.length){ showErr('Keine Warnregionen für diesen Kanton gefunden.'); return; }

  const results=await Promise.allSettled(
    ids.map(rid=>fetch(`${BRIEFING_BASE}/regions/${rid}.json`).then(r=>r.ok?r.json():null))
  );
  const briefings=results.map(r=>r.status==='fulfilled'?r.value:null).filter(Boolean);
  if(!briefings.length){
    showErr(DATE_PARAM
      ? `Kein Briefing für ${DATE_PARAM} vorhanden.\nZuerst generieren:\npython pipeline/03_generate_briefings.py --date ${DATE_PARAM}`
      : 'Keine Briefing-Daten für diesen Kanton. Pipeline ausführen: python pipeline/03_generate_briefings.py');
    return;
  }

  // Median CDI
  const cdis=[...briefings.map(b=>b.cdi??1)].sort((a,b)=>a-b);
  const medianCdi=cdis[Math.floor(cdis.length/2)];

  // Synthetic aggregate briefing for the top section
  const base=briefings.find(b=>b.cdi===medianCdi)??briefings[0];
  const synth={
    ...base,
    cdi:medianCdi,
    cdi_label_de:(CDI[medianCdi]?.word??base.cdi_label_de),
    region_name_de:currentEntry.display_name,
    summary_de:`Kanton ${currentEntry.display_name}: ${briefings.length} Warnregionen. `+
      `Medianer Trockenheitsindex CDI ${medianCdi}, ${CDI[medianCdi]?.word??''}.`,
    region_id:null,
  };
  render(synth);
  renderKantonRegions(briefings);
}

function renderKantonRegions(briefings){
  const section=$('kantonRegionsSection');
  const list=$('kantonRegionsList');
  section.hidden=false;
  list.innerHTML=[...briefings].sort((a,b)=>(b.cdi??1)-(a.cdi??1)).map(b=>{
    const cfg=CDI[b.cdi??1]??CDI[1];
    const ind=b.indicators??{};
    const p=ind.precip?.short_term, s=ind.soil_moisture, h=ind.hydro;
    const details=[
      p?.value_mm!=null?`🌧 ${p.value_mm} mm (30 T)`:'',
      s?.value_pct!=null?`🌱 ${s.value_pct}% nFK`:'',
      h?.value_q_rel!=null?`💧 ${h.value_q_rel}% Mittelabfluss`:(h?.flow_range_de?`💧 ${h.flow_range_de}`:''),
    ].filter(Boolean).join(' · ');
    return `<details class="region-item">
      <summary class="region-item-summary">
        <span class="region-dot" style="background:${cfg.hex}"></span>
        <span class="region-item-name">${escHtml(b.region_name_de)}</span>
        <span class="cdi-tag" style="background:${cfg.hex};color:#fff">CDI ${b.cdi}</span>
        <span class="region-trend">${TREND_ARROWS[b.trend??'unbekannt']??'?'}</span>
      </summary>
      <div class="region-item-body">
        <p>${escHtml(b.summary_de??'')}</p>
        ${b.forecast_summary_de?`<p class="region-fc">${escHtml(b.forecast_summary_de)}</p>`:''}
        ${details?`<p class="region-details">${escHtml(details)}</p>`:''}
        <a class="ind-detail-link" href="${escAttr(b.region_url_cdi??'https://www.trockenheit.admin.ch')}" target="_blank" rel="noopener">Details ↗</a>
      </div>
    </details>`;
  }).join('');
}

// ── Rendering ─────────────────────────────────────────────────────────────────
function render(b){
  const cdi=Math.max(1,Math.min(5,b.cdi??1)), cfg=CDI[cdi];
  $('kantonRegionsSection').hidden=true;  // hidden unless renderKantonRegions re-shows it

  // Staleness-Banner
  const adys=b.data_age_days??0;
  if(adys>30){
    stalenessBanner.textContent=`⚠ Daten sind ${adys} Tage alt! Bitte Pipeline neu ausführen: python pipeline/02_fetch_data.py && python pipeline/03_generate_briefings.py`;
    stalenessBanner.className='staleness-banner staleness-error'; stalenessBanner.hidden=false;
  } else if(adys>7){
    stalenessBanner.textContent=`ℹ Daten vom ${fmt(b.measured_at)} (${adys} Tage alt), wöchentliche Aktualisierung empfohlen.`;
    stalenessBanner.className='staleness-banner staleness-warn'; stalenessBanner.hidden=false;
  } else { stalenessBanner.hidden=true; }

  // Lagebeurteilung
  const display=currentEntry?.type==='kanton'?`Kanton · ${b.region_name_de}`:`${currentEntry?.display_name} · ${b.region_name_de}`;
  $('regionName').textContent=display;
  $('dataDate').textContent=`Datenstand ${fmt(b.measured_at)}`;
  $('cdiLabel').textContent=b.cdi_label_de??'–';
  $('cdiLabel').style.color=cfg.hex;
  $('summaryText').textContent=b.summary_de??'';
  $('cdiBadgeNum').textContent=cdi;
  $('cdiBadgeNum').style.color=cfg.hex;

  const dot=$('statusDot'); dot.style.backgroundColor=cfg.hex;
  const card=document.querySelector('.status-card');
  card.style.background=cfg.bg; card.style.borderColor=cfg.border;

  $('trendText').textContent=`${TREND_ARROWS[b.trend??'unbekannt']??'?'} ${b.trend_label_de??'–'}`;

  // Link auf offizielle Seite — nur bei Einzelregion sinnvoll
  const hasMultiple = currentEntry?.type === 'kanton' || (currentEntry?.region_ids?.length > 1);
  const linkEl = $('officialLink');
  if(hasMultiple){
    linkEl.hidden = true;
  } else {
    linkEl.hidden = false;
    linkEl.href = b.region_url_cdi ?? b.region_url ?? 'https://www.trockenheit.admin.ch';
    linkEl.textContent = `Offizielle Warnung ${b.region_name_de ?? ''} \u2197`;
  }

  renderIndicators(b.indicators??{});

  // Auswirkungen
  const impacts=b.impacts_de??[], sec=$('impactsSection');
  if(impacts.length){ $('impactsList').innerHTML=impacts.map(i=>`<li>${escHtml(i)}</li>`).join(''); sec.hidden=false; }
  else sec.hidden=true;

  // Empfehlungen
  const recs=b.recommendations_de??[];
  $('recList').innerHTML=recs.length?recs.map(r=>`<li>${escHtml(r)}</li>`).join(''):'<li>Keine besonderen Massnahmen erforderlich.</li>';

  renderForecast(b);
  renderSources(b.sources??[], b.generated_at);
  $('disclaimerText').textContent=b.disclaimer_de??'';

  document.querySelectorAll('.section-num').forEach(el=>el.style.color=cfg.hex);

  // Permalink: URL mit Datum aktualisieren (nur beim aktuellen Stand, nicht nochmals bei historisch)
  if(!DATE_PARAM && b.measured_at && b.measured_at!=='unbekannt'){
    const url=new URL(window.location.href);
    url.searchParams.set('date', b.measured_at);
    history.replaceState(null,'',url);
  }
  initOrUpdateMap(b.region_id);
  briefingEl.hidden=false;
  briefingEl.scrollIntoView({behavior:'smooth',block:'start'});
}

function renderIndicators(ind){
  const grid=$('indicatorsGrid'); grid.innerHTML='';

  // Niederschlag (kombiniert: 30 Tage + 90 Tage)
  if(ind.precip){
    const p=ind.precip;
    const st=p.short_term??{}, lt=p.long_term??{};
    const idx=Math.max(st.index??1, lt.index??1);
    const cfg=CDI[idx]??CDI[1];
    const card=makeCard(p.icon??'🌧', p.label_de??'Niederschlag', idx, cfg, p.link);
    card.querySelector('.ind-status').innerHTML=`
      <div class="precip-row">
        <span class="precip-term">Letzte 30 Tage</span>
        <span class="precip-val">${st.value_mm??'–'}&thinsp;<small>mm</small></span>
        <span class="precip-comp">${escHtml(st.comparison_de??'')}</span>
      </div>
      <div class="precip-row precip-lt">
        <span class="precip-term">Letzte 90 Tage</span>
        <span class="precip-val">${lt.value_mm??'–'}&thinsp;<small>mm</small></span>
        <span class="precip-comp">${escHtml(lt.comparison_de??'')}</span>
      </div>`;
    appendLink(card);
    grid.appendChild(card);
  }

  // Gewässer und Pegel
  if(ind.hydro){
    const h=ind.hydro, cfg=CDI[h.index??1]??CDI[1];
    const card=makeCard(h.icon??'💧', h.label_de??'Gewässer und Pegel', h.index??1, cfg, h.link);
    card.querySelector('.ind-status').textContent=h.plain_de??h.label_status_de??'–';
    const hVal=document.createElement('span'); hVal.className='ind-val';
    if(h.value_q_m3s!=null){
      // Bevorzuge echte m³/s-Daten wenn vorhanden
      let html=`Aktuell: ${h.value_q_m3s}&thinsp;<small>m³/s</small>`;
      if(h.value_q_norm_min!=null && h.value_q_norm_max!=null){
        const period=h.value_q_norm_period??'Normalwert';
        html+=`<br><small>Normal (${escHtml(period)}): ${h.value_q_norm_min}\u2013${h.value_q_norm_max}\u202fm³/s</small>`;
      }
      hVal.innerHTML=html;
    } else if(h.value_q_rel!=null){
      hVal.innerHTML=`Aktuell: ${h.value_q_rel}&thinsp;<small>% des langjährigen Mittelabflusses</small>`;
    } else if(h.flow_range_de){
      hVal.innerHTML=`Normalbereich (Stufe ${h.index??1}): <small>${escHtml(h.flow_range_de)}</small>`;
    }
    if(hVal.innerHTML) card.appendChild(hVal);
    appendLink(card);
    grid.appendChild(card);
  }

  // Bodenfeuchte
  if(ind.soil_moisture){
    const s=ind.soil_moisture, cfg=CDI[s.index??1]??CDI[1];
    const card=makeCard(s.icon??'🌱', s.label_de??'Bodenfeuchte', s.index??1, cfg, s.link);
    card.querySelector('.ind-status').textContent=s.plain_de??s.label_status_de??'–';
    if(s.value_pct!=null){
      const v=document.createElement('span'); v.className='ind-val';
      v.innerHTML=`${s.value_pct}&thinsp;<small>% der maximalen Feldkapazität</small>`;
      card.appendChild(v);
    }
    appendLink(card);
    grid.appendChild(card);
  }
}

function makeCard(icon, label, idx, cfg, link){
  const card=document.createElement('div'); card.className='ind-card';
  card.style.setProperty('--ind-color',cfg.hex);
  card.style.setProperty('--ind-bg',cfg.bg);
  card.innerHTML=`
    <div class="ind-top">
      <span class="ind-icon">${icon}</span>
      <span class="ind-label">${escHtml(label)}</span>
      <span class="ind-pill" style="background:${cfg.hex}">${idx}</span>
    </div>
    <p class="ind-status"></p>`;
  if(link) card.dataset.link=link;
  return card;
}

function appendLink(card){
  if(!card.dataset.link) return;
  const a=document.createElement('a');
  a.className='ind-detail-link'; a.href=card.dataset.link;
  a.target='_blank'; a.rel='noopener';
  a.textContent='Details auf trockenheit.admin.ch \u2197';
  card.appendChild(a);
}

function renderForecast(b){
  const fc=$('forecastBlock');
  const p50=b.forecast_cdi_p50, summary=b.forecast_summary_de;
  if(!p50){
    fc.innerHTML=`<p class="muted">Keine Prognosedaten verfügbar.</p>`;
    return;
  }
  const cfg=CDI[p50]??CDI[1];
  fc.innerHTML=`
    <div class="fc-summary-card" style="border-color:${cfg.border};background:${cfg.bg}">
      <div class="fc-icon">${cfg.emoji}</div>
      <div class="fc-body">
        <p class="fc-main-text">${escHtml(summary??'')}</p>
        <div class="fc-detail">
          <span class="fc-label-small">Prognose CDI (P50)</span>
          <span class="fc-val" style="color:${cfg.hex}">Stufe ${p50}: ${escHtml((CDI[p50]??{}).word??'')}</span>
          ${b.forecast_valid_at?`<span class="fc-valid">Prognose für die Woche ab ${fmt(b.forecast_valid_at)}</span>`:''}
        </div>
      </div>
    </div>
    <p class="fc-note muted">Ensemble-Prognose BAFU/MeteoSchweiz. P50 = wahrscheinlichste Entwicklung. P10/P90 für CDI nicht verfügbar.</p>`;
}

function renderSources(sources, generatedAt){
  const list=$('sourcesList');
  list.innerHTML=sources.map(s=>`
    <div class="src-row">
      <span class="src-name">${escHtml(s.name)}</span>
      ${s.data_date?`<span class="src-date">Datenstand: ${fmt(s.data_date)}</span>`:''}
      <a class="src-link" href="${escAttr(s.url)}" target="_blank" rel="noopener noreferrer">${escHtml(s.url)}&nbsp;↗</a>
    </div>`).join('');
  if(generatedAt) list.insertAdjacentHTML('beforeend',`<div class="src-row src-generated"><span class="src-name">Briefing generiert</span><span class="src-date">${fmtDt(generatedAt)}</span></div>`);
}

// ── Leaflet-Karte (EPSG:2056 / LV95) ─────────────────────────────────────────
const RESOLUTIONS_2056=[
  4000,3750,3500,3250,3000,2750,2500,2250,2000,1750,
  1500,1250,1000, 750, 650, 500, 250, 100,  50,  20,
    10,   5, 2.5,   2, 1.5,   1, 0.5,
];
const CRS_2056=new L.Proj.CRS(
  'EPSG:2056',
  '+proj=somerc +lat_0=46.9524055555556 +lon_0=7.43958333333333 +k_0=1 '+
  '+x_0=2600000 +y_0=1200000 +ellps=bessel +towgs84=674.374,15.056,405.346,0,0,0,0 +units=m +no_defs',
  {resolutions:RESOLUTIONS_2056, origin:[2420000,1350000], bounds:L.bounds([2420000,1030000],[2900000,1350000])}
);

function initOrUpdateMap(regionId){
  const container=$('map');
  if(!leafletMap){
    leafletMap=L.map(container,{crs:CRS_2056,center:[46.82,8.22],zoom:3,minZoom:0,maxZoom:26,attributionControl:true});
    L.tileLayer(
      'https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-grau/default/current/2056/{z}/{x}/{y}.jpeg',
      {attribution:'© <a href="https://www.swisstopo.admin.ch" target="_blank">swisstopo</a>',maxZoom:26,tileSize:256}
    ).addTo(leafletMap);
    L.tileLayer.wms('https://wms.geo.admin.ch/',{
      layers:'ch.bafu.trockenheitsindex',format:'image/png',transparent:true,
      opacity:0.72,version:'1.3.0',crossOrigin:true,
      attribution:'© <a href="https://www.bafu.admin.ch" target="_blank">BAFU</a>',
    }).addTo(leafletMap);
    L.tileLayer.wms('https://wms.geo.admin.ch/',{
      layers:'ch.bafu.trockenheitswarnungen',format:'image/png',
      transparent:true,opacity:0.6,version:'1.3.0',crossOrigin:true,
    }).addTo(leafletMap);
  }
  setTimeout(()=>{
    leafletMap.invalidateSize();
    if(regionId) zoomToRegion(regionId);
  },120);
  if(!allRegionsLayer) addRegionClickLayer();
}

async function addRegionClickLayer(){
  if(!warnregionenGeoJSON){
    try{ const r=await fetch(`${DATA_BASE}/lookups/warnregionen.geojson`); if(r.ok) warnregionenGeoJSON=await r.json(); }
    catch(e){ return; }
  }
  try{
    const r=await fetch(`${BRIEFING_BASE}/index.json`);
    if(r.ok){ const d=await r.json(); briefingIndex={}; for(const reg of d.regions) briefingIndex[reg.region_id]=reg; }
  } catch(e){}
  if(!warnregionenGeoJSON) return;
  allRegionsLayer=L.geoJSON(warnregionenGeoJSON,{
    style:{color:'#999',weight:0.8,fillOpacity:0},
    onEachFeature(feature,layer){
      layer.on('click',e=>{
        const rid=Number(feature.properties.region_id);
        const info=briefingIndex?.[rid];
        const name=feature.properties.Name??`Region ${rid}`;
        const cfg=CDI[info?.cdi]??CDI[1];
        let html=`<strong>${escHtml(name)}</strong>`;
        if(info){
          html+=`<table class="map-popup">
            <tr><td>Trockenheitsindex</td><td><b style="color:${cfg.hex}">${escHtml(info.cdi_label_de)}</b></td></tr>
            <tr><td>Gültig ab</td><td>${fmt(info.measured_at)}</td></tr>
            <tr><td>Gültig bis</td><td>${fmtAddDays(info.measured_at,6)}</td></tr>
          </table>`;
        } else { html+='<br><em>Noch keine Briefing-Daten.</em>'; }
        L.popup({maxWidth:280}).setLatLng(e.latlng).setContent(html).openOn(leafletMap);
      });
    },
  }).addTo(leafletMap);
}

async function zoomToRegion(regionId){
  if(!warnregionenGeoJSON){
    try{ const r=await fetch(`${DATA_BASE}/lookups/warnregionen.geojson`); if(r.ok) warnregionenGeoJSON=await r.json(); }
    catch(e){ return; }
  }
  const feature=warnregionenGeoJSON.features.find(f=>Number(f.properties.region_id)===Number(regionId));
  if(!feature) return;
  if(regionLayer) leafletMap.removeLayer(regionLayer);
  regionLayer=L.geoJSON(feature,{style:{color:'#1a1a1a',weight:2,fillOpacity:0.08,fillColor:'#1a1a1a'}}).addTo(leafletMap);
  const bounds=coordsBounds(feature.geometry.coordinates);
  if(bounds) leafletMap.fitBounds(bounds,{padding:[32,32]});
}

function coordsBounds(coords){
  const pts=[];
  const collect=c=>(typeof c[0]==='number'?pts.push(c):c.forEach(collect));
  collect(coords);
  if(!pts.length) return null;
  const lngs=pts.map(p=>p[0]), lats=pts.map(p=>p[1]);
  return L.latLngBounds([Math.min(...lats),Math.min(...lngs)],[Math.max(...lats),Math.max(...lngs)]);
}

// ── Hilfsfunktionen ───────────────────────────────────────────────────────────
function fmt(ds){
  if(!ds||ds==='unbekannt') return 'unbekannt';
  try{ return new Intl.DateTimeFormat('de-CH',{day:'2-digit',month:'long',year:'numeric'}).format(new Date(ds)); } catch{ return ds; }
}
function fmtAddDays(ds,days){
  if(!ds||ds==='unbekannt') return '–';
  try{ const d=new Date(ds); d.setDate(d.getDate()+days); return new Intl.DateTimeFormat('de-CH',{day:'2-digit',month:'long',year:'numeric'}).format(d); } catch{ return ds; }
}
function fmtDt(ds){
  if(!ds) return '–';
  try{ return new Intl.DateTimeFormat('de-CH',{day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Zurich'}).format(new Date(ds))+' MEZ'; } catch{ return ds; }
}
function escHtml(s){ return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function escAttr(s){ return escHtml(s); }
function showErr(msg){ errorMsg.textContent=msg; errorState.hidden=false; loadingState.hidden=true; briefingEl.hidden=true; }

init();
