"""The settings window in the web browser (--gui): a local server with a form, frame preview and film rendering."""
from __future__ import annotations

import collections
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path

from .i18n import Translator, _, ui
from .legacy import upgrade
from .net import CACHE
from .text import DECIMAL
from .util import log

LOG_LINES = 200                                       # lines of program messages the window keeps and shows


class Messages:
    """Messages of a job for the window. Progress lines ("  37%") set the percentage; the other lines are counted and the
    last LOG_LINES of them kept, so the page can append only the lines it has not shown yet."""

    def __init__(self):
        self.lines, self.total, self.pct, self.lock = collections.deque(maxlen=LOG_LINES), 0, 0, threading.Lock()

    def add(self, line: str) -> None:
        m = re.match(r"\s*(\d+)%", line)
        with self.lock:
            if m:
                self.pct = int(m[1])
            elif line.strip():
                self.lines.append(line.rstrip())
                self.total += 1

    def tail(self) -> dict:
        """The kept lines and the number of all lines so far."""
        with self.lock:
            return {"log": list(self.lines), "n": self.total}


def localize(page: str, t: Translator) -> str:
    """The window page in the language of t: ⟦text⟧ marks a text of the HTML, ⟪text⟫ the inside of a JavaScript string."""
    page = re.sub(r"⟦(.*?)⟧", lambda m: html.escape(t.gettext(m[1])), page, flags=re.S)
    return re.sub(r"⟪(.*?)⟫", lambda m: json.dumps(t.gettext(m[1]), ensure_ascii=False)[1:-1].replace("'", "\\'"), page, flags=re.S)


GUI_PAGE = r"""<!doctype html>
<html lang="__LANG__"><head><meta charset="utf-8"><title>gpxfilm</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#0E1113;--panel:#171C20;--field:#0F1417;--line:#2A3238;--ink:#E9ECEE;--mute:#98A3AA;--red:#E5202E}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
.app{display:grid;grid-template-columns:410px 1fr;height:100vh}
aside{background:var(--panel);padding:20px 20px 30px;overflow:auto;border-right:1px solid var(--line)}
h1{font-size:24px;line-height:1;margin:0 0 14px;font-weight:800}h1 span{color:var(--red)}
fieldset{border:0;border-top:1px solid var(--line);margin:16px 0 0;padding:10px 0 0}
legend{padding:0 10px 0 0;font-weight:700;font-size:14px}
label{display:block;margin:9px 0 3px;font-size:13px;color:var(--mute)}
input[type=text],input[type=number],select,textarea{width:100%;background:var(--field);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:8px 9px;font:inherit}
input[type=color]{width:100%;height:37px;padding:2px;background:var(--field);border:1px solid var(--line);border-radius:6px}
textarea{resize:vertical;min-height:78px}
.row{display:flex;gap:8px;align-items:flex-end}.row>div{flex:1;min-width:0}.row>input{flex:1;min-width:0}
.checks{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:7px 10px;margin-top:10px}
.checks label{display:flex;gap:7px;align-items:center;margin:0;color:var(--ink);font-size:14px}
button{font:inherit;font-weight:600;color:var(--ink);background:#222A30;border:1px solid var(--line);border-radius:6px;padding:8px 13px;cursor:pointer;white-space:nowrap}
button:hover{border-color:var(--mute)}button.go{background:var(--red);border-color:var(--red);color:#fff}button:disabled{opacity:.5;cursor:default}
:focus-visible{outline:2px solid var(--red);outline-offset:2px}
main{padding:20px;display:flex;flex-direction:column;gap:14px;min-width:0;overflow:auto}
.stage{flex:none;background:#000;border:1px solid var(--line);border-radius:8px;aspect-ratio:16/9;width:min(100%,calc(66vh*16/9));position:relative;display:grid;place-items:center;overflow:hidden}
.stage img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;display:none}
.stage video{position:absolute;inset:0;width:100%;height:100%;background:#000}
.auto{display:flex;gap:6px;align-items:center;margin:0;font-size:13px;color:var(--mute)}
.note{flex:none;margin:0;color:var(--mute);font-size:13px}
#chips{flex:none;display:flex;flex-wrap:wrap;gap:6px}
.chip{display:inline-flex;border:1px solid var(--line);border-radius:99px;overflow:hidden;background:var(--field)}
.chip button{border:0;border-radius:0;background:transparent;padding:5px 9px;font-weight:500;font-size:13px}
.chip button+button{border-left:1px solid var(--line);color:var(--mute);padding:5px 9px 5px 8px}
.chip button:hover{background:#222A30}.stage p{color:var(--mute);margin:0;padding:24px;text-align:center;max-width:34em}
.actions{flex:none;display:flex;gap:10px;align-items:center;flex-wrap:wrap}#msg{color:var(--mute);font-size:14px}
.bar{flex:none;height:8px;background:#222A30;border-radius:99px;overflow:hidden}.bar i{display:block;height:100%;width:0;background:var(--red);transition:width .3s}
pre{margin:0;background:var(--field);border:1px solid var(--line);border-radius:6px;padding:10px;font:12.5px/1.45 ui-monospace,Menlo,Consolas,monospace;color:var(--mute);white-space:pre-wrap;word-break:break-word;overflow:auto}
#log{flex:1 1 0;min-height:calc(14.5em + 24px)}                 /* the free height under the preview, at least 10 lines */
@media(max-width:900px){.app{grid-template-columns:1fr;height:auto}#log{flex:none;height:max(calc(14.5em + 24px),50vh)}}
</style></head><body>
<div class="app">
<aside>
  <h1>gpx<span>film</span></h1>
  <label for="gpx">⟦GPX track⟧</label>
  <div class="row"><input type="text" id="gpx" placeholder="⟦choose or drop a file on the window⟧"><button id="pickGpx">⟦Choose…⟧</button></div>
  <input type="file" id="file" accept=".gpx,.gz" hidden>
  <div class="row"><div><label for="track">⟦Track number when the file has several (empty = the program chooses)⟧</label><input type="number" id="track" min="1" step="1"></div></div>
  <div class="checks"><label><input type="checkbox" id="keep_times">⟦times from the file, even when they look fake⟧</label></div>
  <div class="row"><div><label for="distance">⟦Distance from the watch, km (empty = from the file)⟧</label><input type="number" id="distance" min="0" step="0.01"></div>
    <div><label for="ascent">⟦Ascent from the watch, m (empty = from the file)⟧</label><input type="number" id="ascent" min="0" step="1"></div></div>
  <label for="title">⟦Title (each line on its own; empty = name from the track)⟧</label><textarea id="title"></textarea>
  <label for="subtitle">⟦Subtitle (empty = date from the track)⟧</label><input type="text" id="subtitle">
  <div class="checks"><label><input type="checkbox" id="auto_title">⟦title from the place name⟧</label></div>
  <fieldset><legend>⟦Map⟧</legend>
    <div class="row">
      <div><label for="map">⟦Map background⟧</label><select id="map"><option value="terrain">⟦terrain⟧</option><option value="topo">⟦topographic⟧</option><option value="aerial">⟦aerial photos⟧</option><option value="satellite">⟦satellite⟧</option><option value="custom">⟦custom address⟧</option></select></div>
      <div><label for="map_style">⟦Color style⟧</label><select id="map_style"><option value="natural">⟦natural⟧</option><option value="night">⟦night⟧</option><option value="light">⟦light⟧</option><option value="gray">⟦gray⟧</option></select></div>
      <div style="flex:0 0 64px"><label for="color">⟦Route⟧</label><input type="color" id="color"></div>
    </div>
    <div id="urlbox" hidden><label for="map_url">⟦Tile address with {z}/{x}/{y} or WMS with {bbox}⟧</label><input type="text" id="map_url" placeholder="https://…/{z}/{x}/{y}.png"></div>
    <label for="color_by">⟦Route color by data⟧</label>
    <select id="color_by"><option value="">⟦one color⟧</option><option value="slope">⟦slope⟧</option><option value="speed">⟦speed⟧</option><option value="heart-rate">⟦heart rate⟧</option><option value="elevation">⟦elevation⟧</option></select>
    <div class="checks">
      <label><input type="checkbox" id="sunlight">⟦light as from the sun⟧</label>
      <label><input type="checkbox" id="minimap">⟦country minimap⟧</label>
      <label><input type="checkbox" id="background_names">⟦names in the background⟧</label>
      <label><input type="checkbox" id="km_markers">⟦kilometer markers⟧</label>
      <label><input type="checkbox" id="counters">⟦live counters⟧</label>
      <label><input type="checkbox" id="no_heart_rate">⟦no heart rate⟧</label>
    </div>
  </fieldset>
  <fieldset><legend>Film</legend>
    <div class="row">
      <div><label for="size">⟦Size⟧</label><select id="size"><option value="3840x2160">4K</option><option value="2560x1440">1440p</option><option value="1920x1080">1080p</option><option value="1280x720">720p</option></select></div>
      <div><label for="fps">⟦Frames/s⟧</label><select id="fps"><option>60</option><option>30</option></select></div>
      <div><label for="route_duration">⟦Route, s⟧</label><input type="number" id="route_duration" min="2" step="1"></div>
    </div>
    <div class="checks">
      <label><input type="checkbox" id="intro">⟦intro: camera flight from the country view⟧</label>
      <label><input type="checkbox" id="outro">⟦outro: summary card⟧</label>
    </div>
    <div class="row">
      <div><label for="intro_duration">⟦Flight time, s⟧</label><input type="number" id="intro_duration" min="1" step="0.5"></div>
      <div><label for="outro_duration">⟦Summary card, s⟧</label><input type="number" id="outro_duration" min="1" step="0.5"></div>
      <div><label for="stop_minutes">⟦Stops from, min⟧</label><input type="number" id="stop_minutes" min="0" step="1"></div>
    </div>
    <div class="row">
      <div><label for="intro_label_duration" title="⟦Time from the last label coming in until they start to fade, the same for the places and peaks of the region and for the peaks along the route.⟧">⟦How long places and peaks stay, s⟧</label><input type="number" id="intro_label_duration" min="0.5" max="20" step="0.5" placeholder="⟦default about 1 s⟧" title="⟦Time from the last label coming in until they start to fade, the same for the places and peaks of the region and for the peaks along the route.⟧"></div>
      <div><label for="intro_peaks">⟦Peaks in the intro⟧</label><input type="number" id="intro_peaks" min="0" step="1"></div>
      <div><label for="intro_places">⟦Places in the intro⟧</label><input type="number" id="intro_places" min="0" step="1"></div>
      <div><label for="intro_radius" title="⟦Radius around the start within which the intro looks for the highest peaks and the main places⟧">⟦Intro radius, km⟧</label><input type="number" id="intro_radius" min="0.5" max="100" step="0.5"></div>
    </div>
    <label for="music">⟦Music (sound file)⟧</label>
    <div class="row"><input type="text" id="music" placeholder="⟦empty = no sound⟧"><button data-pick="music" data-kind="file">⟦Choose…⟧</button></div>
  </fieldset>
  <fieldset><legend>⟦Photos on the route⟧</legend>
    <label for="photos">⟦Folder with photos⟧</label>
    <div class="row"><input type="text" id="photos" placeholder="⟦empty = no photos⟧"><button data-pick="photos" data-kind="dir">⟦Choose…⟧</button></div>
    <div class="row">
      <div><label for="photo_duration">⟦Photo time, s⟧</label><input type="number" id="photo_duration" min="0.5" step="0.5"></div>
      <div><label for="photo_limit">⟦At most (0 = all)⟧</label><input type="number" id="photo_limit" min="0" step="1"></div>
      <div><label for="photo_offset">⟦Clock correction, s⟧</label><input type="number" id="photo_offset" step="1"></div>
    </div>
  </fieldset>
  <fieldset><legend>⟦Labels⟧</legend>
    <div class="row">
      <div><label for="places">⟦Places along the route⟧</label><input type="number" id="places" min="0" step="1"></div>
      <div><label for="peaks">⟦Peaks⟧</label><input type="number" id="peaks" min="0" step="1"></div>
      <div><label for="language">⟦Language of the film texts⟧</label><select id="language"><option value="pl">⟦Polish⟧</option><option value="en">⟦English⟧</option></select></div>
    </div>
    <div class="row">
      <div><label for="name_language">⟦Language of the map names⟧</label><input type="text" id="name_language" placeholder="⟦e.g. it⟧"></div>
      <div><label for="timezone">⟦Time zone⟧</label><input type="text" id="timezone" placeholder="⟦e.g. Europe/Rome⟧"></div>
    </div>
    <div class="row">
      <div><label for="label_style">⟦Labels on the map⟧</label><select id="label_style"><option value="plain">⟦plain⟧</option><option value="strong">⟦strong⟧</option><option value="badges">⟦on badges⟧</option></select></div>
      <div><label for="label_size">⟦Label size⟧</label><input type="number" id="label_size" min="0.7" max="2" step="0.05"></div>
    </div>
    <label for="label_file">⟦File with your own labels⟧</label>
    <div class="row"><input type="text" id="label_file" placeholder="⟦lines: 3.2 km Name or 12:39 Name⟧"><button data-pick="label_file" data-kind="file">⟦Choose…⟧</button></div>
    <label for="skip">⟦Skip places (part of the name, one per line)⟧</label><textarea id="skip" style="min-height:52px"></textarea>
    <label for="rename">⟦Rename (old=new, one per line)⟧</label><textarea id="rename" style="min-height:52px"></textarea>
  </fieldset>
  <fieldset><legend>⟦Font and logo⟧</legend>
    <label for="font">⟦Custom title font (TTF file)⟧</label>
    <div class="row"><input type="text" id="font" placeholder="⟦empty = default⟧"><button data-pick="font" data-kind="file">⟦Choose…⟧</button></div>
    <label for="logo">⟦Logo in the corner (PNG file)⟧</label>
    <div class="row"><input type="text" id="logo" placeholder="⟦empty = no logo⟧"><button data-pick="logo" data-kind="file">⟦Choose…⟧</button></div>
  </fieldset>
  <fieldset><legend>⟦Output⟧</legend>
    <label for="output">⟦Film file⟧</label><input type="text" id="output" placeholder="⟦by default next to the GPX track⟧">
    <label for="slides">⟦Folder for slide frames⟧</label>
    <div class="row"><input type="text" id="slides" placeholder="⟦empty = no frames⟧"><button data-pick="slides" data-kind="dir">⟦Choose…⟧</button></div>
    <div class="row">
      <div><label for="crf">⟦Quality (CRF, lower = better)⟧</label><input type="number" id="crf" min="0" max="40" step="1"></div>
      <div><label for="encoder">⟦ffmpeg encoder⟧</label><input type="text" id="encoder" placeholder="⟦automatic⟧"></div>
    </div>
    <div class="checks"><label><input type="checkbox" id="no_osm">⟦no OpenStreetMap data⟧</label></div>
  </fieldset>
</aside>
<main>
  <div class="stage"><img id="img" alt="⟦Preview of the last frame of the film⟧"><video id="vid" controls playsinline hidden></video><p id="hint">⟦Choose a GPX track or drop a file on this window. The preview of the last frame of the film will appear here.⟧</p></div>
  <div class="actions"><button id="prev">⟦Refresh preview⟧</button><button id="draft">⟦Film preview⟧</button><button id="go" class="go">⟦Render film⟧</button><button id="stop" hidden>⟦Stop⟧</button><button id="show" hidden>⟦Show file⟧</button>
    <label class="auto"><input type="checkbox" id="auto" checked>⟦refresh after every change⟧</label><span id="msg" role="status"></span></div>
  <div class="bar"><i id="bar"></i></div>
  <p class="note" id="info">⟦The image above is the last frame of the route. “Film preview”, a quick 720p draft, shows the intro, photos, stops and outro.⟧</p>
  <div id="chips"></div>
  <pre id="log">⟦The command for these settings and the messages of the program will appear here.⟧</pre>
</main>
</div>
<script>
const K=new URLSearchParams(location.search).get('k'),INIT=__INIT__,$=id=>document.getElementById(id);
const VALS=['gpx','title','subtitle','map','map_url','map_style','color','color_by','size','fps','route_duration','intro_duration','intro_label_duration','intro_peaks','intro_places','intro_radius','outro_duration','stop_minutes','music','photos','photo_duration','photo_limit','photo_offset',
  'places','peaks','language','name_language','timezone','label_style','label_size','label_file','skip','rename','font','logo','output','slides','crf','encoder','track','distance','ascent'];
const FLAGS=['auto_title','sunlight','minimap','background_names','km_markers','counters','no_heart_rate','intro','outro','no_osm','keep_times'];
const STILL_SAME=['output','slides','music','crf','encoder','fps','size'];   // fields that do not change the preview
if(INIT.map&&/\{z\}|\{bbox\}/.test(String(INIT.map))){INIT.map_url=INIT.map;INIT.map='custom';}
for(const k of VALS){const v=INIT[k];if(v!=null&&v!=='')$(k).value=k==='title'?String(v).replaceAll('\\n','\n'):v;}
for(const k of FLAGS)$(k).checked=!!INIT[k];
const opts=()=>{const o={};for(const k of VALS)o[k]=$(k).value.trim();for(const k of FLAGS)o[k]=$(k).checked;
  if(o.map==='custom')o.map=o.map_url;delete o.map_url;return o;};
const urlbox=()=>{$('urlbox').hidden=$('map').value!=='custom';};
$('map').addEventListener('change',urlbox);urlbox();
const post=(p,body,q='')=>fetch(p+'?k='+K+q,{method:'POST',body}).then(r=>r.json());
const say=t=>{$('msg').textContent=t;};
const fmt=(t,v)=>t.replace(/\{(\w+)\}/g,(m,k)=>k in v?v[k]:m);   // fills the {fields} of a translated text
const mmss=s=>Math.floor(s/60)+':'+String(Math.round(s%60)).padStart(2,'0');
document.querySelectorAll('button[data-pick]').forEach(b=>{b.onclick=async()=>{const r=await post('/pick',null,'&kind='+b.dataset.kind+'&field='+b.dataset.pick);if(r.path){$(b.dataset.pick).value=r.path;changed(b.dataset.pick);}else say('⟪Type the path by hand.⟫');};});
const forget=()=>{for(const k of ['track','distance','ascent'])$(k).value='';$('keep_times').checked=false;};   // these belong to one file
$('gpx').addEventListener('input',forget);
function setGpx(p,out){
  $('gpx').value=p;forget();
  if(!$('output').value||$('output').dataset.auto){$('output').value=out||p.replace(/\.[^.\/]+$/,'')+'.mp4';$('output').dataset.auto='1';}
  preview();
}
$('output').addEventListener('input',()=>{delete $('output').dataset.auto;});
async function upload(f){const r=await post('/upload',f,'&name='+encodeURIComponent(f.name));setGpx(r.path,r.out);}
$('pickGpx').onclick=async()=>{const r=await post('/pick',null,'&kind=file');if(r.path)setGpx(r.path);else $('file').click();};
$('file').onchange=e=>{if(e.target.files[0])upload(e.target.files[0]);};
addEventListener('dragover',e=>e.preventDefault());
addEventListener('drop',e=>{e.preventDefault();const f=e.dataTransfer.files[0];if(f)upload(f);});

let busy=false,dirty=false,timer=null,deb=null,outPath='';
const LOG_KEEP=200;let logHead='',logLines=[],logSeen=0,stick=true;   // the command, the messages shown, how many came so far
const atBottom=()=>{const l=$('log');return l.scrollHeight-l.scrollTop-l.clientHeight<4;};
$('log').addEventListener('scroll',()=>{stick=atBottom();});   // only the reader scrolls; a box that gets smaller does not
new ResizeObserver(()=>{if(stick)$('log').scrollTop=$('log').scrollHeight;}).observe($('log'));
function showLog(fresh){                               // new lines follow the bottom, unless the box was scrolled up to read
  const l=$('log'),follow=fresh||stick,top=l.scrollTop;
  if(follow&&logLines.length>LOG_KEEP)logLines=logLines.slice(-LOG_KEEP);   // old lines go only when nobody reads them
  l.textContent=logHead+(logHead&&logLines.length?'\n\n':'')+logLines.join('\n');
  l.scrollTop=follow?l.scrollHeight:top;
  if(follow)stick=true;
}
function newLog(head,lines){logHead=head;logLines=lines;logSeen=0;showLog(true);}
const STAGE_MIN=240;                                   // px: the lowest preview; below it the right panel scrolls
function fitStage(){                                   // a low window makes the preview smaller, so that the buttons and
  const m=document.querySelector('main'),st=document.querySelector('.stage'),l=$('log');   // 10 lines of messages fit under it
  if(innerWidth<=900){st.style.width='';return;}
  const cs=getComputedStyle(m),items=[...m.children].filter(e=>getComputedStyle(e).display!=='none');
  const other=items.filter(e=>e!==st&&e!==l).reduce((h,e)=>h+e.getBoundingClientRect().height,0);
  const room=m.clientHeight-parseFloat(cs.paddingTop)-parseFloat(cs.paddingBottom)-other-parseFloat(getComputedStyle(l).minHeight)
    -(items.length-1)*(parseFloat(cs.rowGap)||0);
  const h=Math.floor(Math.max(STAGE_MIN,Math.min(room,innerHeight*0.66)));
  st.style.width=Math.min(m.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight),h*16/9)+'px';
}
const fit=new ResizeObserver(fitStage);
for(const e of [document.querySelector('main'),document.querySelector('.actions'),$('info'),$('chips')])fit.observe(e);
function changed(id){                                  // a setting changed: refresh the preview after a moment
  if(STILL_SAME.includes(id)||!$('auto').checked||!$('gpx').value.trim())return;
  clearTimeout(deb);deb=setTimeout(preview,900);
}
document.querySelector('aside').addEventListener('input',e=>changed(e.target.id));
document.querySelector('aside').addEventListener('change',e=>changed(e.target.id));
function addLine(id,text){const t=$(id);t.value=(t.value.trim()?t.value.trim()+'\n':'')+text;}
function report(r){
  if(!r)return;
  const bits=[fmt('⟪the film will last about {time}⟫',{time:mmss(r.duration)}),fmt('⟪route {km} km⟫',{km:String(r.distance_km).replace('.',INIT.decimal)})];
  if(r.photos.total)bits.push(fmt('⟪photos on the route: {matched} of {total}⟫',{matched:r.photos.matched,total:r.photos.total}));
  if(r.stops)bits.push(fmt('⟪stops: {n}⟫',{n:r.stops}));
  if(r.country)bits.push(r.country);
  $('info').textContent=fmt('⟪{facts}.  Labels below: click a name to change it, or × to skip it.⟫',{facts:bits.join('  ·  ')});
  const box=$('chips');box.textContent='';
  for(const name of [...r.places.map(m=>m.name),...r.peaks]){
    const chip=document.createElement('span');chip.className='chip';
    const lab=document.createElement('button');lab.textContent=name;lab.title='⟪Rename⟫';
    lab.onclick=()=>{const n=prompt('⟪New name on the film:⟫',name);if(n&&n.trim()&&n!==name){addLine('rename',name+'='+n.trim());preview();}};
    const del=document.createElement('button');del.textContent='×';del.title='⟪Skip this place⟫';del.setAttribute('aria-label',fmt('⟪Skip {name}⟫',{name}));
    del.onclick=()=>{addLine('skip',name);preview();};
    chip.append(lab,del);box.append(chip);
  }
}
async function preview(){
  clearTimeout(deb);
  if(busy){dirty=true;return;}
  if(!$('gpx').value.trim()){say('⟪Choose a GPX track first.⟫');return;}
  busy=true;$('prev').disabled=true;say('⟪Drawing the preview…⟫');
  try{
    const r=await post('/preview',JSON.stringify(opts()));
    if(r.ok){$('vid').pause();$('vid').hidden=true;$('img').src='/preview.png?k='+K+'&t='+Date.now();$('img').style.display='block';$('hint').style.display='none';report(r.report);}
    const lines=r.log.trim()?r.log.trim().split('\n'):[];
    if(timer){                                       // a film is being made: its messages stay in the box
      say(r.ok?'⟪Preview ready.⟫':fmt('⟪Preview failed: {error}⟫',{error:lines.slice(-1)[0]||'⟪no details⟫'}));
    }else{newLog(r.cmd,lines);say(r.ok?'⟪Preview ready.⟫':'⟪Preview failed. Details below.⟫');}
  }catch(e){say('⟪No connection to the program. Is the terminal still open?⟫');}
  finally{busy=false;$('prev').disabled=false;if(dirty){dirty=false;preview();}}
}
$('prev').onclick=preview;
async function start(draft){
  if(!$('gpx').value.trim()){say('⟪Choose a GPX track first.⟫');return;}
  const r=await post('/render',JSON.stringify({...opts(),draft}));
  if(!r.ok){say(r.log);return;}
  outPath=r.out;newLog(r.cmd,[]);$('bar').style.width='0';$('go').disabled=$('draft').disabled=true;$('stop').hidden=false;$('show').hidden=true;
  say(draft?'⟪Preparing the film preview…⟫':'⟪Rendering the film…⟫');
  timer=setInterval(poll,700);
}
$('go').onclick=()=>start(false);
$('draft').onclick=()=>start(true);
async function poll(){
  const s=await fetch('/status?k='+K).then(r=>r.json());
  $('bar').style.width=s.pct+'%';
  const fresh=s.n-logSeen;
  if(fresh>0){logLines.push(...s.log.slice(-Math.min(fresh,s.log.length)));logSeen=s.n;showLog(false);}
  if(s.running)say(fmt(s.draft?'⟪Preparing the film preview… {pct}%⟫':'⟪Rendering the film… {pct}%⟫',{pct:s.pct}));
  if(s.done){
    clearInterval(timer);timer=null;$('go').disabled=$('draft').disabled=false;$('stop').hidden=true;
    if(!s.ok){say('⟪Rendering failed. Details below.⟫');return;}
    if(s.draft){const v=$('vid');v.src='/draft.mp4?k='+K+'&t='+Date.now();v.hidden=false;$('img').style.display='none';$('hint').style.display='none';v.play().catch(()=>{});say('⟪Film preview ready (720p draft).⟫');}
    else{say(fmt('⟪Done: {file}⟫',{file:s.out}));$('show').hidden=false;}
  }
}
$('stop').onclick=()=>post('/cancel','{}');
$('show').onclick=()=>post('/open',JSON.stringify({path:outPath}));
if($('gpx').value.trim())preview();
</script></body></html>"""


def run_gui(A, defaults: dict) -> None:
    """Panel in the browser: settings form, preview of the last frame and film rendering with a progress bar."""
    import secrets
    import shlex
    import tempfile
    import threading
    import webbrowser
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    token, tmp = secrets.token_urlsafe(12), Path(tempfile.mkdtemp(prefix="gpxfilm-"))
    script = [sys.executable, "-m", "gpxfilm"]        # the same program, also when it runs from a source checkout
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(Path(__file__).resolve().parent.parent), os.environ.get("PYTHONPATH")])))
    state = dict(running=False, done=False, ok=False, pct=0, out="", msgs=Messages(), proc=None, draft=False)
    memo = CACHE / "gui.json"                         # the window's last used settings
    VALS = [("title", "--title"), ("subtitle", "--subtitle"), ("size", "--size"), ("fps", "--fps"), ("route_duration", "--route-duration"), ("color", "--color"),
            ("map", "--map"), ("map_style", "--map-style"), ("color_by", "--color-by"), ("intro_duration", "--intro-duration"), ("photos", "--photos"),
            ("intro_label_duration", "--intro-label-duration"), ("intro_peaks", "--intro-peaks"), ("intro_places", "--intro-places"), ("intro_radius", "--intro-radius"),
            ("photo_duration", "--photo-duration"), ("photo_limit", "--photo-limit"), ("name_language", "--name-language"), ("timezone", "--timezone"), ("places", "--places"),
            ("peaks", "--peaks"), ("language", "--language"), ("label_style", "--label-style"), ("label_size", "--label-size"), ("photo_offset", "--photo-offset"), ("stop_minutes", "--stop-minutes"), ("label_file", "--label-file"),
            ("font", "--font"), ("logo", "--logo"), ("outro_duration", "--outro-duration"), ("crf", "--crf"), ("encoder", "--encoder"),
            ("music", "--music"), ("slides", "--slides"), ("track", "--track"),
            ("distance", "--distance"), ("ascent", "--ascent")]
    FLAGS = ["intro", "outro", "counters", "no_heart_rate", "km_markers", "minimap", "background_names", "sunlight", "auto_title", "no_osm", "keep_times"]
    keys = set(dict(VALS)) | set(FLAGS) | {"gpx", "output", "rename", "skip"}
    ONE_FILE = {"track", "keep_times", "distance", "ascent"}   # chosen for one file, not remembered
    conv = lambda v: str(v) if isinstance(v, Path) else "\n".join(v) if isinstance(v, list) else v
    init = {k: conv(v) for k, v in vars(A).items() if k in keys}
    try:                                              # remembered settings come back unless the command gives them now
        for k, v in upgrade(json.loads(memo.read_text(encoding="utf-8")))[0].items():   # also settings remembered under the old names
            if k in keys - ONE_FILE and conv(vars(A).get(k)) == conv(defaults.get(k)) and not (k in ("gpx", "photos", "label_file", "font", "logo", "music") and v and not Path(v).exists()):
                init[k] = v
    except Exception:  # noqa: BLE001
        pass

    def remember(o: dict) -> None:
        try:
            memo.parent.mkdir(parents=True, exist_ok=True)
            memo.write_text(json.dumps({k: v for k, v in o.items() if k in keys and k not in ONE_FILE}, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def build(o: dict, preview: bool = False, draft: bool = False) -> list[str]:   # form settings → command arguments
        a = [str(o.get("gpx", ""))]
        for key, flag in VALS:
            if key == "track" and not str(o.get(key) or "").isdecimal():   # empty, or a half-typed number
                continue
            if o.get(key) not in (None, "", False) and not (preview and key in ("music", "slides")) and not (draft and key == "slides"):
                a += [flag, str(o[key])]
        for key in ("rename", "skip"):                # multi-line fields: one option per line
            a += [x for ln in str(o.get(key) or "").splitlines() if ln.strip() for x in ("--" + key, ln.strip())]
        return a + ["--" + k.replace("_", "-") for k in FLAGS if o.get(k)]

    def pick(kind: str, field: str = "") -> str:      # the system's file or directory chooser
        try:
            if sys.platform == "darwin":
                ask = _("Choose a file") if kind != "dir" else _("Choose the folder with photos") if field == "photos" else _("Choose a folder")
                what = f'choose {"folder" if kind == "dir" else "file"} with prompt "{ask}"'
                r = subprocess.run(["osascript", "-e", "tell me to activate", "-e", f"POSIX path of ({what})"], capture_output=True, text=True)
            elif shutil.which("zenity"):
                r = subprocess.run(["zenity", "--file-selection", "--title=gpxfilm"] + (["--directory"] if kind == "dir" else []), capture_output=True, text=True)
            elif shutil.which("kdialog"):
                r = subprocess.run(["kdialog", "--getexistingdirectory" if kind == "dir" else "--getopenfilename", str(Path.home())], capture_output=True, text=True)
            else:
                return ""
            return r.stdout.strip()
        except Exception:  # noqa: BLE001 – without a chooser the path is typed by hand
            return ""

    def worker(args: list[str], msgs: Messages) -> None:   # the film is made by the same command as in the terminal
        p = subprocess.Popen([*script, *args], stderr=subprocess.PIPE, text=True, env=env)
        state["proc"] = p
        for line in p.stderr:
            msgs.add(line)
            state["pct"] = msgs.pct
        p.wait()
        state.update(running=False, done=True, ok=p.returncode == 0, pct=100 if p.returncode == 0 else state["pct"], proc=None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):                    # no line in the terminal for every request
            pass

        def reply(self, body, ctype="application/json", code=200):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def route(self):
            u = urllib.parse.urlparse(self.path)
            q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            return u.path, q, q.get("k") == token

        def do_GET(self):
            path, q, ok = self.route()
            if not ok:
                return self.reply({"error": _("access denied")}, code=403)
            if path == "/":
                t = ui()
                page = localize(GUI_PAGE, t).replace("__LANG__", t.language)
                page = page.replace("__INIT__", json.dumps(init | {"decimal": t.pgettext(*DECIMAL)}))
                return self.reply(page.encode(), "text/html; charset=utf-8")
            if path == "/preview.png" and (tmp / "preview.png").exists():
                return self.reply((tmp / "preview.png").read_bytes(), "image/png")
            if path == "/status":
                return self.reply({k: state[k] for k in ("running", "done", "ok", "pct", "out", "draft")} | state["msgs"].tail())
            if path == "/draft.mp4" and (tmp / "draft.mp4").exists():      # the browser's player asks for parts of the file
                data, m = (tmp / "draft.mp4").read_bytes(), re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
                if not m:
                    return self.reply(data, "video/mp4")
                a = int(m[1]) if m[1] else 0
                b = min(int(m[2]) if m[2] else len(data) - 1, len(data) - 1)
                self.send_response(206)
                for h, v in (("Content-Type", "video/mp4"), ("Accept-Ranges", "bytes"), ("Content-Range", f"bytes {a}-{b}/{len(data)}"), ("Content-Length", str(b - a + 1))):
                    self.send_header(h, v)
                self.end_headers()
                self.wfile.write(data[a:b + 1])
                return None
            self.reply({}, code=404)

        def do_POST(self):
            path, q, ok = self.route()
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if not ok:
                return self.reply({"error": _("access denied")}, code=403)
            if path == "/pick":
                return self.reply({"path": pick(q.get("kind", "file"), q.get("field", ""))})
            if path == "/upload":                     # a file dropped on the window or chosen in the browser
                p = tmp / Path(q.get("name", "track.gpx")).name
                p.write_bytes(raw)
                return self.reply({"path": str(p), "out": str(Path.home() / (p.stem + ".mp4"))})
            o = json.loads(raw or b"{}")
            if path == "/preview":
                remember(o)
                rp = tmp / "report.json"
                rp.unlink(missing_ok=True)
                r = subprocess.run([*script, *build(o, True), "--frame-only", str(tmp / "preview.png"), "--size", "1280x720", "--report", str(rp)], capture_output=True, text=True, env=env)
                rep_ = json.loads(rp.read_text(encoding="utf-8")) if r.returncode == 0 and rp.exists() else None
                msgs = Messages()
                for line in r.stderr.splitlines():
                    msgs.add(line)
                return self.reply({"ok": r.returncode == 0, "log": "\n".join(msgs.tail()["log"]), "cmd": "python -m gpxfilm " + shlex.join(build(o)), "report": rep_})
            if path == "/render":
                if state["running"]:
                    return self.reply({"ok": False, "log": _("A film is already being rendered.")})
                remember(o)
                draft = bool(o.get("draft"))
                out = str(tmp / "draft.mp4") if draft else o.get("output") or str(Path(o.get("gpx") or "film").with_suffix(".mp4"))
                args = build(o, draft=draft) + (["--draft"] if draft else []) + ["-o", out]
                msgs = Messages()                     # the new job is running before the reply, so no poll sees the old one as done
                state.update(running=True, done=False, ok=False, pct=0, out=out, msgs=msgs, draft=draft)
                threading.Thread(target=worker, args=(args, msgs), daemon=True).start()
                return self.reply({"ok": True, "out": out, "cmd": "python -m gpxfilm " + shlex.join(args)})
            if path == "/cancel":
                if state["proc"]:
                    state["proc"].terminate()
                return self.reply({"ok": True})
            if path == "/open":
                tgt = Path(o.get("path", ""))
                if tgt.exists():
                    subprocess.Popen(["open", "-R", str(tgt)] if sys.platform == "darwin" else ["xdg-open", str(tgt.parent)])
                return self.reply({"ok": True})
            self.reply({}, code=404)

    srv = ThreadingHTTPServer(("127.0.0.1", A.port), Handler)
    url = f"http://127.0.0.1:{srv.server_address[1]}/?k={token}"
    log(_("Settings window: {url}\nTo close it, press Ctrl+C in this terminal.").format(url=url))
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
