'use strict';
if(window.location.protocol==='file:'){document.body.innerHTML='<div style="font-family:system-ui;padding:40px;text-align:center"><h2>Lokaler Server erforderlich</h2><pre style="display:inline-block;background:#111;color:#f5f5f3;padding:16px;border-radius:8px;margin-top:16px">cd drybrief-suisse\npython -m http.server 8080</pre><br><br><a href="http://localhost:8080/frontend/simple.html">http://localhost:8080/frontend/simple.html</a></div>';throw new Error('file://');}

const DATA_BASE=window.location.pathname.includes('/frontend')?'../data':'./data';

const LEVELS={
  1:{bg:'#d1fae5',fg:'#064e3b',emoji:'💧',word:'Nicht trocken',sub:'Alles in Ordnung — keine Trockenheit.'},
  2:{bg:'#fef9c3',fg:'#713f12',emoji:'☀️',word:'Leicht trocken',sub:'Erste Anzeichen — die Lage im Auge behalten.'},
  3:{bg:'#ffedd5',fg:'#7c2d12',emoji:'🌵',word:'Trocken',sub:'Erhebliche Trockenheit — Massnahmen prüfen.'},
  4:{bg:'#fee2e2',fg:'#7f1d1d',emoji:'🔥',word:'Sehr trocken',sub:'Grosse Trockenheit — Behörden informieren und handeln.'},
  5:{bg:'#1c0101',fg:'#fef2f2',emoji:'🚨',word:'Extrem trocken',sub:'Kritische Lage — sofort handeln.'},
};

let searchIndex={}, currentEntry=null, suggIdx=-1;
const $=id=>document.getElementById(id);

async function init(){
  try{
    const r=await fetch(`${DATA_BASE}/lookups/search_index.json`);
    searchIndex=await r.json();
    $('searchInput').disabled=false;
    $('searchInput').placeholder='Gemeinde oder Kanton …';
    bindEvents();
  }catch(e){ $('searchInput').placeholder='Pipeline ausführen — search_index.json fehlt'; }
}

function bindEvents(){
  const inp=$('searchInput'),sugg=$('suggestions');
  inp.addEventListener('input',()=>{
    const q=inp.value.trim().toLowerCase();
    if(!q){sugg.hidden=true;return;}
    const res=Object.entries(searchIndex).filter(([k])=>k.startsWith(q)||k.includes(q)).sort(([a],[b])=>(a.startsWith(q)?0:1)-(b.startsWith(q)?0:1)).slice(0,7);
    if(!res.length){sugg.hidden=true;return;}
    sugg.innerHTML='';sugg.hidden=false;
    res.forEach(([,e])=>{
      const li=document.createElement('li');
      li.innerHTML=`<span>${e.type==='kanton'?'🏛':'📍'}</span> <strong>${e.display_name}</strong>`;
      li.addEventListener('click',()=>{ inp.value=e.display_name; sugg.hidden=true; loadBriefing(e); });
      sugg.appendChild(li);
    });
  });
  inp.addEventListener('keydown',ev=>{ if(ev.key==='Escape') sugg.hidden=true; });
  document.addEventListener('click',e=>{ if(!e.target.closest('.search-box')) sugg.hidden=true; });
  $('backBtn').addEventListener('click',()=>{ $('resultView').hidden=true; $('searchView').hidden=false; $('searchInput').value=''; $('searchInput').focus(); });
}

async function loadBriefing(entry){
  const rid=entry.primary_region_id??entry.region_id;
  if(!rid) return;
  try{
    const r=await fetch(`${DATA_BASE}/briefings/regions/${rid}.json`);
    if(!r.ok) throw new Error('Briefing nicht gefunden');
    showResult(await r.json(), entry);
  }catch(e){ alert('Fehler: '+e.message+'\nBitte Pipeline ausführen.'); }
}

function showResult(b, entry){
  const cdi=Math.max(1,Math.min(5,b.cdi??1)), lv=LEVELS[cdi];
  const body=document.body;
  body.style.background=lv.bg; body.style.color=lv.fg;

  $('bigEmoji').textContent=lv.emoji;
  $('locationName').textContent=entry.display_name;
  $('bigWord').textContent=lv.word;
  $('bigSub').textContent=lv.sub;
  $('bigWord').style.color=lv.fg;

  // Mini-Cards
  const ind=b.indicators??{};
  const st=ind.precip?.short_term??{};
  $('precipVal').textContent=st.comparison_de??'–';
  $('hydroVal').textContent=ind.hydro?.plain_de??'–';
  $('soilVal').textContent=ind.soil_moisture?.plain_de??'–';

  // Prognose
  const fc=$('forecastStrip');
  fc.textContent=b.forecast_summary_de??'Keine Prognose verfügbar.';

  // Metainfo
  const age=b.data_age_days;
  $('dataNote').textContent=`Daten vom ${fmtDate(b.measured_at)}${age?` (${age} Tage alt)`:''}`;
  if(age>7) $('dataNote').style.color='#dc2626';

  $('officialBtn').href=b.region_url??'https://www.trockenheit.admin.ch';

  // Karten-Farbe anpassen
  ['precipCard','hydroCard','soilCard'].forEach(id=>{
    $( id ).style.background=lightenHex(lv.bg,0.4);
    $( id ).style.borderColor=lv.fg+'22';
    $( id ).style.color=lv.fg;
  });
  $('officialBtn').style.background=lv.fg;
  $('officialBtn').style.color=lv.bg;

  $('searchView').hidden=true;
  $('resultView').hidden=false;
}

function fmtDate(ds){
  try{return new Intl.DateTimeFormat('de-CH',{day:'2-digit',month:'long',year:'numeric'}).format(new Date(ds));}catch{return ds??'–';}
}

function lightenHex(hex,amount){
  // Very simple mix with white
  return hex; // just return same bg (cards use opacity for contrast)
}

init();
