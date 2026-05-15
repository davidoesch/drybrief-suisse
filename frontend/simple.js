'use strict';
if(window.location.protocol==='file:'){document.body.innerHTML='<div style="font-family:system-ui;padding:40px;text-align:center"><h2>Lokaler Server erforderlich</h2><pre style="display:inline-block;background:#111;color:#f5f5f3;padding:16px;border-radius:8px;margin-top:16px">cd drybrief-suisse\npython -m http.server 8080</pre><br><br><a href="http://localhost:8080/frontend/simple.html">http://localhost:8080/frontend/simple.html</a></div>';throw new Error('file://');}

const DATA_BASE=window.location.pathname.includes('/frontend')?'../data':'./data';

let searchIndex={}, currentEntry=null;
const $=id=>document.getElementById(id);

async function init(){
  try{
    const r=await fetch(`${DATA_BASE}/lookups/search_index.json`);
    searchIndex=await r.json();
    $('searchInput').disabled=false;
    $('searchInput').placeholder='Gemeinde oder Kanton \u2026';
    bindEvents();
  }catch(e){ $('searchInput').placeholder='Pipeline ausführen \u2013 search_index.json fehlt'; }
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
      li.addEventListener('click',()=>{ inp.value=e.display_name; sugg.hidden=true; currentEntry=e; loadBriefing(e); });
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
  const cdi=Math.max(1,Math.min(5,b.cdi??1));
  // Farben und Texte kommen aus dem Briefing JSON (generiert aus thresholds.json)
  const bg    = b.cdi_bg_hex ?? '#d1fae5';
  const fg    = b.cdi_fg_hex ?? '#064e3b';
  const emoji = b.cdi_emoji  ?? '💧';
  const word  = b.cdi_label_de ?? 'Nicht trocken';
  const sub   = b.cdi_sub_de   ?? b.summary_de ?? '';

  const body=document.body;
  body.style.background=bg; body.style.color=fg;

  $('bigEmoji').textContent=emoji;
  $('locationName').textContent=entry.display_name;
  $('bigWord').textContent=word;
  $('bigSub').textContent=sub;
  $('bigWord').style.color=fg;

  // Mini-Cards
  const ind=b.indicators??{};
  const st=ind.precip?.short_term??{};
  $('precipVal').textContent=st.comparison_de??'–';
  $('hydroVal').textContent=ind.hydro?.plain_de??'–';
  $('soilVal').textContent=ind.soil_moisture?.plain_de??'–';

  // Prognose
  $('forecastStrip').textContent=b.forecast_summary_de??'Keine Prognose verfügbar.';

  // Metainfo
  const age=b.data_age_days;
  $('dataNote').textContent=`Daten vom ${fmtDate(b.measured_at)}${age?` (${age} Tage alt)`:''}`;
  if(age>7) $('dataNote').style.color='#dc2626';

  $('officialBtn').href=b.region_url??'https://www.trockenheit.admin.ch';

  // Empfehlungen
  const recs=(b.recommendations_simple_de??b.recommendations_de??[]).slice(0,5);
  const recsCard=$('recsCard');
  if(recs.length){
    $('recsList').innerHTML=recs.map(r=>`<li><span class="rec-check">✓</span><span>${escHtml(r)}</span></li>`).join('');
    recsCard.hidden=false;
    recsCard.style.borderColor=fg+'44';
  } else { recsCard.hidden=true; }

  // Kartenfarben angleichen
  ['precipCard','hydroCard','soilCard'].forEach(id=>{
    $(id).style.borderColor=fg+'33';
    $(id).style.color=fg;
  });
  $('officialBtn').style.background=fg;
  $('officialBtn').style.color=bg;

  $('searchView').hidden=true;
  $('resultView').hidden=false;
}

function fmtDate(ds){
  try{return new Intl.DateTimeFormat('de-CH',{day:'2-digit',month:'long',year:'numeric'}).format(new Date(ds));}catch{return ds??'–';}
}
function escHtml(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

init();
