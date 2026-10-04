(function(){
"use strict";
var QS = new URLSearchParams(location.search);
// ?api=... wins; opened from a file -> local dev API; otherwise the API serves this page, so same origin.
var API = (QS.get("api") || (location.protocol==="file:" ? "http://127.0.0.1:8000" : location.origin)).replace(/\/$/,"");
var LABEL = {normal:"ปกติ", watch:"เฝ้าระวัง", alert:"เตือนภัย", unknown:"ไม่มีเกณฑ์เทียบ"};
var COLOR = {normal:"#17835a", watch:"#c98a00", alert:"#d2372f", unknown:"#6b7f8a"};
var INK = {normal:"var(--normal-ink)", watch:"var(--watch-ink)", alert:"var(--alert-ink)", unknown:"var(--unknown-ink)"};   // for text: darker in light mode
var REFRESH_MS = 5*60*1000;
var Z = {alert:600, watch:400, unknown:200, normal:0};
var CLUSTER_OFF_ZOOM = 11;
var DEFAULT_PROVINCE = "พระนครศรีอยุธยา";
var province = DEFAULT_PROVINCE;   // always open on the default province; the choice is not remembered between visits
var filter = null;
var stations = [], byId = {}, markers = {}, sel = null, origin = null, pinMode = false, originLayer = null;
function $(id){return document.getElementById(id);}
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];});}
function store(k,v){try{ if(v===null) localStorage.removeItem(k); else if(v===undefined) return localStorage.getItem(k); else localStorage.setItem(k,v);}catch(e){} return null;}
function fmtTs(ts){ if(!ts) return "-"; return new Date(ts).toLocaleString("th-TH",{timeZone:"Asia/Bangkok",day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"}); }
function fmtAge(m){ if(m==null) return "ไม่มีข้อมูล"; if(m<60) return m+" นาทีที่แล้ว"; var h=Math.floor(m/60); return h<48 ? h+" ชม. ที่แล้ว" : Math.floor(h/24)+" วันที่แล้ว"; }
function pctText(s){ return s.pct_of_bank==null ? "-" : Math.round(s.pct_of_bank)+"%"; }
var TREND = {rising:["▲","กำลังสูงขึ้น"], falling:["▼","กำลังลดลง"], steady:["■","ทรงตัว"]};
function trendHtml(s){
  if(!s.trend) return "";
  var t = TREND[s.trend], r = s.trend_pct_per_hr, eta = s.eta_to_bank_h;
  return '<span class="trend '+s.trend+'"><span aria-hidden="true">'+t[0]+'</span>'+t[1]+(r!=null?' '+(r>0?"+":"")+r.toFixed(1)+'%/ชม.':'')+'</span>'+
    (eta!=null?' <span class="note">· ถ้าเป็นแบบนี้ต่อ จะถึงตลิ่งใน ~'+eta+' ชม.</span>':'');
}
function statusText(s){ return (s.pct_of_bank!=null && s.pct_of_bank>=100) ? "ล้นตลิ่ง" : LABEL[s.status]; }
function isMobile(){ return window.matchMedia("(max-width:899px)").matches; }

/* ---------- small helpers: toast, glyphs ---------- */
var toastTimer = null;
function toast(msg, sticky){
  var t = $("toast"); if(!msg){ t.hidden = true; return; }
  t.textContent = msg; t.hidden = false; clearTimeout(toastTimer);
  if(!sticky) toastTimer = setTimeout(function(){ t.hidden = true; }, 4500);
}
function say(msg, sticky){ var n = $("originNote"); n.textContent = msg||""; n.hidden = !msg; toast(msg, sticky); }
function shapeSvg(status, size, stale){   // small status glyph used on filter chips
  var c = COLOR[status] || COLOR.unknown, h = size/2, r = size*0.34, g = "";
  if(status==="normal") g = '<circle cx="'+h+'" cy="'+h+'" r="'+r+'" fill="'+c+'"/>';
  else if(status==="watch") g = '<rect x="'+(h-r)+'" y="'+(h-r)+'" width="'+(r*2)+'" height="'+(r*2)+'" rx="'+(r*.7)+'" fill="'+c+'"/>';
  else if(status==="alert") g = '<circle cx="'+h+'" cy="'+h+'" r="'+(r*1.1)+'" fill="'+c+'"/><circle cx="'+h+'" cy="'+h+'" r="'+(h-1)+'" fill="none" stroke="'+c+'" stroke-width="1.5" opacity=".5"/>';
  else g = '<circle cx="'+h+'" cy="'+h+'" r="'+(r*.8)+'" fill="none" stroke="'+c+'" stroke-width="2"/>';
  var ring = stale ? '<circle cx="'+h+'" cy="'+h+'" r="'+(h-1.5)+'" fill="none" stroke="#6b7f8a" stroke-width="1.6" stroke-dasharray="3 2.5"/>' : '';
  return '<svg width="'+size+'" height="'+size+'" viewBox="0 0 '+size+' '+size+'" aria-hidden="true">'+ring+g+'</svg>';
}
var compact = false;   // zoomed far out: smaller markers so a province's stations do not pile up
function icon(s, selected){
  var st = s.status, base = compact ? (st==="alert" ? 26 : st==="watch" ? 23 : 20) : (st==="alert" ? 34 : st==="watch" ? 30 : 26);
  var size = base + (selected ? 6 : 0);
  var label = s.pct_of_bank==null ? "–" : Math.round(s.pct_of_bank);
  return L.divIcon({className:"", iconSize:[size,size], iconAnchor:[size/2,size/2],
    html:'<div class="mk '+st+(compact?' sm':'')+(s.stale?' stale':'')+(selected?' sel':'')+'" style="width:'+size+'px;height:'+size+'px"><span>'+label+'</span></div>'});
}
var RANK = {unknown:0, normal:1, watch:2, alert:3};
function clusterIcon(c){
  var kids = c.getAllChildMarkers(), worst = "unknown";
  kids.forEach(function(m){ var s = m.options.status; if(RANK[s]>RANK[worst]) worst = s; });
  return L.divIcon({className:"", iconSize:[44,44], iconAnchor:[22,22], html:'<div class="cl '+worst+'" aria-label="'+kids.length+' สถานี"><span>'+kids.length+'</span></div>'});
}

/* ---------- map ---------- */
var map = L.map("map",{zoomControl:false,attributionControl:true}).setView([13.5,101.0],6);
L.control.zoom({position:"bottomright"}).addTo(map);
map.attributionControl.setPrefix(false);
// OpenStreetMap tiles (no API key). The "soft" look and the dark mode come from CSS filters on .basemap.
var baseTiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19,className:"basemap",attribution:"© OpenStreetMap contributors"}).addTo(map);
// Nearby stations merge into a numbered cluster (coloured by its worst status); zoom in to split them.
var layer = L.markerClusterGroup({showCoverageOnHover:false, maxClusterRadius:36, disableClusteringAtZoom:CLUSTER_OFF_ZOOM,
                                  spiderfyOnMaxZoom:false, iconCreateFunction:clusterIcon});
map.addLayer(layer);

function fitPad(){   // the part of the map not covered by the top bar / sheet
  if(isMobile()){   // the two buttons floating above the sheet cover the map too
    var fab = $("fab"), fabH = (fab && getComputedStyle(fab).display!=="none") ? fab.offsetHeight : 0;
    return {paddingTopLeft:[24, $("topbar").offsetHeight+24], paddingBottomRight:[24, $("sheet").offsetHeight+fabH+16]};
  }
  return {paddingTopLeft:[440, 96], paddingBottomRight:[24, 24]};
}
function flyToVisible(ll, zoom){   // fly so that ll lands in the middle of the uncovered area
  var p = fitPad(), tl = p.paddingTopLeft, br = p.paddingBottomRight;
  var c = map.project(ll, zoom).subtract([(tl[0]-br[0])/2, (tl[1]-br[1])/2]);
  map.flyTo(map.unproject(c, zoom), zoom);
}

var probeMark = null;
function probe(latlng){
  if(probeMark){ map.removeLayer(probeMark); probeMark = null; }
  var m = probeMark = L.circleMarker(latlng,{radius:7,color:"#fff",weight:2,fillColor:"#0b6e8f",fillOpacity:1}).addTo(map);
  m.bindPopup('<div class="pp">กำลังค้นหาสถานีใกล้จุดนี้…</div>',{autoPanPadding:[24,120]}).openPopup();
  getJSON("/stations/nearby?lat="+latlng.lat+"&lng="+latlng.lng+"&limit=3").then(function(list){
    if(probeMark!==m) return;
    list.forEach(function(s){ byId[s.id]=Object.assign(byId[s.id]||{},s); });
    m.setPopupContent('<div class="pp"><b>สถานีใกล้จุดที่เลือก</b>'+list.map(function(s){
      return '<button type="button" class="pprow" data-id="'+esc(s.id)+'"><span>'+esc(s.name)+'<small>'+esc(s.province||"")+' · '+s.distance_km+' กม.'+(s.stale?' · ข้อมูลไม่อัปเดต':'')+'</small></span><strong style="color:'+(COLOR[s.status]||COLOR.unknown)+'">'+esc(statusText(s))+' '+pctText(s)+'</strong></button>';
    }).join("")+'<small style="color:var(--muted)">กดชื่อสถานีเพื่อดูรายละเอียด</small></div>');
  }).catch(function(){ if(probeMark===m) m.setPopupContent('<div class="pp">เรียกข้อมูลสถานีใกล้เคียงไม่ได้ ลองใหม่อีกครั้ง</div>'); });
}
map.on("zoomend",function(){
  var c = map.getZoom() < CLUSTER_OFF_ZOOM;
  if(c!==compact){ compact = c; if(stations.length) drawMarkers(); }
});
map.on("popupopen",function(e){
  var el = e.popup.getElement(); if(!el) return;
  el.addEventListener("click",function(ev){
    var b = ev.target.closest ? ev.target.closest(".pprow") : null; if(!b) return;
    select(b.getAttribute("data-id"), false); map.closePopup();
  });
});
function showErr(msg){ var e=$("err"); e.hidden=!msg; e.textContent=msg||""; if(msg) toast(msg); }
function getJSON(path){
  if(path==="/stations" && window.__stations){   // the head script already asked for it: use that answer once, then fetch afresh as usual
    var early = window.__stations; window.__stations = null;
    return early.catch(function(){ return fetch(API+path).then(function(r){ if(!r.ok) throw new Error("HTTP "+r.status); return r.json(); }); });
  }
  return fetch(API+path).then(function(r){ if(!r.ok) throw new Error("HTTP "+r.status); return r.json(); });
}

/* ---------- bottom sheet (mobile) / side panel (desktop) ---------- */
var sheetState = "half";
function sheetHeights(){ var H = window.innerHeight; return {peek:Math.min(196,Math.round(H*.3)), half:Math.round(H*.46), full:H-Math.round($("topbar").offsetHeight)-28}; }
function setSheet(st){
  sheetState = st; $("sheet").setAttribute("data-state", st);
  if(isMobile()) $("sheet").style.setProperty("--sheet-h", sheetHeights()[st]+"px"); else $("sheet").style.removeProperty("--sheet-h");
}
(function(){
  var grab = $("grab"), sheet = $("sheet"), y0 = 0, h0 = 0, moved = 0, active = false;
  function order(){ return ["peek","half","full"]; }
  grab.addEventListener("pointerdown",function(e){ active = true; y0 = e.clientY; h0 = sheet.offsetHeight; moved = 0; grab.setPointerCapture(e.pointerId); sheet.classList.add("drag"); });
  grab.addEventListener("pointermove",function(e){
    if(!active) return; moved = Math.max(moved, Math.abs(e.clientY-y0));
    var hs = sheetHeights(), h = Math.max(hs.peek, Math.min(hs.full, h0 - (e.clientY-y0)));
    sheet.style.setProperty("--sheet-h", h+"px");
  });
  function end(){
    if(!active) return; active = false; sheet.classList.remove("drag");
    if(moved < 6){ var o = order(); setSheet(o[(o.indexOf(sheetState)+1)%o.length]); return; }
    var hs = sheetHeights(), h = sheet.offsetHeight, best = "half", bd = 1e9;
    order().forEach(function(k){ var d = Math.abs(hs[k]-h); if(d<bd){ bd = d; best = k; } });
    setSheet(best);
  }
  grab.addEventListener("pointerup", end); grab.addEventListener("pointercancel", end);
  grab.addEventListener("keydown",function(e){ if(e.key==="Enter"||e.key===" "){ e.preventDefault(); var o = order(); setSheet(o[(o.indexOf(sheetState)+1)%o.length]); } });
  window.addEventListener("resize",function(){ setSheet(sheetState); });
})();
function revealInSheet(el){
  if(isMobile() && sheetState==="peek") setSheet("half");
  var body = $("sheetBody"); body.scrollTo({top:Math.max(0, el.offsetTop-8), behavior:"smooth"});
}

function inProv(s){ return !province || s.province===province; }
function visible(s){
  if(!inProv(s)) return false;
  if(!filter) return true;
  var v = (s);
  return filter==="stale" ? v.stale : v.status===filter;
}

/* ---------- drawing ---------- */
function drawMarkers(){
  layer.clearLayers(); markers = {};
  var batch = [];
  stations.forEach(function(s){
    if(!visible(s) && s.id!==sel) return;
    var v = (s);
    var m = L.marker([s.lat,s.lng],{icon:icon(v,s.id===sel),keyboard:true,title:s.name+" "+statusText(v),status:v.status,zIndexOffset:(s.id===sel?1000:Z[v.status]||0)});
    m.on("click",function(){ select(s.id,false); });
    markers[s.id] = m; batch.push(m);
  });
  layer.addLayers(batch);
}
function counts(){
  var list = stations.filter(inProv), c = {alert:0,watch:0,normal:0,unknown:0}, stale = 0, latest = 0;
  list.forEach(function(s0){ var s = (s0); c[s.status]++; if(s.stale) stale++; var t = s0.ts ? Date.parse(s0.ts) : 0; if(t>latest) latest = t; });
  return {c:c, n:list.length, stale:stale, latest:latest};
}
function drawChips(){
  var k = counts(), c = k.c;
  var html = ["alert","watch","normal","unknown"].filter(function(s){ return s!=="unknown" || c.unknown>0; }).map(function(s){
    return '<button type="button" class="chip" data-f="'+s+'" aria-pressed="'+(filter===s)+'">'+shapeSvg(s,16,false)+LABEL[s]+" "+c[s]+'</button>';
  }).join("");
  html += '<button type="button" class="chip" data-f="stale" aria-pressed="'+(filter==="stale")+'">'+shapeSvg("unknown",16,true)+'ข้อมูลค้าง '+k.stale+'</button>';
  $("chips").innerHTML = html;
}
function pctHint(p){ return p>=100 ? "ล้นตลิ่งแล้ว" : p>=90 ? "ใกล้ล้นตลิ่ง" : p>=70 ? "น้ำสูงใกล้ตลิ่ง" : "ยังต่ำกว่าตลิ่ง"; }
function fastRisers(list){   // water rising >= 30 cm in 3 h, even if still far below the bank; one per site
  var best = {};
  list.forEach(function(s){
    if(s.stale || s.rise_3h_m==null || s.rise_3h_m<0.3) return;
    var k = s.lat.toFixed(2)+","+s.lng.toFixed(2); if(!best[k] || s.rise_3h_m>best[k].rise_3h_m) best[k] = s;
  });
  return Object.keys(best).map(function(k){ return best[k]; }).sort(function(a,b){ return b.rise_3h_m-a.rise_3h_m; }).slice(0,3);
}
var lastRain = null;   // {name, past, next} from the rain card, for the one-line summary
function oneLineText(list){   // only what the status card below does not already say: fast rises and rain
  var fast = fastRisers(list).length, bits = [];
  bits.push(fast ? "น้ำขึ้นเร็ว "+fast+" แห่งใน 3 ชม." : "ไม่มีสถานีที่น้ำขึ้นเร็ว");
  if(province && !origin && lastRain && lastRain.name==="จ."+province){
    if(lastRain.past!=null && lastRain.past>=1) bits.push("ฝนตกมาแล้ว "+Math.round(lastRain.past)+" มม. ใน 24 ชม.");
    if(lastRain.next>=1) bits.push("พยากรณ์ฝน "+Math.round(lastRain.next)+" มม.");
  }
  return bits.join(" · ");
}
function drawPills(c){   // the status in a glance, also visible when the phone's sheet is pulled down
  var h = "";
  if(c.alert) h += '<span class="badge alert">'+LABEL.alert+' '+c.alert+'</span>';
  if(c.watch) h += '<span class="badge watch">'+LABEL.watch+' '+c.watch+'</span>';
  if(!c.alert && !c.watch && c.normal) h += '<span class="badge normal">'+LABEL.normal+'</span>';
  $("sumPills").innerHTML = h;
}
function drawOneLine(){ var el = $("oneLine"); if(el) el.textContent = oneLineText(stations.filter(inProv)); }
var bootTimer = null;   // reveal the bottom of the panel shortly after the first data has been drawn
function endBoot(){ clearTimeout(bootTimer); bootTimer = setTimeout(function(){ if(window.__unboot) window.__unboot(); }, 600); }
function drawSummary(){
  var k = counts(), c = k.c, list = stations.filter(inProv);
  $("sumTitle").textContent = province ? "จ."+province : "ทั้งประเทศ";
  drawPills(c);
  $("sumSub").textContent = k.n+" สถานี"+(k.latest ? " · อัปเดต "+fmtTs(new Date(k.latest).toISOString()) : "");
  var top = null, rising = 0;
  list.forEach(function(s){
    if(s.stale) return;
    if(s.pct_of_bank!=null && (!top || s.pct_of_bank>top.pct_of_bank)) top = s;
    if(s.trend==="rising" && (s.status==="alert" || s.status==="watch")) rising++;
  });
  var kind, head;
  if(!k.n){ kind = "unknown"; head = "ยังไม่มีข้อมูลสถานี"; }
  else if(c.alert){ kind = "alert"; head = "เตือนภัย: น้ำสูงใกล้หรือล้นตลิ่ง "+c.alert+" จาก "+k.n+" สถานี"; }
  else if(c.watch){ kind = "watch"; head = "เฝ้าระวัง: น้ำเริ่มสูง "+c.watch+" จาก "+k.n+" สถานี"; }
  else if(c.normal){ kind = "normal"; head = "ระดับน้ำปกติ"+(k.n>1 ? " ทุกสถานีที่มีเกณฑ์เทียบ" : ""); }
  else { kind = "unknown"; head = "ยังประเมินไม่ได้ (ไม่มีเกณฑ์เทียบ)"; }
  var line2 = top ? '<div class="vm">น้ำสูงสุด <b>'+Math.round(top.pct_of_bank)+'%</b> ของตลิ่ง ('+pctHint(top.pct_of_bank)+') ที่ '+esc(top.name)+'</div>' : "";
  var bits = [];
  if(rising) bits.push("▲ กำลังสูงขึ้น "+rising+" สถานี");
  if(k.stale) bits.push("ข้อมูลค้าง "+k.stale+" สถานี");
  $("sumBody").innerHTML = '<div class="oneline" id="oneLine"></div><div class="verdict" style="--vc:'+COLOR[kind]+'"><div class="vh" style="color:'+INK[kind]+'">'+esc(head)+'</div>'+line2+
    (bits.length ? '<div class="vm">'+esc(bits.join(" · "))+'</div>' : "")+
    '<div class="vn">% ของตลิ่ง = ระดับน้ำเทียบกับความสูงตลิ่ง 100% คือน้ำเสมอตลิ่ง</div></div>'+fastHtml(list);
  drawOneLine(); endBoot();
}
function fastHtml(list){
  var f = fastRisers(list); if(!f.length) return "";
  return '<div class="fast"><h3>▲ น้ำขึ้นเร็วใน 3 ชม.ที่ผ่านมา</h3>'+f.map(function(s){
    return '<button type="button" class="fastrow" data-id="'+esc(s.id)+'"><span>'+esc(s.name)+'<small>'+esc(s.province||"")+' · '+pctText(s)+(s.pct_of_bank!=null?' ของตลิ่ง':'')+'</small></span><b style="color:'+(INK[s.status]||INK.unknown)+'">+'+Math.round(s.rise_3h_m*100)+' ซม.</b></button>';
  }).join("")+'<div class="note" style="margin:6px 0 0">นับจากระดับน้ำจริงของสถานี ไม่นับค่าที่กระโดดผิดปกติ</div></div>';
}
$("sumBody").addEventListener("click",function(e){ var b = e.target.closest ? e.target.closest(".fastrow") : null; if(b) select(b.getAttribute("data-id"),true); });
function redraw(){ drawMarkers(); drawChips(); drawSummary(); drawRivers(); if(typeof updateBoundary==="function") updateBoundary(); if(typeof refreshShelters==="function") refreshShelters(); if(typeof refreshRain==="function") refreshRain(); }

/* ---------- river lines ---------- */
var RIVER_MAX_KM = 80;
var riverLayer = L.layerGroup().addTo(map);
function kmBetween(a,b){
  var p = Math.PI/180, x = Math.sin((b.lat-a.lat)*p/2), y = Math.sin((b.lng-a.lng)*p/2);
  return 12742*Math.asin(Math.sqrt(x*x + Math.cos(a.lat*p)*Math.cos(b.lat*p)*y*y));
}
function riverSeg(a,b){
  var va = (a), vb = (b), st = RANK[va.status]>=RANK[vb.status] ? va.status : vb.status;
  L.polyline([[a.lat,a.lng],[b.lat,b.lng]],{color:COLOR[st]||COLOR.unknown,weight:4,opacity:.7,lineCap:"round"}).addTo(riverLayer)
    .bindTooltip(esc(a.river)+": "+esc(a.name)+" → "+esc(b.name)+" ("+Math.round(kmBetween(a,b))+" กม.)",{sticky:true});
}
function drawRivers(){
  riverLayer.clearLayers();
  if(!$("chkRiver").checked) return;
  var groups = {};
  stations.forEach(function(s){
    if(!s.river || s.ground_level==null || !inProv(s)) return;
    var k = s.river+"|"+(s.basin||""); (groups[k] = groups[k]||[]).push(s);
  });
  Object.keys(groups).forEach(function(k){
    var prev = null;
    groups[k].sort(function(a,b){ return b.ground_level-a.ground_level; }).forEach(function(s){  // high bed first = upstream
      if(prev){
        var d = kmBetween(prev,s);
        if(d<0.5) return;                       // the same site reported by two agencies
        if(d<=RIVER_MAX_KM) riverSeg(prev,s);   // farther apart: start a new stretch instead of joining
      }
      prev = s;
    });
  });
}
$("chkRiver").addEventListener("change", drawRivers);

/* satellite flood extent: tiles are proxied by our API (the key stays server-side); offered only when configured */
var floodTiles = null;
function drawFlood(){
  if(floodTiles){ map.removeLayer(floodTiles); floodTiles = null; }
  if(!$("chkFlood").checked) return;
  floodTiles = L.tileLayer(API+"/api/gistda/flood/"+$("floodPeriod").value+"/{z}/{x}/{y}",
    {opacity:.6, maxZoom:19, maxNativeZoom:15, errorTileUrl:"data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==", attribution:"น้ำท่วม: GISTDA"}).addTo(map);
}
$("chkFlood").addEventListener("change", drawFlood);
$("floodPeriod").addEventListener("change", drawFlood);
fetch(API+"/api/gistda/status").then(function(r){ return r.ok ? r.json() : null; }).then(function(j){
  if(j && j.enabled){ $("floodBox").hidden = false; syncQuick(); syncLayerCount(); }
}).catch(function(){});

/* ---------- lists & detail ---------- */
function rowHtml(s, extra){
  return '<li><button class="row" type="button" data-id="'+esc(s.id)+'"><span>'+esc(s.name)+'<small>'+esc(s.province||"")+(extra?" · "+esc(extra):"")+(s.stale?" · ข้อมูลไม่อัปเดต":"")+(s.twin_conflict?" · ⚠ สองแหล่งขัดกัน":"")+(s.trend==="rising"?" · ▲ สูงขึ้น":s.trend==="falling"?" · ▼ ลดลง":"")+'</small></span><span class="badge '+s.status+'">'+esc(statusText(s))+" "+pctText(s)+'</span></button></li>';
}
function showResults(title, list, extraFn){
  $("resSort").hidden = true;   // only the province list (showProvince) offers sorting
  $("results").hidden = false; $("resTitle").textContent = title;
  $("resList").innerHTML = list.length ? list.map(function(s){return rowHtml(s, extraFn?extraFn(s):"");}).join("") : '<li class="note">ไม่พบสถานี</li>';
}
function chartSvg(rs){
  var pts = rs.filter(function(r){return r.pct_of_bank!=null;});
  if(pts.length<2) return '<p class="note">กำลังสะสมข้อมูลย้อนหลัง จะแสดงกราฟเมื่อมีข้อมูลตั้งแต่ 2 รอบขึ้นไป</p>';
  var W=340,H=150,pl=8,pr=8,pt=8,pb=22, maxP=Math.max(120,Math.max.apply(null,pts.map(function(p){return p.pct_of_bank;}))*1.05), minP=0;
  var t0=new Date(pts[0].ts).getTime(), t1=new Date(pts[pts.length-1].ts).getTime(), span=Math.max(t1-t0,1);
  function X(t){return pl+(new Date(t).getTime()-t0)/span*(W-pl-pr);}
  function Y(v){return pt+(maxP-v)/(maxP-minP)*(H-pt-pb);}
  var s='<svg class="chart" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="กราฟเปอร์เซ็นต์ของตลิ่งย้อนหลัง">';
  s+='<rect x="'+pl+'" y="'+Y(maxP)+'" width="'+(W-pl-pr)+'" height="'+(Y(90)-Y(maxP))+'" fill="var(--alert-bg)" rx="4"/>';
  s+='<rect x="'+pl+'" y="'+Y(90)+'" width="'+(W-pl-pr)+'" height="'+(Y(70)-Y(90))+'" fill="var(--watch-bg)"/>';
  s+='<rect x="'+pl+'" y="'+Y(70)+'" width="'+(W-pl-pr)+'" height="'+(Y(0)-Y(70))+'" fill="var(--normal-bg)" rx="4"/>';
  s+='<line x1="'+pl+'" x2="'+(W-pr)+'" y1="'+Y(100)+'" y2="'+Y(100)+'" stroke="var(--alert)" stroke-dasharray="4 3"/><text x="'+(pl+3)+'" y="'+(Y(100)-3)+'" font-size="12" fill="var(--muted)">ตลิ่ง 100%</text>';
  s+='<path d="'+pts.map(function(p,i){return (i?"L":"M")+X(p.ts).toFixed(1)+" "+Y(p.pct_of_bank).toFixed(1);}).join(" ")+'" fill="none" stroke="var(--ink)" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>';
  var last=pts[pts.length-1]; s+='<circle cx="'+X(last.ts)+'" cy="'+Y(last.pct_of_bank)+'" r="4.5" fill="var(--ink)"/>';
  s+='<text x="'+pl+'" y="'+(H-6)+'" font-size="12" fill="var(--muted)">'+esc(fmtTs(pts[0].ts))+'</text><text x="'+(W-pr)+'" y="'+(H-6)+'" font-size="12" fill="var(--muted)" text-anchor="end">'+esc(fmtTs(last.ts))+'</text>';
  return s+'</svg>';
}
function twinHtml(s){
  if(!s.twins || !s.twins.length) return "";
  var txt = s.twins.map(function(x){ return esc(x.source||"")+' '+(x.pct_of_bank==null?"-":Math.round(x.pct_of_bank)+"%")+' ('+esc(LABEL[x.status])+')'; }).join(", ");
  return s.twin_conflict
    ? '<div class="warn" role="alert">จุดเดียวกันนี้มีอีกหน่วยงานรายงานและสถานะต่างกัน (ใช้ค่าตลิ่งคนละชุด): '+txt+' ให้ตรวจกับหน่วยงานในพื้นที่</div>'
    : '<p class="note">จุดเดียวกันนี้มีอีกหน่วยงานรายงานด้วย: '+txt+'</p>';
}
function relHtml(rel){
  if(!rel || (!rel.upstream.length && !rel.downstream.length)) return "";
  function list(title, a){
    if(!a.length) return "";
    return '<h3>'+title+'</h3><ul class="rows">'+a.map(function(x){
      return '<li><button class="row" type="button" data-id="'+esc(x.id)+'"><span>'+esc(x.name)+'<small>'+esc(x.province||"")+' · '+x.distance_km+' กม.'+
        (x.trend==="rising"?' · ▲ สูงขึ้น':x.trend==="falling"?' · ▼ ลดลง':'')+'</small></span><span class="badge '+x.status+'">'+
        (x.pct_of_bank==null?"-":Math.round(x.pct_of_bank)+"%")+'</span></button><button type="button" class="btn cmpbtn" data-cmp="'+esc(x.id)+'">เทียบกราฟกับสถานีนี้</button></li>';}).join("")+'</ul>';
  }
  return '<div class="rel"><h2 style="margin-top:14px">แม่น้ำเดียวกัน ('+esc(rel.river)+')</h2>'+
    '<p class="note" style="margin:0">ต้นน้ำ/ปลายน้ำประมาณจากระดับท้องน้ำ ไม่ใช่ข้อมูลเครือข่ายแม่น้ำจริง น้ำใช้เวลาไหลมาถึง ดูเป็นแนวทางเท่านั้น</p>'+
    list("ต้นน้ำ (ถ้าสูงขึ้น ปลายน้ำมักตามมา)", rel.upstream)+list("ปลายน้ำ", rel.downstream)+'</div>';
}
function shareBar(s){
  return '<div class="sharebar"><button class="btn" type="button" id="btnShare">แชร์ลิงก์สถานีนี้</button><span class="note" id="shareMsg" style="margin:0" aria-live="polite"></span></div>';
}
function gaugeHtml(s){
  if(s.pct_of_bank==null) return "";
  var w = Math.max(2, Math.min(100, s.pct_of_bank)), col = COLOR[s.status]||COLOR.unknown;
  return '<div class="gauge" role="img" aria-label="'+Math.round(s.pct_of_bank)+'% ของตลิ่ง"><i style="width:'+w+'%;background:'+col+'"></i><b style="left:'+(s.watch_pct||70)+'%"></b><b style="left:'+(s.alert_pct||90)+'%"></b></div>';
}
function firstPhone(p){ return String(p||"").split(/[,;\/]/)[0].trim(); }   // "0 2577 1964, 081..." -> "0 2577 1964"
function telHref(p){ return "tel:"+firstPhone(p).replace(/[^\d+]/g,""); }
var nearShl = {};   // station id -> nearest shelter list (fetched once, only for stations at watch / alert)
function shelterBox(s){
  var l = nearShl[s.id]; if(!(s.status==="watch" || s.status==="alert") || !l || !l.length) return "";
  var x = l[0], tel = firstPhone(x.phone);
  return '<div class="shbox"><h3>ศูนย์พักพิงที่ใกล้สถานีนี้ที่สุด</h3><div class="nm">'+esc(x.name)+'</div><small>ห่าง '+x.distance_km+' กม. · ต.'+esc(x.subdistrict)+' อ.'+esc(x.district)+(x.capacity?' · รองรับ ~'+x.capacity+' คน':'')+'</small>'+
    '<div class="acts">'+(tel?'<a href="'+esc(telHref(tel))+'">โทร '+esc(tel)+'</a>':'')+'<a target="_blank" rel="noopener" href="https://www.google.com/maps/dir/?api=1&destination='+x.lat+','+x.lng+'">นำทาง</a></div>'+
    '<div class="note" style="margin:6px 0 0">ข้อมูลจาก ปภ. อาจไม่เป็นปัจจุบัน โทรสอบถามก่อนเดินทาง (สายด่วน 1784)</div></div>';
}
function loadStationShelter(s){
  if(!(s.status==="watch" || s.status==="alert") || nearShl[s.id]!==undefined) return;
  nearShl[s.id] = null;   // asked once; an error just leaves the box out
  getJSON("/api/shelters?lat="+s.lat+"&lng="+s.lng+"&limit=1").then(function(l){
    nearShl[s.id] = l;
    var box = $("shBox"); if(sel===s.id && box) box.innerHTML = shelterBox(s);
  }).catch(function(){});
}
var cmpState = null, curRs = null, curRel = null;   // cmpState: {of, other, rs} while a second station is overlaid
function compareHtml(s, rs){
  var c = cmpState; if(!c || c.of!==s.id) return "";
  var a = (rs||[]).filter(function(r){return r.pct_of_bank!=null;}), b = c.rs.filter(function(r){return r.pct_of_bank!=null;});
  var head = '<div class="cmp"><h2>เทียบกับ '+esc(c.other.name)+'</h2>';
  var close = '<button type="button" class="btn" id="cmpClose">ปิดการเปรียบเทียบ</button></div>';
  if(a.length<2 || b.length<2) return head+'<p class="note">ข้อมูลย้อนหลังยังไม่พอจะเปรียบเทียบ</p>'+close;
  var W=340,H=150,pl=8,pr=8,pt=8,pb=22, all = a.concat(b), maxP = Math.max(110, Math.max.apply(null, all.map(function(p){return p.pct_of_bank;}))*1.05);
  var t0 = Math.min(Date.parse(a[0].ts), Date.parse(b[0].ts)), t1 = Math.max(Date.parse(a[a.length-1].ts), Date.parse(b[b.length-1].ts)), span = Math.max(t1-t0,1);
  function X(t){ return pl+(Date.parse(t)-t0)/span*(W-pl-pr); }
  function Y(v){ return pt+(maxP-v)/maxP*(H-pt-pb); }
  function line(pts, col, dash){ return '<path d="'+pts.map(function(p,i){return (i?"L":"M")+X(p.ts).toFixed(1)+" "+Y(p.pct_of_bank).toFixed(1);}).join(" ")+'" fill="none" stroke="'+col+'" stroke-width="2.2" stroke-linejoin="round"'+(dash?' stroke-dasharray="6 3"':'')+'/>'; }
  var svg = '<svg class="chart" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="กราฟเทียบเปอร์เซ็นต์ของตลิ่งสองสถานี">'+
    '<line x1="'+pl+'" x2="'+(W-pr)+'" y1="'+Y(100)+'" y2="'+Y(100)+'" stroke="var(--alert)" stroke-dasharray="4 3"/><text x="'+(pl+3)+'" y="'+(Y(100)-3)+'" font-size="12" fill="var(--muted)">ตลิ่ง 100%</text>'+
    line(a,"var(--ink)",false)+line(b,"var(--accent)",true)+
    '<text x="'+pl+'" y="'+(H-6)+'" font-size="12" fill="var(--muted)">'+esc(fmtTs(new Date(t0).toISOString()))+'</text><text x="'+(W-pr)+'" y="'+(H-6)+'" font-size="12" fill="var(--muted)" text-anchor="end">'+esc(fmtTs(new Date(t1).toISOString()))+'</text></svg>';
  function peak(pts){ var m = pts[0]; pts.forEach(function(p){ if(p.pct_of_bank>m.pct_of_bank) m = p; }); return m; }
  function range(pts){ var v = pts.map(function(p){return p.pct_of_bank;}); return Math.max.apply(null,v)-Math.min.apply(null,v); }
  function inside(pts){ var m = peak(pts); return m!==pts[0] && m!==pts[pts.length-1]; }   // a maximum at either end of the window is not a real peak
  var note = "";
  if(range(a)>=5 && range(b)>=5 && inside(a) && inside(b)){
    var pa = peak(a), pb2 = peak(b), dh = (Date.parse(pb2.ts)-Date.parse(pa.ts))/3600000;
    note = '<p class="note">จุดสูงสุดในช่วงนี้: '+esc(s.name)+' '+esc(fmtTs(pa.ts))+' · '+esc(c.other.name)+' '+esc(fmtTs(pb2.ts))+
      (Math.abs(dh)>=1 ? ' (ห่างกันประมาณ '+Math.round(Math.abs(dh))+' ชม.)' : ' (ใกล้เคียงกัน)')+' ดูเป็นแนวทางเท่านั้น น้ำไหลเร็วช้าขึ้นกับหลายปัจจัย</p>';
  }
  return head+'<div class="lg"><span><i style="background:var(--ink)"></i>'+esc(s.name)+'</span><span><i style="background:var(--accent)"></i>'+esc(c.other.name)+'</span></div>'+svg+note+close;
}
function drawDetail(s, rs, rel){
  curRs = rs; curRel = rel;
  var h='<div class="head"><div><h2 style="margin:0;font-size:18px">'+esc(s.name)+'</h2><div class="note" style="margin:0">'+esc(s.province||"")+(s.source?" · "+esc(s.source):"")+'</div></div><span class="badge '+s.status+'">'+esc(statusText(s))+'</span></div>';
  h+='<div class="big">'+pctText(s)+' <small>ของตลิ่ง</small></div>'+(s.pct_of_bank!=null ? '<div class="note" style="margin:2px 0 6px">'+pctHint(s.pct_of_bank)+' (100% = น้ำเสมอตลิ่ง)</div>' : '')+gaugeHtml(s);
  h+='<details class="lvl"><summary>ตัวเลขระดับน้ำ (เมตร)</summary><p class="note" style="margin:0 0 6px">ระดับน้ำ '+(s.water_level==null?"-":s.water_level.toFixed(2))+' · ตลิ่ง '+(s.bank_level==null?"-":s.bank_level.toFixed(2))+' · ท้องน้ำ '+(s.ground_level==null?"-":s.ground_level.toFixed(2))+'<br>วัดเทียบระดับน้ำทะเลปานกลาง (ม.รทก.) ตลิ่ง 100% คือน้ำเสมอระดับตลิ่งนี้</p></details>';
  if(s.trend) h+='<p style="margin:6px 0 0">'+trendHtml(s)+'</p>';
  h+='<p class="note">อัปเดตล่าสุด '+esc(fmtTs(s.ts))+' ('+esc(fmtAge(s.age_min))+')</p>';
  h+=twinHtml(s);
  if(s.advice) h+='<div class="advice">'+esc(s.advice)+'</div>';
  h+='<div id="shBox">'+shelterBox(s)+'</div>';
  if(s.status==="unknown") h+='<div class="warn">สถานีนี้ไม่มีค่าตลิ่ง/ท้องน้ำให้คำนวณ จึงไม่แสดงสถานะ</div>';
  if(s.stale && s.status!=="unknown") h+='<div class="warn" role="alert">ข้อมูลของสถานีนี้ไม่ได้อัปเดตเกิน 2 ชั่วโมง ตัวเลขอาจไม่ตรงกับสถานการณ์จริง</div>';
  h+='<h2 style="margin-top:14px">ย้อนหลัง 7 วัน</h2>'+(rs?chartSvg(rs):'<div class="skel" style="height:120px"></div>')+compareHtml(s, rs);
  h+=relHtml(rel)+shareBar(s);
  var d=$("detail"); d.hidden=false; d.innerHTML=h;
}
function select(id, fly){
  var prev = sel; sel = id; var s = byId[id]; if(!s) return;
  cmpState = null;
  if(prev && markers[prev]){ markers[prev].setIcon(icon((byId[prev]),false)); markers[prev].setZIndexOffset(Z[(byId[prev]).status]||0); }
  if(markers[id]){ markers[id].setIcon(icon((s),true)); markers[id].setZIndexOffset(1000); }
  if(fly) flyToVisible([s.lat,s.lng], Math.max(map.getZoom(),CLUSTER_OFF_ZOOM+1));
  else if(markers[id] && layer.getVisibleParent(markers[id])!==markers[id]) layer.zoomToShowLayer(markers[id]);
  drawDetail(s,null); loadStationShelter(s);
  revealInSheet($("detail"));
  try{ var u = new URL(location.href); u.searchParams.set("station",id); history.replaceState(null,"",u); }catch(e){}
  var rel = null;
  getJSON("/stations/"+encodeURIComponent(id)+"/readings?days=7").then(function(rs){ if(sel===id) drawDetail(s,rs,rel); return rs; })
    .catch(function(){ if(sel===id) drawDetail(s,[],rel); return []; })
    .then(function(rs){
      return getJSON("/stations/"+encodeURIComponent(id)+"/related").then(function(r){ rel = r; if(sel===id) drawDetail(s,rs,rel); }).catch(function(){});
    });
}

/* ---------- my location, province ---------- */
function setOrigin(o){
  origin = o;
  if(originLayer){ map.removeLayer(originLayer); originLayer=null; }
  if(o){
    originLayer = (o.kind==="home")
      ? L.marker([o.lat,o.lng],{icon:L.divIcon({className:"",iconSize:[30,30],iconAnchor:[15,15],html:'<svg width="30" height="30" viewBox="0 0 26 26" aria-hidden="true"><path d="M2 13 L13 2 L24 13 L21 13 L21 23 L5 23 L5 13 Z" fill="#0b6e8f" stroke="#fff" stroke-width="2"/></svg>'}),title:"บ้านของฉัน"}).addTo(map)
      : L.circleMarker([o.lat,o.lng],{radius:8,color:"#fff",weight:2,fillColor:"#0b6e8f",fillOpacity:1}).addTo(map);
  }
  $("btnClear").hidden = !o;
  if((!o || o.kind!=="area") && $("tam").value!=="") $("tam").value = "";   // another kind of place replaces the picked subdistrict
  say(!o ? "" : (o.kind==="home" ? "ใช้ตำแหน่งบ้านที่ปักไว้ (ใช้เฉพาะตอนเปิดหน้านี้ ไม่ถูกเก็บไว้)" : o.kind==="area" ? "ตรวจพื้นที่ "+o.label+" (พิกัดกลางพื้นที่โดยประมาณ)" : "ใช้ตำแหน่งปัจจุบันของคุณ"));
  showNearShelters(o); refreshRain();
  if(!o){ $("results").hidden = true; drawNear(null); return; }
  getJSON("/stations/nearby?lat="+o.lat+"&lng="+o.lng+"&limit=3").then(function(list){
    list.forEach(function(s){ byId[s.id]=Object.assign(byId[s.id]||{},s); });
    if(province && o.kind!=="area" && list.some(function(s){ return s.province!==province; })){  // don't hide the stations near the user
      province = ""; $("prov").value = ""; redraw(); updateRepLink();
    }
    showResults("สถานีใกล้ที่สุด 3 แห่ง", list, function(s){return s.distance_km+" กม.";});
    var pick = list[0];
    if(o.kind==="area" && province){   // a picked subdistrict: open the nearest station of the chosen province, not one across the border
      var best = null, bd = 1e9;
      stations.forEach(function(s){ if(s.province!==province) return; var d = kmBetween(o,s); if(d<bd){ bd = d; best = s; } });
      if(best) pick = best;
    }
    if(pick){ select(pick.id,false); drawNear(o, pick); $("sheetBody").scrollTo({top:0, behavior:"smooth"}); }
    var b = L.latLngBounds([[o.lat,o.lng]].concat(list.map(function(s){return [s.lat,s.lng];})));
    map.fitBounds(b,Object.assign({maxZoom:12},fitPad()));
  }).catch(function(){ showErr("เรียกข้อมูลสถานีใกล้เคียงไม่ได้"); });
}
function drawNear(o, s0){
  var el = $("nearCard");
  if(!o || !s0){ el.hidden = true; el.innerHTML = ""; return; }
  var s = byId[s0.id] || s0, d = kmBetween(o, s);
  var who = o.kind==="home" ? "บ้านของคุณ" : o.kind==="area" ? (o.label || "พื้นที่ที่เลือก") : "ตำแหน่งของคุณ";
  var col = COLOR[s.status] || COLOR.unknown, t = TREND[s.trend];
  var line = s.pct_of_bank!=null
    ? '<div class="nm">น้ำ <b>'+pctText(s)+'</b> ของตลิ่ง ('+pctHint(s.pct_of_bank)+')'+(t && s.trend!=="steady" ? ' · '+t[0]+' '+t[1] : s.trend==="steady" ? ' · ทรงตัว' : '')+'</div>'
    : '<div class="nm">ยังไม่มีเกณฑ์เทียบสำหรับสถานีนี้</div>';
  el.innerHTML = '<div class="eyebrow">สถานีที่ใกล้'+(o.kind==="area" ? ' ' : '')+esc(who)+(o.kind==="area" ? ' ' : '')+'ที่สุด</div>'+
    '<button type="button" class="nearbtn" data-id="'+esc(s.id)+'"><div class="nh"><span>'+esc(s.name)+'</span><span class="badge '+s.status+'">'+esc(statusText(s))+'</span></div>'+
    '<div class="nn">ห่างประมาณ '+(d<10 ? d.toFixed(1) : Math.round(d))+' กม.'+(s.province ? ' · จ.'+esc(s.province) : '')+'</div>'+line+
    (s.stale ? '<div class="nn" style="color:var(--watch-ink)">⚠ สถานีนี้ข้อมูลไม่อัปเดต ใช้ประกอบอย่างระวัง</div>' : '')+
    '<div class="nn">แตะเพื่อดูรายละเอียดและกราฟ →</div></button>'+(o.kind==="area" ? "" : '<div class="elev" id="elevBox"></div>');
  el.hidden = false;
  if(o.kind!=="area") loadElevation(o, s, d);
}
/* How high the place is (Open-Meteo elevation, a ~90 m terrain model) next to the nearest station's current water level.
   A rough comparison of two numbers: it ignores the slope of the river, levees and rain that pools locally. */
var elevCache = {};
function loadElevation(o, s, d){
  var key = o.lat.toFixed(3)+","+o.lng.toFixed(3), box = $("elevBox"); if(!box) return;
  function show(e){
    var b = $("elevBox"); if(!b || origin!==o) return;
    var h = '<b>ความสูงของพื้นที่ตรงนี้ ≈ '+e.toFixed(1)+' ม.รทก.</b> <small>(ประมาณจากแผนที่ความสูง ละเอียดราว 90 ม.)</small>';
    if(s.water_level!=null && !s.stale && d<=15){
      var diff = e - s.water_level, a = Math.abs(diff).toFixed(1), cls = diff<0.5 ? " warnv" : "";
      h += '<div class="'+cls.trim()+'">ระดับน้ำที่สถานี '+esc(s.name)+' (ห่าง '+(d<10 ? d.toFixed(1) : Math.round(d))+' กม.) ตอนนี้ '+s.water_level.toFixed(2)+' ม.รทก. → พื้นที่ตรงนี้ '+
           (diff>=0.5 ? 'สูงกว่าระดับน้ำที่สถานีประมาณ '+a+' ม.' : diff>-0.5 ? 'ใกล้เคียงระดับน้ำที่สถานี (ต่างกันไม่ถึง 0.5 ม.)' : 'ต่ำกว่าระดับน้ำที่สถานีประมาณ '+a+' ม.')+'</div>';
    }
    h += '<div class="note" style="margin:4px 0 0">เป็นการเทียบตัวเลขอย่างหยาบ ไม่รวมความลาดของแม่น้ำ คันกั้นน้ำ หรือน้ำฝนที่ขังในพื้นที่ จึงไม่ได้บอกว่าจะท่วมหรือไม่ท่วม</div>';
    b.innerHTML = h;
  }
  if(elevCache[key]!=null){ show(elevCache[key]); return; }
  fetch("https://api.open-meteo.com/v1/elevation?latitude="+o.lat.toFixed(5)+"&longitude="+o.lng.toFixed(5))
    .then(function(r){ if(!r.ok) throw new Error("HTTP "+r.status); return r.json(); })
    .then(function(j){ var e = j && j.elevation && j.elevation[0]; if(typeof e==="number"){ elevCache[key] = e; show(e); } })
    .catch(function(){});   // no elevation: the card simply stays without this block
}
$("nearCard").addEventListener("click",function(e){
  var b = e.target.closest ? e.target.closest(".nearbtn") : null; if(b) select(b.getAttribute("data-id"), true);
});
function updateRepLink(){
  $("repLink").href = "report.html" + (province ? "?province="+encodeURIComponent(province) : "");
}
function drawProvinceSelect(){
  var names = {}; stations.forEach(function(s){ if(s.province) names[s.province]=(names[s.province]||0)+1; });
  var list = Object.keys(names).sort(function(a,b){ return a.localeCompare(b,"th"); });
  if(province && !names[province]) province = "";  // saved province has no stations any more
  $("prov").innerHTML = '<option value="">ทั้งประเทศ ('+stations.length+')</option>' +
    list.map(function(n){ return '<option value="'+esc(n)+'">'+esc(n)+'</option>'; }).join("");
  $("prov").value = province; updateRepLink(); syncAreaPills();
}
function showProvince(fit){
  var list = stations.filter(inProv);
  if(fit && list.length) map.fitBounds(L.latLngBounds(list.map(function(s){return [s.lat,s.lng];})),Object.assign({maxZoom:11},fitPad()));
  if(!province){ if(!origin) $("results").hidden = true; return; }
  drawProvList(list);
}
var resSort = "pct";   // "pct" highest % of bank, "fast" rising fastest in 3 h, "near" closest to the pinned place
var SORTS = [["pct","สูงสุด"],["fast","น้ำขึ้นเร็ว"],["near","ใกล้ฉัน"]];
function drawProvList(list){
  if(resSort==="near" && !origin) resSort = "pct";
  var by = {
    pct:  function(a,b){ return (b.pct_of_bank==null?-1:b.pct_of_bank)-(a.pct_of_bank==null?-1:a.pct_of_bank); },
    fast: function(a,b){ return (b.rise_3h_m==null?-9:b.rise_3h_m)-(a.rise_3h_m==null?-9:a.rise_3h_m); },
    near: function(a,b){ return kmBetween(origin,a)-kmBetween(origin,b); }
  };
  list = list.slice().sort(by[resSort]);
  var name = resSort==="fast" ? "น้ำขึ้นเร็วที่สุด" : resSort==="near" ? "ใกล้คุณที่สุด" : "น้ำสูงสุด";
  var extra = resSort==="fast" ? function(s){ return s.rise_3h_m==null ? "ไม่มีข้อมูล 3 ชม." : (s.rise_3h_m>=0 ? "+" : "")+Math.round(s.rise_3h_m*100)+" ซม. ใน 3 ชม."; }
            : resSort==="near" ? function(s){ return kmBetween(origin,s).toFixed(1)+" กม."; } : null;
  showResults(name+"ใน จ."+province+(list.length>10 ? " (10 จาก "+list.length+" สถานี)" : " ("+list.length+" สถานี)"), list.slice(0,10), extra);
  var bar = $("resSort");
  bar.innerHTML = SORTS.map(function(x){
    var off = x[0]==="near" && !origin;
    return '<button type="button" class="btn" data-sort="'+x[0]+'" aria-pressed="'+(resSort===x[0])+'"'+(off?' disabled title="ปักตำแหน่งบ้านหรือกดหาตำแหน่งของฉันก่อน"':'')+'>'+x[1]+'</button>';
  }).join("");
  bar.hidden = false;
}
$("resSort").addEventListener("click", function(e){
  var b = e.target.closest ? e.target.closest("button[data-sort]") : null; if(!b || b.disabled) return;
  resSort = b.getAttribute("data-sort"); if(province) drawProvList(stations.filter(inProv));
});
/* "data may be late" bar: the server tells us when ingest keeps failing or the source stopped sending new readings */
var DELAY_READING_MIN = 180;
function checkHealth(){
  getJSON("/health").then(function(h){
    var bar = $("delayBar"), msg = "";
    if(h.ingest_ok===false){
      msg = h.last_ingest_age_min==null ? "ระบบยังดึงข้อมูลจาก ThaiWater ไม่สำเร็จ"
          : "ระบบดึงข้อมูลจาก ThaiWater ไม่ได้มาแล้ว "+fmtAge(h.last_ingest_age_min).replace(/\s?ที่แล้ว$/,"");
    } else if(h.latest_reading_age_min!=null && h.latest_reading_age_min>DELAY_READING_MIN){
      msg = "ต้นทาง (ThaiWater) ยังไม่ส่งค่าใหม่มา "+fmtAge(h.latest_reading_age_min).replace(/\s?ที่แล้ว$/,"");
    }
    if(!msg){ bar.hidden = true; bar.innerHTML = ""; return; }
    bar.innerHTML = "<b>ข้อมูลอาจล่าช้า</b>"+esc(msg)+" ตัวเลขที่เห็นอาจไม่ใช่ค่าปัจจุบัน ถ้าต้องตัดสินใจ โปรดตรวจกับ ปภ. โทร 1784";
    bar.hidden = false;
  }).catch(function(){});   // no health answer: the normal error bar below already covers a dead API
}
/* The last good station list is kept on this device, so a slow or missing connection still opens on something.
   It is always shown with a bar saying how old it is; fresh data replaces it as soon as it arrives. */
var LAST_KEY = "/__last-stations", LAST_MAX_H = 12;
function savedStations(){
  if(!window.caches) return Promise.resolve(null);
  return caches.open("nkb-data").then(function(c){ return c.match(LAST_KEY); }).then(function(r){
    return r ? r.json().then(function(list){ return {at:+r.headers.get("x-saved-at"), list:list}; }) : null;
  }).catch(function(){ return null; });
}
function saveStations(list){
  if(!window.caches) return;
  try{ caches.open("nkb-data").then(function(c){
    return c.put(LAST_KEY, new Response(JSON.stringify(list), {headers:{"content-type":"application/json","x-saved-at":String(Date.now())}}));
  }).catch(function(){}); }catch(e){}
}
function savedAgeText(at){
  var m = Math.max(0, Math.round((Date.now()-at)/60000));
  return new Date(at).toLocaleTimeString("th-TH",{hour:"2-digit",minute:"2-digit"})+" น. ("+(m<60 ? m+" นาที" : Math.round(m/60)+" ชั่วโมง")+"ก่อน)";
}
function showCacheBar(at, offline){
  var bar = $("cacheBar");
  bar.innerHTML = "<b>"+(offline ? "เชื่อมต่อไม่ได้ แสดงข้อมูลที่บันทึกไว้" : "กำลังอัปเดต แสดงข้อมูลที่บันทึกไว้ในเครื่องก่อน")+"</b>"+
    "บันทึกเมื่อ "+esc(savedAgeText(at))+" สถานการณ์จริงอาจต่างจากนี้ "+(offline ? "ตรวจกับ ปภ. สายด่วน 1784" : "ตัวเลขใหม่จะมาแทนที่เมื่อโหลดเสร็จ");
  bar.hidden = false;
}
function applyStations(list){
  stations = list; byId = {}; list.forEach(function(s){byId[s.id]=s;});
  showErr(""); drawProvinceSelect(); redraw();
  if(sel && byId[sel]) select(sel,false);
}
var gotFresh = false, shownSaved = null;
function showSaved(sv, offline){      // put the saved list on screen, once, labelled with its age; false when there is nothing usable
  if(shownSaved) { showCacheBar(shownSaved, offline); return true; }
  if(gotFresh || !sv || !sv.list.length || Date.now()-sv.at > LAST_MAX_H*3600000) return false;
  var mins = Math.round((Date.now()-sv.at)/60000);
  sv.list.forEach(function(s){ if(s.age_min!=null){ s.age_min += mins; s.stale = s.age_min > 120; } });   // its readings are older now by the time since saving
  shownSaved = sv.at; applyStations(sv.list); showCacheBar(sv.at, offline);
  return true;
}
function load(){
  checkHealth();
  var fresh = getJSON("/stations");
  var saved = stations.length ? Promise.resolve(null) : savedStations();   // first load only
  saved.then(function(sv){ if(sv) showSaved(sv, false); });                // while the real answer is on its way
  return fresh.then(function(list){
    gotFresh = true; shownSaved = null; $("cacheBar").hidden = true;
    applyStations(list); saveStations(list);
  }).catch(function(e){
    return saved.then(function(sv){
      if(showSaved(sv, true)){ showErr(""); return; }                      // no connection, but there is something to show
      showErr("เชื่อมต่อ API ที่ "+API+" ไม่ได้ ("+e.message+") ตรวจว่า uvicorn ยังรันอยู่ แล้วโหลดหน้านี้ใหม่");
    });
  });
}

/* ---------- events ---------- */
$("chips").addEventListener("click",function(e){
  var b = e.target.closest ? e.target.closest(".chip") : null; if(!b) return;
  var f = b.getAttribute("data-f"); filter = (filter===f) ? null : f;
  redraw();
});
function deselect(){   // forget the open station and take ?station= out of the address so the page and the link agree
  var prev = sel; sel = null;
  if(prev && markers[prev] && byId[prev]){ markers[prev].setIcon(icon((byId[prev]),false)); markers[prev].setZIndexOffset(Z[(byId[prev]).status]||0); }
  $("detail").hidden = true;
  try{ var u = new URL(location.href); u.searchParams.delete("station"); history.replaceState(null,"",u); }catch(e){}
}
$("prov").addEventListener("change",function(e){
  province = e.target.value;
  if(sel && province && byId[sel] && byId[sel].province!==province) deselect();   // the open station is outside the newly chosen province
  if(origin && origin.kind==="area") setOrigin(null);
  redraw(); showProvince(true); updateRepLink(); syncAreaPills();
});
var geoSeq = 0;
function geocode(q){
  var my = ++geoSeq;
  $("results").hidden = false; $("resTitle").textContent = 'ค้นหาสถานที่ "'+q+'"';
  $("resList").innerHTML = '<li class="note">กำลังค้นหา…</li>';
  if(isMobile() && sheetState==="peek") setSheet("half");
  fetch("https://nominatim.openstreetmap.org/search?format=jsonv2&limit=6&countrycodes=th&accept-language=th&q="+encodeURIComponent(q))
    .then(function(r){ if(!r.ok) throw new Error("HTTP "+r.status); return r.json(); })
    .then(function(list){
      if(my!==geoSeq) return;
      $("resTitle").textContent = 'สถานที่ที่ตรงกับ "'+q+'" ('+list.length+')';
      $("resList").innerHTML = list.length ? list.map(function(p){
        var name = p.display_name||"";
        return '<li><button class="row" type="button" data-lat="'+esc(p.lat)+'" data-lng="'+esc(p.lon)+'"><span>'+esc(name.split(",")[0])+'<small>'+esc(name)+'</small></span><span aria-hidden="true">→</span></button></li>';
      }).join("") : '<li class="note">ไม่พบสถานที่ ลองพิมพ์ชื่อตำบล อำเภอ หรือจังหวัด</li>';
    }).catch(function(){ if(my===geoSeq) $("resList").innerHTML = '<li class="note">ค้นหาสถานที่ไม่ได้ในขณะนี้ ลองใหม่อีกครั้ง</li>'; });
}
function goToPlace(ll){
  var done = false, go = function(){ if(done) return; done = true; probe(ll); };
  map.once("moveend", go); setTimeout(go, 1800); flyToVisible(ll, 12);
}
/* ---------- search-box type-ahead (/api/suggest: our own subdistricts, districts, provinces, stations, shelters) ---------- */
function attachSuggest(input, list, onPick){
  var items = [], active = -1, timer = null, seq = 0;
  function close(){ list.hidden = true; list.innerHTML = ""; items = []; active = -1; input.setAttribute("aria-expanded","false"); input.removeAttribute("aria-activedescendant"); }
  function mark(){ [].forEach.call(list.children, function(li,i){ li.setAttribute("aria-selected", String(i===active)); }); if(active>=0){ input.setAttribute("aria-activedescendant", list.children[active].id); list.children[active].scrollIntoView({block:"nearest"}); } }
  function pick(i){ var it = items[i]; if(!it) return; close(); onPick(it); }
  input.addEventListener("input", function(){
    clearTimeout(timer); var q = input.value.trim();
    if(q.length<2){ seq++; close(); return; }
    var my = ++seq;
    timer = setTimeout(function(){
      getJSON("/api/suggest?v=2&q="+encodeURIComponent(q)).then(function(l){
        if(my!==seq || document.activeElement!==input) return;
        items = l; active = -1;
        if(!l.length){ close(); return; }
        list.innerHTML = l.map(function(it,i){ return '<li role="option" id="'+list.id+'-'+i+'" data-i="'+i+'" aria-selected="false"><span>'+esc(it.label)+'</span><small>'+esc(it.type)+'</small></li>'; }).join("");
        list.hidden = false; input.setAttribute("aria-expanded","true");
      }).catch(function(){});
    }, 200);
  });
  input.addEventListener("keydown", function(e){      // capture phase: a highlighted suggestion wins over the Enter = map search below
    if(list.hidden) return;
    if(e.key==="ArrowDown"){ e.preventDefault(); active = (active+1)%items.length; mark(); }
    else if(e.key==="ArrowUp"){ e.preventDefault(); active = (active-1+items.length)%items.length; mark(); }
    else if(e.key==="Enter" && active>=0){ e.preventDefault(); e.stopImmediatePropagation(); pick(active); }
    else if(e.key==="Escape"){ e.stopPropagation(); close(); }
  }, true);
  list.addEventListener("mousedown", function(e){ e.preventDefault(); });   // keep focus in the input while tapping the list
  list.addEventListener("click", function(e){ var li = e.target.closest ? e.target.closest("li") : null; if(li) pick(+li.getAttribute("data-i")); });   // pick on click so the tap cannot fall through to the map
  input.addEventListener("blur", function(){ setTimeout(close, 150); });
  $("clearQ").addEventListener("click", close);
}
function fireChange(sel, val){ sel.value = val; sel.dispatchEvent(new Event("change")); }
function whenAreas(name, cb){      // the subdistrict list of a province loads after the province is chosen
  var tries = 0;
  (function wait(){ if(areaCur && areaCur.province===name){ cb(); return; } if(++tries<50) setTimeout(wait, 100); })();
}
function pickPlace(it){
  $("q").value = it.name; $("clearQ").hidden = false; $("q").blur();
  if(!origin) $("results").hidden = true;
  if(it.type==="สถานี" && it.id && byId[it.id]){ select(it.id, true); return; }
  if(it.type==="ศูนย์พักพิง"){
    flyToVisible([it.lat,it.lng], 15);
    L.popup().setLatLng([it.lat,it.lng]).setContent('<div class="pp"><b>'+esc(it.name)+'</b><div class="note">ศูนย์พักพิงชั่วคราว (ข้อมูล ปภ.) โทรสอบถามก่อนเดินทาง</div></div>').openOn(map);
    return;
  }
  if(!it.province){ goToPlace(L.latLng(it.lat, it.lng)); return; }
  if(province!==it.province) fireChange($("prov"), it.province);
  if($("prov").value!==it.province){ goToPlace(L.latLng(it.lat, it.lng)); return; }   // province without stations: just fly there
  if(it.type==="จังหวัด") return;
  whenAreas(it.province, function(){
    var di = areaCur.districts.findIndex(function(d){ return d.name===it.district; }); if(di<0) return;
    fireChange($("dist"), String(di));
    if(it.type==="ตำบล"){
      var ti = areaCur.districts[di].tambons.findIndex(function(t){ return t.name===it.tambon; });
      if(ti>=0) fireChange($("tam"), String(ti));
    }
  });
}
attachSuggest($("q"), $("sugQ"), pickPlace);
$("q").addEventListener("input",function(e){
  var q = e.target.value.trim();
  $("clearQ").hidden = !e.target.value;
  if(!q){ if(!origin) $("results").hidden = true; return; }
  var hits = stations.filter(function(s){ return (s.name+" "+(s.province||"")).indexOf(q)>-1; });
  hits.sort(function(a,b){return (b.pct_of_bank||-1)-(a.pct_of_bank||-1);});
  showResults('ผลค้นหา "'+q+'" ('+hits.length+' สถานี)', hits.slice(0,8));
  $("resList").insertAdjacentHTML("beforeend",'<li><button class="row" type="button" data-geo="'+esc(q)+'"><span>ค้นหา "'+esc(q)+'" เป็นชื่อสถานที่บนแผนที่<small>ใช้ OpenStreetMap หรือกด Enter</small></span><span aria-hidden="true">→</span></button></li>');
  if(isMobile() && sheetState==="peek") setSheet("half");
});
$("q").addEventListener("keydown",function(e){
  var q = e.target.value.trim();
  if(e.key==="Enter" && q.length>=2){ e.preventDefault(); geocode(q); e.target.blur(); }
});
$("clearQ").addEventListener("click",function(){ var q = $("q"); q.value = ""; $("clearQ").hidden = true; if(!origin) $("results").hidden = true; q.focus(); });
$("detail").addEventListener("click",function(e){
  var cb = e.target.closest ? e.target.closest(".cmpbtn") : null;
  if(cb){
    var oid = cb.getAttribute("data-cmp"), me = sel; cb.disabled = true;
    getJSON("/stations/"+encodeURIComponent(oid)+"/readings?days=7").then(function(ro){
      if(sel!==me) return; cmpState = {of:me, other:byId[oid]||{id:oid,name:oid}, rs:ro}; drawDetail(byId[me], curRs, curRel);
      var box = document.querySelector(".cmp"); if(box && box.scrollIntoView) box.scrollIntoView({block:"nearest", behavior:"smooth"});
    }).catch(function(){ cb.disabled = false; toast("โหลดข้อมูลเปรียบเทียบไม่ได้"); });
    return;
  }
  if(e.target.id==="cmpClose"){ cmpState = null; drawDetail(byId[sel], curRs, curRel); return; }
  var r = e.target.closest(".row"); if(r){ select(r.getAttribute("data-id"),true); return; }
  if(e.target.id==="btnShare"){
    var u = new URL(location.href); u.searchParams.set("station",sel); u.searchParams.delete("api");
    var url = u.toString();
    var done = function(t){ if($("shareMsg")) $("shareMsg").textContent = t; };
    if(navigator.share){ navigator.share({title:"น้ำใกล้บ้านฉัน – "+byId[sel].name,url:url}).catch(function(){}); }
    else if(navigator.clipboard){ navigator.clipboard.writeText(url).then(function(){done("คัดลอกลิงก์แล้ว");},function(){done(url);}); }
    else done(url);
  }
});
$("resList").addEventListener("click",function(e){
  var b = e.target.closest(".row"); if(!b) return;
  if(b.hasAttribute("data-geo")) return geocode(b.getAttribute("data-geo"));
  if(b.hasAttribute("data-lat")) return goToPlace(L.latLng(+b.getAttribute("data-lat"), +b.getAttribute("data-lng")));
  select(b.getAttribute("data-id"),true);
});
function closeLayers(){ $("layers").hidden = true; $("btnLayers").setAttribute("aria-expanded","false"); }
$("btnLayers").addEventListener("click",function(e){
  e.stopPropagation();
  var open = $("layers").hidden; $("layers").hidden = !open; this.setAttribute("aria-expanded", String(open));
});
document.addEventListener("click",function(e){ if(!$("layers").hidden && !$("layers").contains(e.target)) closeLayers(); });
document.addEventListener("keydown",function(e){ if(e.key==="Escape") closeLayers(); });
$("btnPin").addEventListener("click",function(){
  pinMode=!pinMode; this.setAttribute("aria-pressed",String(pinMode)); this.textContent = pinMode?"ยกเลิกการปักหมุด":"ปักหมุดบ้านฉัน";
  map.getContainer().style.cursor = pinMode?"crosshair":"";
  if(pinMode){ closeLayers(); say("แตะบนแผนที่ตรงจุดที่ตั้งบ้านของคุณ", true); } else say("");
});
map.on("click",function(e){
  if(rptPicking){ rptPicking=false; map.getContainer().style.cursor=""; toast(""); rptSetPoint(e.latlng); rptOpen(); return; }
  if(!pinMode){ probe(e.latlng); return; }
  pinMode=false; $("btnPin").setAttribute("aria-pressed","false"); $("btnPin").textContent="ย้ายหมุดบ้าน"; map.getContainer().style.cursor="";
  setOrigin({lat:e.latlng.lat,lng:e.latlng.lng,kind:"home"});
});
$("btnClear").addEventListener("click",function(){ $("btnPin").textContent="ปักหมุดบ้านฉัน"; setOrigin(null); closeLayers(); });
$("btnMe").addEventListener("click",function(){
  if(!navigator.geolocation){ say("เบราว์เซอร์นี้ไม่รองรับการระบุตำแหน่ง ลองปักหมุดบ้านแทน"); return; }
  say("กำลังหาตำแหน่งของคุณ…", true);
  navigator.geolocation.getCurrentPosition(function(p){ setOrigin({lat:p.coords.latitude,lng:p.coords.longitude,kind:"me"}); },
    function(){ say("ใช้ตำแหน่งไม่ได้ กรุณาอนุญาตการเข้าถึงตำแหน่ง หรือปักหมุดบ้านบนแผนที่แทน"); },
    {timeout:10000,maximumAge:300000});
});


/* ---------- rain forecast (Open-Meteo, next 24 h + 3 days) ---------- */
var HOUSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#fff" d="M12 3 2 12h3v8h5v-5h4v5h5v-8h3z"/></svg>';
var rainCache = {}, rainTimer = null, rainSeq = 0;
function rainPoint(){
  if(origin) return {lat:origin.lat, lng:origin.lng, place:true, name: origin.label || (origin.kind==="home" ? "บ้านของฉัน" : "ตำแหน่งของคุณ")};
  if(!province) return null;
  var l = stations.filter(function(s){ return s.province===province; }); if(!l.length) return null;
  return {lat:l.reduce(function(a,s){return a+s.lat;},0)/l.length, lng:l.reduce(function(a,s){return a+s.lng;},0)/l.length, province:province, name:"จ."+province};
}
function rainClass(mm){ return mm<0.1 ? "ไม่มีฝน" : mm<=10 ? "ฝนเล็กน้อย" : mm<=35 ? "ฝนปานกลาง" : mm<=90 ? "ฝนหนัก" : "ฝนหนักมาก"; }
function refreshRain(){ clearTimeout(rainTimer); rainTimer = setTimeout(loadRain, 400); }
function loadRain(){
  var pt = rainPoint(), card = $("rainCard"), my = ++rainSeq;
  if(!pt){ card.hidden = true; return; }
  var key = pt.lat.toFixed(1)+","+pt.lng.toFixed(1), hit = rainCache[key];
  if(hit && Date.now()-hit.t < 30*60000){ drawRain(hit.j, pt); return; }
  fetch("https://api.open-meteo.com/v1/forecast?latitude="+pt.lat.toFixed(3)+"&longitude="+pt.lng.toFixed(3)+
        "&hourly=precipitation,precipitation_probability&daily=precipitation_sum,precipitation_probability_max&timezone=Asia%2FBangkok&forecast_days=3&past_hours=24")
    .then(function(r){ if(!r.ok) throw new Error("HTTP "+r.status); return r.json(); })
    .then(function(j){ if(my!==rainSeq) return; rainCache[key] = {t:Date.now(), j:j}; drawRain(j, pt); })
    .catch(function(e){ console.warn("rain forecast:", e); if(my===rainSeq) card.hidden = true; });
}
function drawRain(j, pt){
  var card = $("rainCard"), H = j.hourly, now = new Date().toLocaleString("sv-SE",{timeZone:"Asia/Bangkok"}).slice(0,13).replace(" ","T")+":00";
  var i0 = Math.max(0, H.time.indexOf(now)), mm = H.precipitation.slice(i0, i0+24), pr = H.precipitation_probability.slice(i0, i0+24);
  if(mm.length<6){ card.hidden = true; return; }
  var past = i0>=24 ? H.precipitation.slice(i0-24, i0).reduce(function(a,b){return a+(b||0);},0) : null;   // rain that already fell (model analysis, not a gauge)
  var total = mm.reduce(function(a,b){return a+(b||0);},0), pmax = Math.max.apply(null, pr.map(function(x){return x||0;}));
  var peak = mm.indexOf(Math.max.apply(null, mm)), mx = Math.max(2, Math.max.apply(null, mm));
  var W = 300, Hh = 64, bw = W/mm.length, bars = mm.map(function(v,i){
    var h = Math.max(v>0?2:0, (v||0)/mx*(Hh-14));
    return '<rect x="'+(i*bw+1).toFixed(1)+'" y="'+(Hh-14-h).toFixed(1)+'" width="'+(bw-2).toFixed(1)+'" height="'+h.toFixed(1)+'" rx="2" fill="var(--accent)" opacity="'+(pr[i]>=60?1:.55)+'"><title>'+H.time[i0+i].slice(11,16)+' น. '+(v||0).toFixed(1)+' มม. โอกาสฝน '+(pr[i]||0)+'%</title></rect>';
  }).join("");
  var ticks = [0,6,12,18].filter(function(i){return i<mm.length;}).map(function(i){ return '<text x="'+(i*bw+2)+'" y="'+(Hh-2)+'" font-size="11" fill="var(--muted)">'+(i===0?"ตอนนี้":H.time[i0+i].slice(11,13)+":00")+'</text>'; }).join("");
  var D = j.daily, names = ["วันนี้","พรุ่งนี้","มะรืนนี้"];
  card.innerHTML = '<div class="eyebrow">พยากรณ์ฝน · '+esc(pt.name)+'</div>'+
    '<div class="big">'+total.toFixed(0)+' มม. <small>ใน 24 ชม. · '+rainClass(total)+' · โอกาสฝนสูงสุด '+pmax+'%</small></div>'+
    (past!==null ? '<div class="note" style="margin:2px 0">ที่ผ่านมา 24 ชม. ตกไปแล้ว ~'+past.toFixed(0)+' มม. ('+rainClass(past)+')</div>' : "")+
    '<div class="raingauge" id="gaugeLine" aria-live="polite"><span class="note">กำลังโหลดค่าจากเครื่องวัดฝน…</span></div>'+
    (total>=0.5 ? '<div class="note" style="margin:2px 0">ช่วงที่ฝนแรงสุด ~'+H.time[i0+peak].slice(11,16)+' น. ('+mm[peak].toFixed(1)+' มม./ชม.)</div>' : '<div class="note" style="margin:2px 0">ช่วง 24 ชม. ข้างหน้าแทบไม่มีฝน</div>')+
    '<svg viewBox="0 0 '+W+' '+Hh+'" role="img" aria-label="ปริมาณฝนรายชั่วโมงใน 24 ชั่วโมงข้างหน้า">'+bars+ticks+'</svg>'+
    '<div class="days">'+D.time.map(function(d,i){ return '<div class="day">'+names[i]+'<b>'+(D.precipitation_sum[i]||0).toFixed(0)+' มม.</b><span class="note">โอกาส '+(D.precipitation_probability_max[i]||0)+'%</span></div>'; }).join("")+'</div>'+
    '<p class="note">ข้อมูลพยากรณ์และฝนที่ผ่านมาจาก <a href="https://open-meteo.com" target="_blank" rel="noopener">Open-Meteo</a> เป็นผลจากแบบจำลอง ไม่ใช่เครื่องวัดฝนจริง ไม่ใช่ประกาศของกรมอุตุนิยมวิทยา ตำแหน่งที่ใช้ดึงพยากรณ์จะถูกส่งไปยังบริการนี้</p>';
  card.hidden = false;
  lastRain = {name:pt.name, past:past, next:total}; drawOneLine();
  loadGauges(pt);
}
/* Rain actually caught by gauges (ThaiWater, via our server): a province summary, or the gauges nearest to a place. */
var gaugeCache = {}, gaugeSeq = 0;
function gaugeHtml2(g, pt){
  if(pt.place){
    var l = (g && g.nearby) || [];
    if(!l.length) return '<span class="note">ไม่มีเครื่องวัดฝนในรัศมี 25 กม. ที่รายงานเมื่อไม่นานมานี้</span>';
    return '<b>เครื่องวัดฝนใกล้คุณ (ฝน 24 ชม. ที่วัดได้จริง)</b>'+l.map(function(r){
      return '<div class="gl"><span>'+esc(r.name)+' <small>'+r.distance_km+' กม.</small></span><b>'+Math.round(r.mm24)+' มม.</b></div>'; }).join("");
  }
  if(!g || !g.stations) return '<span class="note">ยังไม่มีเครื่องวัดฝนในจังหวัดนี้ที่รายงานเมื่อไม่นานมานี้</span>';
  return '<b>เครื่องวัดฝนจริง 24 ชม.: สูงสุด '+Math.round(g.max.mm24)+' มม.</b> ที่ '+esc(g.max.name)+
    '<div class="note" style="margin:2px 0 0">เฉลี่ย '+Math.round(g.mean_mm)+' มม. จาก '+g.stations+' สถานี'+(g.over_35 ? ' · ตั้งแต่ 35 มม. ขึ้นไป '+g.over_35+' สถานี' : '')+'</div>';
}
function loadGauges(pt){
  var my = ++gaugeSeq, box = $("gaugeLine"); if(!box) return;
  var q = pt.place ? "lat="+pt.lat.toFixed(4)+"&lng="+pt.lng.toFixed(4) : "province="+encodeURIComponent(pt.province||""), hit = gaugeCache[q];
  function show(g){ var b = $("gaugeLine"); if(my===gaugeSeq && b) b.innerHTML = gaugeHtml2(g, pt)+'<div class="note" style="margin:4px 0 0">วัดจริงจากสถานีวัดฝนของ ThaiWater ต่างจากพยากรณ์ด้านล่างที่มาจากแบบจำลอง</div>'; }
  if(hit && Date.now()-hit.t < 5*60000){ show(hit.g); return; }
  getJSON("/api/rain?"+q).then(function(g){ gaugeCache[q] = {t:Date.now(), g:g}; show(g); })
    .catch(function(){ var b = $("gaugeLine"); if(my===gaugeSeq && b) b.innerHTML = '<span class="note">ยังไม่มีข้อมูลจากเครื่องวัดฝนตอนนี้</span>'; });
}

/* ---------- low battery: switch off the heavy layers (Chrome on Android only; other browsers have no Battery API) ---------- */
var batteryHandled = false;
function watchBattery(){
  if(!navigator.getBattery) return;
  navigator.getBattery().then(function(b){
    function check(){
      if(b.charging || b.level>=0.3){ batteryHandled = false; return; }
      if(b.level>=0.2 || batteryHandled) return;
      batteryHandled = true;
      var off = ["chkRadar","chkFlood"].filter(function(id){ return $(id).checked; });
      off.forEach(function(id){ $(id).click(); });
      if(off.length) toast("แบตเตอรี่เหลือ "+Math.round(b.level*100)+"% ปิดเรดาร์ฝนและภาพดาวเทียมให้เพื่อประหยัดแบต เปิดใหม่ได้ที่ปุ่มชั้นแผนที่");
    }
    b.addEventListener("levelchange", check); b.addEventListener("chargingchange", check); check();
  }).catch(function(){});
}
watchBattery();

/* ---------- shelters (DDPM open data, via our API) ---------- */
function syncShelterSize(){ map.getContainer().classList.toggle("zsm", map.getZoom() < 12); }
map.on("zoomend", syncShelterSize); syncShelterSize();
var shelterLayer = L.layerGroup().addTo(map), shelterProv = null, shelterData = [];
function shelterPopup(s){
  return '<div class="shp"><b>'+esc(s.name)+'</b><div class="note">ต.'+esc(s.subdistrict)+' อ.'+esc(s.district)+' จ.'+esc(s.province)+'</div>'+
    (s.capacity?'<div class="note">รองรับประมาณ '+s.capacity+' คน</div>':'')+
    (s.phone?'<div><a href="'+esc(telHref(s.phone))+'">โทร '+esc(firstPhone(s.phone))+'</a></div>':'')+
    '<div><a target="_blank" rel="noopener" href="https://www.google.com/maps/dir/?api=1&destination='+s.lat+','+s.lng+'">นำทาง</a></div>'+
    '<div class="note">ข้อมูลจาก ปภ. อาจไม่เป็นปัจจุบัน</div></div>';
}
function drawShelters(){
  shelterLayer.clearLayers();
  if(!$("chkShl").checked) return;
  shelterData.forEach(function(s){
    L.marker([s.lat,s.lng],{icon:L.divIcon({className:"",html:'<div class="shw"><div class="shl">'+HOUSE+'</div></div>',iconSize:[32,32],iconAnchor:[16,16]}),title:"ศูนย์พักพิง: "+s.name,keyboard:true})
      .bindPopup(shelterPopup(s)).addTo(shelterLayer);
  });
}
function areaSel(){   // the district / subdistrict picked in the bar, or null for each
  var di = $("dist").value, ti = $("tam").value;
  var d = (areaCur && di!=="" && areaCur.province===province) ? areaCur.districts[+di] : null;
  return {d:d, t:(d && ti!=="") ? d.tambons[+ti] : null};
}
function shelterKey(){ var a = areaSel(); return province+"|"+(a.d ? a.d.name : "")+"|"+(a.t ? a.t.name : ""); }
function refreshShelters(){
  if(!$("chkShl").checked){ drawShelters(); return; }   // unticked: drawShelters() just clears the markers
  if(!province){ shelterData = []; shelterProv = null; drawShelters(); toast("เลือกจังหวัดเพื่อดูศูนย์พักพิง"); return; }
  var key = shelterKey();
  if(key===shelterProv){ drawShelters(); return; }
  var a = areaSel(), pv = "province="+encodeURIComponent(province), tries = [];
  if(a.t) tries.push({q:"&district="+encodeURIComponent(a.d.name)+"&tambon="+encodeURIComponent(a.t.name), where:"ตำบลนี้"});
  if(a.d) tries.push({q:"&district="+encodeURIComponent(a.d.name), where:"อำเภอนี้"});
  tries.push({q:"", where:"จังหวัดนี้"});
  (function next(i){        // narrowest area first; if the DDPM list has nothing there, widen and say so
    getJSON("/api/shelters?"+pv+tries[i].q).then(function(l){
      if(shelterKey()!==key) return;
      if(!l.length && i<tries.length-1){ next(i+1); return; }
      shelterProv = key; shelterData = l; drawShelters();
      if(!l.length) toast("ไม่มีข้อมูลศูนย์พักพิงของจังหวัดนี้");
      else if(i>0) toast("ไม่พบศูนย์พักพิงใน"+tries[i-1].where+"ในข้อมูล ปภ. จึงแสดงของ"+tries[i].where.replace("นี้","")+"แทน");
    }).catch(function(){ toast("โหลดข้อมูลศูนย์พักพิงไม่ได้"); });
  })(0);
}
$("chkShl").addEventListener("change", refreshShelters);
function showNearShelters(o){
  var card = $("shelterCard"); card.hidden = true; if(!o) return;
  getJSON("/api/shelters?lat="+o.lat+"&lng="+o.lng+"&limit=3").then(function(l){
    if(origin!==o || !l.length) return;
    $("shelterList").innerHTML = l.map(function(s,i){
      return '<li><button class="srow" type="button" data-i="'+i+'"><span>'+esc(s.name)+'<small>ต.'+esc(s.subdistrict)+' อ.'+esc(s.district)+(s.capacity?' · รองรับ ~'+s.capacity+' คน':'')+'</small></span><span class="dist">'+s.distance_km+' กม.</span></button></li>';
    }).join("");
    Array.prototype.forEach.call($("shelterList").querySelectorAll(".srow"),function(b){
      b.addEventListener("click",function(){ var s = l[+b.getAttribute("data-i")];
        L.popup({offset:[0,-6]}).setLatLng([s.lat,s.lng]).setContent(shelterPopup(s)).openOn(map);
        flyToVisible([s.lat,s.lng], Math.max(map.getZoom(),14)); });
    });
    card.hidden = false;
  }).catch(function(){});
}

/* ---------- rain radar (RainViewer, past 2 h) ---------- */
var radarFrames = null, radarLayer = null, radarIdx = 0, radarTimer = null;
function radarShow(i){
  var f = radarFrames[i], url = radarFrames.host + f.path + "/256/{z}/{x}/{y}/2/1_1.png";
  var next = L.tileLayer(url,{opacity:.6, maxNativeZoom:7, maxZoom:19, attribution:'ฝน: <a href="https://www.rainviewer.com" target="_blank" rel="noopener">RainViewer</a>'}).addTo(map);
  if(radarLayer) map.removeLayer(radarLayer); radarLayer = next; radarIdx = i;
  $("radarTime").textContent = new Date(f.time*1000).toLocaleTimeString("th-TH",{hour:"2-digit",minute:"2-digit",timeZone:"Asia/Bangkok"})+" น.";
}
function radarStop(){ clearInterval(radarTimer); radarTimer = null; $("radarPlay").textContent = "▶ เล่น"; }
function radarOff(){ radarStop(); if(radarLayer){ map.removeLayer(radarLayer); radarLayer = null; } $("radarCtl").hidden = true; $("radarTime").textContent = ""; }
$("chkRadar").addEventListener("change",function(){
  if(!this.checked){ radarOff(); return; }
  fetch("https://api.rainviewer.com/public/weather-maps.json").then(function(r){ return r.json(); }).then(function(j){
    var past = (j.radar && j.radar.past) || [];
    if(!past.length || !$("chkRadar").checked){ if($("chkRadar").checked){ toast("ยังไม่มีภาพเรดาร์ฝน"); $("chkRadar").checked = false; syncQuick(); } return; }
    radarFrames = past; radarFrames.host = j.host; $("radarCtl").hidden = false; radarShow(past.length-1);
  }).catch(function(){ $("chkRadar").checked = false; syncQuick(); toast("โหลดเรดาร์ฝนไม่ได้"); });
});
$("radarPlay").addEventListener("click",function(){
  if(radarTimer){ radarStop(); return; }
  this.textContent = "⏸ หยุด"; radarIdx = -1;
  radarTimer = setInterval(function(){ radarShow((radarIdx+1) % radarFrames.length); }, 700);
});

/* ---------- user flood reports ---------- */
var rptLayer = L.layerGroup().addTo(map), rptPoint = null, rptPicking = false, rptTemp = null;
var RPT_DROP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 3C9 7.4 6.5 10.3 6.5 13.7a5.5 5.5 0 0 0 11 0C17.5 10.3 15 7.4 12 3z"/></svg>';
function agoText(min){ return min<2 ? "เมื่อสักครู่" : min<60 ? min+" นาทีที่แล้ว" : Math.round(min/60)+" ชม. ที่แล้ว"; }
function drawReports(list){
  rptLayer.clearLayers();
  if(!$("chkRpt").checked) return;
  (list||[]).forEach(function(r){
    var size = 24 + r.level*3;
    var m = L.marker([r.lat,r.lng],{icon:L.divIcon({className:"",html:'<div class="rp l'+r.level+'" style="width:'+size+'px;height:'+size+'px">'+RPT_DROP+'</div>',iconSize:[size,size],iconAnchor:[size/2,size/2]}),
      zIndexOffset:500, keyboard:true, title:"ผู้ใช้รายงาน: "+r.label});
    m.bindPopup('<div class="rpp"><b>'+esc(r.label)+'</b><div class="note">ผู้ใช้รายงาน · '+agoText(r.age_min)+(r.confirmed>1?' · ยืนยันโดย '+r.confirmed+' คน':'')+'</div>'+
      (r.note?'<div>'+esc(r.note)+'</div>':'')+
      '<div class="note">ยังไม่ผ่านการตรวจสอบ</div><button type="button" class="rp-flag" data-id="'+r.id+'">ไม่จริง / น้ำลดแล้ว</button></div>');
    m.addTo(rptLayer);
  });
}
var rptData = [];
function loadReports(){ return getJSON("/api/flood-reports").then(function(l){ rptData = l; drawReports(l); }).catch(function(){}); }
$("chkRpt").addEventListener("change", function(){ drawReports(rptData); });
map.on("popupopen",function(e){
  var b = e.popup.getElement() && e.popup.getElement().querySelector(".rp-flag"); if(!b) return;
  b.addEventListener("click",function(){
    b.disabled = true;
    fetch(API+"/api/flood-reports/"+b.getAttribute("data-id")+"/flag",{method:"POST"}).then(function(r){
      b.textContent = r.ok ? "ขอบคุณที่แจ้ง" : "ส่งไม่สำเร็จ"; if(r.ok) loadReports();
    }).catch(function(){ b.textContent = "ส่งไม่สำเร็จ"; });
  });
});

function rptOpen(){ $("rpt").hidden = false; closeLayers(); rptCheck(); $("rptSend").focus(); }
function rptClose(){ $("rpt").hidden = true; }
function rptCheck(){
  var lv = document.querySelector('input[name=rptLv]:checked');
  $("rptSend").disabled = !(rptPoint && lv);
  $("rptWhere").textContent = rptPoint ? "ตำแหน่ง: "+rptPoint.lat.toFixed(4)+", "+rptPoint.lng.toFixed(4) : "ยังไม่ได้เลือกตำแหน่ง";
}
function rptSetPoint(ll){
  rptPoint = {lat:ll.lat, lng:ll.lng};
  if(rptTemp) map.removeLayer(rptTemp);
  rptTemp = L.circleMarker([ll.lat,ll.lng],{radius:9,color:"#2f6fd6",weight:3,fillColor:"#fff",fillOpacity:.9}).addTo(map);
  rptCheck();
}
$("btnReport").addEventListener("click", rptOpen);
$("rptCancel").addEventListener("click", rptClose);
$("rpt").addEventListener("click", function(e){ if(e.target===$("rpt")) rptClose(); });
document.addEventListener("keydown",function(e){ if(e.key==="Escape" && !$("rpt").hidden) rptClose(); });
Array.prototype.forEach.call(document.querySelectorAll('input[name=rptLv]'),function(i){ i.addEventListener("change", rptCheck); });
$("rptGps").addEventListener("click",function(){
  if(!navigator.geolocation){ $("rptErr").hidden=false; $("rptErr").textContent="เบราว์เซอร์นี้ไม่รองรับการระบุตำแหน่ง ลองเลือกบนแผนที่"; return; }
  $("rptErr").hidden = true; $("rptWhere").textContent = "กำลังหาตำแหน่ง…";
  navigator.geolocation.getCurrentPosition(function(p){ rptSetPoint({lat:p.coords.latitude,lng:p.coords.longitude}); map.setView([p.coords.latitude,p.coords.longitude], Math.max(map.getZoom(),15)); },
    function(){ rptCheck(); $("rptErr").hidden=false; $("rptErr").textContent="ใช้ตำแหน่งไม่ได้ กรุณาอนุญาตการเข้าถึงตำแหน่ง หรือเลือกบนแผนที่"; },
    {timeout:10000,maximumAge:60000,enableHighAccuracy:true});
});
$("rptPick").addEventListener("click",function(){
  rptPicking = true; rptClose(); map.getContainer().style.cursor = "crosshair";
  toast("แตะบนแผนที่ตรงจุดที่น้ำท่วม", true);
});
$("rptSend").addEventListener("click",function(){
  var lv = document.querySelector('input[name=rptLv]:checked'); if(!rptPoint || !lv) return;
  $("rptSend").disabled = true; $("rptErr").hidden = true;
  fetch(API+"/api/flood-reports",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({lat:rptPoint.lat,lng:rptPoint.lng,level:+lv.value,note:$("rptNote").value.trim()||null})
  }).then(function(r){ return r.json().catch(function(){return {};}).then(function(j){ return {ok:r.ok, j:j}; }); }).then(function(x){
    if(!x.ok){ $("rptErr").hidden=false; $("rptErr").textContent = typeof x.j.detail==="string" ? x.j.detail : "ส่งไม่สำเร็จ ตรวจข้อมูลแล้วลองใหม่"; rptCheck(); return; }
    rptClose(); if(rptTemp){ map.removeLayer(rptTemp); rptTemp=null; }
    var ll = rptPoint; rptPoint = null; $("rptNote").value=""; $("chkRpt").checked = true;
    Array.prototype.forEach.call(document.querySelectorAll('input[name=rptLv]'),function(i){ i.checked=false; });
    toast("ขอบคุณ ส่งรายงานแล้ว จะแสดงบนแผนที่ 12 ชม."); loadReports(); map.setView([ll.lat,ll.lng], Math.max(map.getZoom(),14));
  }).catch(function(){ $("rptErr").hidden=false; $("rptErr").textContent="เชื่อมต่อไม่ได้ ลองใหม่อีกครั้ง"; rptCheck(); });
});
loadReports(); setInterval(loadReports, 60000);

/* ---------- route check: OSRM route + our station statuses + user flood reports ---------- */
var ROUTER = "https://router.project-osrm.org";
var RT_STATION_KM = 1.0, RT_REPORT_KM = 0.3;
var rtLayer = L.layerGroup().addTo(map), rtFromPt = null, rtRoutes = null, rtSel = 0, rtNames = null;
function rtClear(){   // remove the drawn route, its card and the typed places so the next check starts clean
  rtLayer.clearLayers(); rtRoutes = null; rtFromPt = null;
  var c = $("routeCard"); c.hidden = true; c.innerHTML = "";
  $("rtFrom").value = ""; $("rtTo").value = ""; $("rtErr").hidden = true;
  if(typeof syncQuick==="function") syncQuick();
}
function rtOpen(){ $("rt").hidden = false; closeLayers(); $("rtErr").hidden = true; $("rtTo").focus(); }
function rtClose(){ $("rt").hidden = true; }
function rtFail(msg){ $("rtErr").hidden = false; $("rtErr").textContent = msg; $("rtGo").disabled = false; $("rtGo").textContent = "ตรวจเส้นทาง"; }
function geo1(q){
  return fetch("https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=th&accept-language=th&q="+encodeURIComponent(q))
    .then(function(r){ if(!r.ok) throw new Error("geo"); return r.json(); })
    .then(function(l){ if(!l.length) throw new Error("nf:"+q); return {lat:+l[0].lat, lng:+l[0].lon, name:(l[0].display_name||q).split(",")[0]}; });
}
function distKmToLine(p, line){   // min distance from point to polyline [[lng,lat]...] (small-area planar approximation) + the nearest vertex index
  var kx = 111.32*Math.cos(p.lat*Math.PI/180), ky = 110.57, best = 1e9, bi = 0;
  for(var i=0;i<line.length-1;i++){
    var ax=(line[i][0]-p.lng)*kx, ay=(line[i][1]-p.lat)*ky, bx=(line[i+1][0]-p.lng)*kx, by=(line[i+1][1]-p.lat)*ky;
    var dx=bx-ax, dy=by-ay, t = dx*dx+dy*dy ? Math.max(0,Math.min(1,-(ax*dx+ay*dy)/(dx*dx+dy*dy))) : 0;
    var d = Math.hypot(ax+t*dx, ay+t*dy); if(d<best){ best=d; bi=i; }
  }
  return {km:best, idx:bi};
}
function rtEvaluate(route){
  var line = route.geometry.coordinates, cum = [0];
  for(var i=1;i<line.length;i++) cum.push(cum[i-1]+kmBetween({lat:line[i-1][1],lng:line[i-1][0]},{lat:line[i][1],lng:line[i][0]}));
  var minLng=1e9,maxLng=-1e9,minLat=1e9,maxLat=-1e9;
  line.forEach(function(c){ minLng=Math.min(minLng,c[0]); maxLng=Math.max(maxLng,c[0]); minLat=Math.min(minLat,c[1]); maxLat=Math.max(maxLat,c[1]); });
  function near(p){ return p.lat>minLat-.02 && p.lat<maxLat+.02 && p.lng>minLng-.02 && p.lng<maxLng+.02; }
  var pts = [];
  rptData.forEach(function(r){
    if(!near(r)) return; var d = distKmToLine(r, line);
    if(d.km<=RT_REPORT_KM) pts.push({kind:"report", lat:r.lat, lng:r.lng, name:"รายงานผู้ใช้: "+r.label, sev:3, at:cum[d.idx], off:d.km});
  });
  stations.forEach(function(s0){
    var s = (s0); if(s.stale || (s.status!=="alert" && s.status!=="watch") || !near(s)) return;
    var d = distKmToLine(s, line);
    if(d.km<=RT_STATION_KM) pts.push({kind:"station", lat:s.lat, lng:s.lng, name:s.name+" ("+(s.status==="alert"?"เตือนภัย":"เฝ้าระวัง")+(s.pct_of_bank!=null?" "+Math.round(s.pct_of_bank)+"%":"")+")", sev:s.status==="alert"?2:1, at:cum[d.idx], off:d.km});
  });
  pts.sort(function(a,b){ return a.at-b.at; });
  return {route:route, pts:pts, score:pts.reduce(function(n,p){ return n+p.sev; },0), km:route.distance/1000, min:Math.round(route.duration/60)};
}
function rtVerdict(ev){
  var rep = ev.pts.filter(function(p){return p.kind==="report";}).length, al = ev.pts.filter(function(p){return p.sev===2;}).length, wa = ev.pts.filter(function(p){return p.sev===1;}).length;
  if(rep) return {cls:"bad", color:"#d2372f", text:"มีรายงานน้ำท่วมบนเส้นทาง "+rep+" จุด"+(al?" และผ่านใกล้สถานีเตือนภัย "+al+" แห่ง":"")+" ควรเลี่ยงหรือตรวจสอบก่อนเดินทาง"};
  if(al) return {cls:"bad", color:"#d2372f", text:"ผ่านใกล้สถานีเตือนภัย "+al+" แห่ง ระดับน้ำใกล้ล้นตลิ่ง ถนนช่วงนั้นอาจมีน้ำท่วม"};
  if(wa) return {cls:"warn", color:"#c98a00", text:"ผ่านใกล้สถานีเฝ้าระวัง "+wa+" แห่ง ควรติดตามสถานการณ์ระหว่างเดินทาง"};
  return {cls:"ok", color:"#17835a", text:"ไม่พบจุดเสี่ยงจากข้อมูลที่มี (ไม่ได้หมายความว่าปลอดภัย ข้อมูลอาจไม่ครบหรือไม่ทัน)"};
}
function rtDraw(){
  rtLayer.clearLayers();
  rtRoutes.forEach(function(ev,i){
    if(i===rtSel) return;
    L.polyline(ev.route.geometry.coordinates.map(function(c){return [c[1],c[0]];}),{color:"#7a8c96",weight:5,opacity:.7,dashArray:"6 8"}).addTo(rtLayer)
      .on("click",function(){ rtSel=i; rtDraw(); rtCard(); });
  });
  var ev = rtRoutes[rtSel], v = rtVerdict(ev), ll = ev.route.geometry.coordinates.map(function(c){return [c[1],c[0]];});
  L.polyline(ll,{color:"#fff",weight:10,opacity:.95,lineCap:"round"}).addTo(rtLayer);
  L.polyline(ll,{color:v.color,weight:6,opacity:1,lineCap:"round"}).addTo(rtLayer);
  ev.pts.forEach(function(p){
    L.circleMarker([p.lat,p.lng],{radius:8,color:"#fff",weight:2,fillColor:p.sev===1?"#c98a00":p.kind==="report"?"#2f6fd6":"#d2372f",fillOpacity:1}).addTo(rtLayer).bindTooltip(esc(p.name),{direction:"top"});
  });
  [ll[0], ll[ll.length-1]].forEach(function(c,i){ L.marker(c,{icon:L.divIcon({className:"",iconSize:[26,26],iconAnchor:[13,13],html:'<div style="width:26px;height:26px;border-radius:50%;background:'+(i?"#10252f":"#0b6e8f")+';color:#fff;border:2px solid #fff;display:grid;place-items:center;font-weight:700;font-size:13px">'+(i?"B":"A")+'</div>'}),interactive:false}).addTo(rtLayer); });
}
function rtCard(){
  var ev = rtRoutes[rtSel], v = rtVerdict(ev), c = $("routeCard");
  c.hidden = false;
  c.innerHTML = '<div class="sumhead"><h2 style="margin:0">เช็กเส้นทาง</h2><button class="linkbtn" id="rtClear" type="button" style="border:0">ล้าง</button></div>'+
    '<div class="note" style="margin:2px 0">'+esc(rtNames[0])+' → '+esc(rtNames[1])+'</div>'+
    '<div class="verd '+v.cls+'">'+esc(v.text)+'</div>'+
    (rtRoutes.length>1 ? rtRoutes.map(function(e,i){ return '<button class="rtopt" type="button" data-i="'+i+'" aria-pressed="'+(i===rtSel)+'"><span>เส้นทาง '+(i+1)+(i===0?" (เสี่ยงน้อยสุด)":"")+'</span><span>'+e.km.toFixed(0)+' กม. · '+e.min+' นาที · เสี่ยง '+e.pts.length+' จุด</span></button>'; }).join("") :
      '<div class="note">'+ev.km.toFixed(0)+' กม. · ประมาณ '+ev.min+' นาที</div>')+
    (ev.pts.length ? '<ul class="rtpts" style="margin-top:8px">'+ev.pts.slice(0,10).map(function(p){ return '<li><span>'+esc(p.name)+'</span><small>ที่ ~'+p.at.toFixed(0)+' กม.</small></li>'; }).join("")+'</ul>' : "")+
    '<p class="note">ตรวจเฉพาะรายงานผู้ใช้และสถานีวัดน้ำ ยังไม่รวมพื้นที่น้ำท่วมจากดาวเทียม (ดูได้ที่ชั้นแผนที่) ไม่ใช่ประกาศทางการ โทร 1784</p>';
  $("rtClear").addEventListener("click", rtClear);
  Array.prototype.forEach.call(c.querySelectorAll(".rtopt"),function(b){ b.addEventListener("click",function(){ rtSel = +b.getAttribute("data-i"); rtDraw(); rtCard(); }); });
}
function rtRun(a, b){
  return fetch(ROUTER+"/route/v1/driving/"+a.lng+","+a.lat+";"+b.lng+","+b.lat+"?overview=full&geometries=geojson&alternatives=true")
    .then(function(r){ if(!r.ok) throw new Error("route"); return r.json(); })
    .then(function(j){
      if(j.code!=="Ok" || !j.routes.length) throw new Error("noroute");
      rtRoutes = j.routes.slice(0,3).map(rtEvaluate).sort(function(x,y){ return x.score-y.score || x.min-y.min; });
      rtSel = 0; rtDraw(); rtCard();
      map.fitBounds(L.latLngBounds(rtRoutes[0].route.geometry.coordinates.map(function(c){return [c[1],c[0]];})), Object.assign({maxZoom:13}, fitPad()));
      revealInSheet($("routeCard")); syncQuick();
    });
}
$("btnRoute").addEventListener("click", rtOpen);
$("rtCancel").addEventListener("click", rtClose);
$("rtReset").addEventListener("click", function(){ rtClear(); $("rtFrom").focus(); });
$("rt").addEventListener("click", function(e){ if(e.target===$("rt")) rtClose(); });
document.addEventListener("keydown",function(e){ if(e.key==="Escape" && !$("rt").hidden) rtClose(); });
$("rtFrom").addEventListener("input", function(){ rtFromPt = null; });
$("rtGps").addEventListener("click",function(){
  if(!navigator.geolocation){ rtFail("เบราว์เซอร์นี้ไม่รองรับการระบุตำแหน่ง พิมพ์ชื่อสถานที่แทน"); return; }
  $("rtErr").hidden = true; $("rtFrom").value = "กำลังหาตำแหน่ง…";
  navigator.geolocation.getCurrentPosition(function(p){ rtFromPt = {lat:p.coords.latitude, lng:p.coords.longitude, name:"ตำแหน่งของฉัน"}; $("rtFrom").value = "ตำแหน่งของฉัน"; },
    function(){ $("rtFrom").value = ""; rtFail("ใช้ตำแหน่งไม่ได้ กรุณาอนุญาตการเข้าถึงตำแหน่ง หรือพิมพ์ชื่อสถานที่แทน"); }, {timeout:10000,maximumAge:60000});
});
$("rtGo").addEventListener("click",function(){
  var f = $("rtFrom").value.trim(), t = $("rtTo").value.trim();
  if(!(rtFromPt || f.length>=2) || t.length<2){ rtFail("กรอกต้นทางและปลายทางก่อน"); return; }
  $("rtErr").hidden = true; this.disabled = true; this.textContent = "กำลังตรวจ…";
  Promise.all([rtFromPt ? Promise.resolve(rtFromPt) : geo1(f), geo1(t)]).then(function(ab){
    rtNames = [ab[0].name, ab[1].name]; return rtRun(ab[0], ab[1]);
  }).then(function(){ $("rtGo").disabled = false; $("rtGo").textContent = "ตรวจเส้นทาง"; rtClose(); })
    .catch(function(e){ var m = String(e&&e.message||"");
      rtFail(m.indexOf("nf:")===0 ? "ไม่พบสถานที่ \""+m.slice(3)+"\" ลองพิมพ์ให้ละเอียดขึ้น เช่นเพิ่มชื่ออำเภอหรือจังหวัด" : m==="noroute" ? "หาเส้นทางระหว่างสองจุดนี้ไม่ได้" : "คำนวณเส้นทางไม่ได้ในขณะนี้ ลองใหม่อีกครั้ง"); });
});

function boundColor(){   // navy on the light map; a lighter blue on the dark one, where navy would vanish. Never red: red means "alert" on the stations
  return window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "#5b8cff" : "#1e3a8a";
}
/* ---------- outline of the chosen province / district / subdistrict (UN OCHA boundaries, via our API) ---------- */
var boundLayer = null, boundKey = "", boundSeq = 0;
var BOUND_ATTR = 'ขอบเขต: UN OCHA / กรมแผนที่ทหาร (CC BY-IGO) แบบย่อรูป';
function updateBoundary(){
  var di = $("dist").value, ti = $("tam").value, key = "";
  var d = (areaCur && di!=="" && areaCur.province===province) ? areaCur.districts[+di] : null;
  var t = (d && ti!=="") ? d.tambons[+ti] : null;
  if(province) key = province + (d ? "|"+d.name : "") + (t ? "|"+t.name : "");
  if(key===boundKey) return;                       // nothing new to draw
  boundKey = key; var my = ++boundSeq;
  if(boundLayer){ map.removeLayer(boundLayer); boundLayer = null; }
  if(!key) return;
  var q = "?province="+encodeURIComponent(province) + (d ? "&district="+encodeURIComponent(d.name) : "") + (t ? "&tambon="+encodeURIComponent(t.name) : "");
  getJSON("/api/boundary"+q).then(function(f){
    if(my!==boundSeq) return;
    boundLayer = L.geoJSON(f,{interactive:false, attribution:BOUND_ATTR,
      style:{color:boundColor(), weight:t?3.5:d?3:2.5, opacity:.95, fillColor:boundColor(), fillOpacity:t?.10:.05, lineJoin:"round"}}).addTo(map);
    boundLayer.bringToBack();
  }).catch(function(){});                            // no outline for this area: draw nothing rather than something wrong
}

/* ---------- area pills: province > district > subdistrict (centre point of the subdistrict) ---------- */
var areaCache = {}, areaCur = null, areaSeq = 0;
function arPre(prov){ return prov==="กรุงเทพมหานคร" ? {d:"เขต", t:"แขวง"} : {d:"อำเภอ", t:"ตำบล"}; }
function arFill(sel, items, placeholder){
  sel.innerHTML = '<option value="">'+placeholder+'</option>'+items.map(function(t,i){ return '<option value="'+i+'">'+esc(t)+'</option>'; }).join("");
  sel.disabled = !items.length; sel.value = "";
  if(typeof areaBtnText==="function") areaBtnText();
}
function arReset(){ var pre = arPre(province); areaCur = null; arFill($("dist"), [], pre.d); arFill($("tam"), [], pre.t); }
/* phones: district + subdistrict live in one dialog behind a single button (the same <select>s are moved, so all their logic is unchanged) */
function areaBtnText(){
  var d = $("dist"), t = $("tam"), pre = arPre(province);
  var dn = d.value!=="" ? d.options[d.selectedIndex].text : "", tn = t.value!=="" ? t.options[t.selectedIndex].text : "";
  $("areaTxt").textContent = tn ? tn+" · "+dn : dn || pre.d+" / "+pre.t;
  $("btnArea").disabled = d.disabled;
  $("arLabD").textContent = pre.d; $("arLabT").textContent = pre.t;
}
function placeAreaSelects(){
  var wide = window.matchMedia("(min-width:900px)").matches;
  var dp = $("pillDist"), tp = $("pillTam"), d = $("dist"), t = $("tam");
  if(wide){ if(d.parentNode!==dp) dp.insertBefore(d, dp.firstChild); if(t.parentNode!==tp) tp.insertBefore(t, tp.firstChild); $("ar").hidden = true; }
  else { if(d.parentNode!==$("slotDist")) $("slotDist").appendChild(d); if(t.parentNode!==$("slotTam")) $("slotTam").appendChild(t); }
}
$("btnArea").addEventListener("click",function(){ areaBtnText(); $("ar").hidden = false; $("dist").focus(); });
$("arDone").addEventListener("click",function(){ $("ar").hidden = true; });
$("arClear").addEventListener("click",function(){
  var d = $("dist"); d.value = ""; d.dispatchEvent(new Event("change")); $("ar").hidden = true;
});
$("ar").addEventListener("click",function(e){ if(e.target===$("ar")) $("ar").hidden = true; });
document.addEventListener("keydown",function(e){ if(e.key==="Escape" && !$("ar").hidden) $("ar").hidden = true; });
window.addEventListener("resize", placeAreaSelects);
function syncAreaPills(){          // call whenever the province may have changed; keeps the picks while it has not
  if(!province){ arReset(); updateBoundary(); return; }
  if(areaCur && areaCur.province===province) return;
  arReset(); updateBoundary(); var my = ++areaSeq, name = province;
  var show = function(d){
    if(my!==areaSeq || province!==name) return;
    areaCur = d; var pre = arPre(name);
    arFill($("dist"), d.districts.map(function(x){return x.name;}), pre.d); arFill($("tam"), [], pre.t);
  };
  if(areaCache[name]){ show(areaCache[name]); return; }
  getJSON("/api/areas?province="+encodeURIComponent(name)).then(function(d){ areaCache[name] = d; show(d); }).catch(function(){});   // no list for this province: pills stay disabled
}
$("dist").addEventListener("change", function(){
  setTimeout(areaBtnText, 0);
  var pre = arPre(province);
  if(origin && origin.kind==="area") setOrigin(null);
  if(this.value==="" || !areaCur){ arFill($("tam"), [], pre.t); updateBoundary(); refreshShelters(); return; }
  var d = areaCur.districts[+this.value];
  arFill($("tam"), d.tambons.map(function(t){return t.name;}), pre.t); updateBoundary(); refreshShelters();
  map.fitBounds(L.latLngBounds(d.tambons.map(function(t){return [t.lat,t.lng];})), Object.assign({maxZoom:12}, fitPad()));
});
$("tam").addEventListener("change", function(){
  setTimeout(areaBtnText, 0);
  updateBoundary(); refreshShelters();
  if(this.value===""){ if(origin && origin.kind==="area") setOrigin(null); return; }
  var d = areaCur.districts[+$("dist").value], t = d.tambons[+this.value], pre = arPre(areaCur.province);
  setOrigin({lat:t.lat, lng:t.lng, kind:"area", label:(pre.t==="แขวง" ? "แขวง" : "ต.")+t.name+" "+(pre.d==="เขต" ? "เขต" : "อ.")+d.name+(pre.t==="แขวง" ? " กทม." : " จ."+areaCur.province)});
});

/* ---------- quick layer chips (mirror the checkboxes in the layers panel) ---------- */
var QUICK = [["chkFlood","🛰","ดาวเทียม","น้ำท่วมจากดาวเทียม"],["chkRadar","🌧","ฝน","เรดาร์ฝน"],["chkShl","🏠","พักพิง","ศูนย์พักพิง"]];
function syncQuick(){
  var clr = rtRoutes ? '<button type="button" class="chip qc clr" data-clear="route" title="ลบเส้นทางที่ตรวจไว้ออกจากแผนที่">✕ ล้างเส้นทาง</button>' : "";
  $("qchips").innerHTML = clr + QUICK.filter(function(q){ return q[0]!=="chkFlood" || !$("floodBox").hidden; }).map(function(q){
    return '<button type="button" class="chip qc" data-q="'+q[0]+'" aria-pressed="'+$(q[0]).checked+'" aria-label="ชั้นแผนที่: '+q[3]+'" title="เปิด/ปิดชั้นแผนที่: '+q[3]+'"><span aria-hidden="true">'+q[1]+'</span>'+q[2]+'</button>';
  }).join("");
}
/* number of extra map layers switched on, shown on the "ชั้นแผนที่" button (the user-reports layer is on by default, so it is not counted) */
var LAYER_IDS = ["chkRiver","chkFlood","chkShl","chkRadar"];
function syncLayerCount(){
  var n = LAYER_IDS.filter(function(id){ return $(id).checked && (id!=="chkFlood" || !$("floodBox").hidden); }).length;
  var el = $("lcount"), btn = $("btnLayers");
  el.hidden = !n; el.textContent = n;
  btn.setAttribute("aria-label", "ชั้นแผนที่" + (n ? " เปิดอยู่ "+n+" ชั้น" : "") + " เปิดปิดน้ำท่วมดาวเทียม เรดาร์ฝน ศูนย์พักพิง");
}
LAYER_IDS.forEach(function(id){ $(id).addEventListener("change", syncLayerCount); });
syncLayerCount();
$("qchips").addEventListener("click",function(e){
  var b = e.target.closest ? e.target.closest(".qc") : null; if(!b) return;
  if(b.getAttribute("data-clear")==="route"){ rtClear(); return; }
  var c = $(b.getAttribute("data-q")); c.checked = !c.checked; c.dispatchEvent(new Event("change"));
});
QUICK.forEach(function(q){ $(q[0]).addEventListener("change", syncQuick); });

/* ---------- move the report / route buttons between the sheet (phones) and the top bar (wide screens) ---------- */
var mqWide = window.matchMedia("(min-width:900px)");
function placeActions(){
  var row = $("actrow"); if(!row) return;
  var to = $(mqWide.matches ? "topActs" : "fab");
  if(row.parentNode !== to) to.appendChild(row);
  row.classList.toggle("top", mqWide.matches);
}
if(mqWide.addEventListener) mqWide.addEventListener("change", placeActions); else if(mqWide.addListener) mqWide.addListener(placeActions);
window.addEventListener("resize", placeActions);   // some browsers skip the media-query change event
placeActions();

/* ---------- fade at the right edge of the pills/chips row while it can still scroll ---------- */
(function(){
  var row = document.querySelector(".row2"); if(!row) return;
  function upd(){ row.classList.toggle("more", row.scrollWidth - row.scrollLeft - row.clientWidth > 6 && row.scrollWidth > row.clientWidth); }
  row.addEventListener("scroll", upd, {passive:true}); window.addEventListener("resize", upd);
  if(window.ResizeObserver) new ResizeObserver(upd).observe(row); else setInterval(upd, 1000);
  setTimeout(upd, 300);
})();

/* ---------- start ---------- */
placeAreaSelects();
// pretty area links (/rangsit, /pathumthani ...): the path names an entry of places.json, which only those visits download
var SLUG = (location.pathname.replace(/^\/+|\/+$/g,"").split("/")[0]||"").toLowerCase();
var placeP = /^[a-z]+$/.test(SLUG)
  ? fetch("/places.json").then(function(r){ return r.ok ? r.json() : {}; }).then(function(d){ return d[SLUG] || null; }).catch(function(){ return null; })
  : Promise.resolve(null);
syncQuick();
(function(){   // first placement without the slide animation, so the sheet does not visibly move while the page loads
  var sh = $("sheet"); sh.classList.add("drag");
  setSheet(isMobile() && !QS.get("station") ? "peek" : "half");
  void sh.offsetHeight; requestAnimationFrame(function(){ sh.classList.remove("drag"); });
})();   // phones: map first, drag the sheet up for details
load().then(function(){
  var want = QS.get("station");
  if(want && byId[want]){   // a shared station link wins; show that station's province too so the page agrees with itself
    var sp = byId[want].province;
    if(sp && sp!==province){ province = sp; $("prov").value = province; redraw(); updateRepLink(); syncAreaPills(); }
    select(want,true); return;
  }
  return placeP.then(function(pl){
    if(pl && pl.province && stations.some(function(s){ return s.province===pl.province; })){   // a pretty link such as /rangsit or /pathumthani
      province = pl.province; $("prov").value = province; redraw(); updateRepLink(); syncAreaPills();
      document.title = "น้ำใกล้บ้านฉัน · "+(pl.name || "จ."+pl.province);
      showProvince(true);
      if(pl.view) map.setView([pl.view[0],pl.view[1]], pl.view[2]);
      if(pl.note){ var pc = $("placeCard"); pc.innerHTML = '<h3 style="margin:0 0 4px;font-size:16px">'+esc(pl.name||"")+'</h3><p class="note" style="margin:0">'+esc(pl.note)+'</p>'; pc.hidden = false; }
      return;
    }
    showProvince(true);  // no shared link and no saved home: start on the chosen province
  });
});
// "add the LINE bot" button: shown only when the server can tell us the bot's add-friend link
getJSON("/api/line/add-friend").then(function(d){ if(d && d.url){ $("lineLink").href = d.url; $("lineCta").hidden = false; } }).catch(function(){});
setInterval(load, REFRESH_MS);
if("serviceWorker" in navigator && location.protocol!=="file:"){ navigator.serviceWorker.register("sw.js").catch(function(){}); }
})();
