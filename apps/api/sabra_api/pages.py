"""Page shell and the pages added with the browser-first workflow.

`page_shell` is the one place that wraps a page's title, navigation and body, and every
HTML page goes through it. It deliberately contains no `<script>`: each page's own script
is the first (and only) script in the document, which is what the offline JavaScript
tests rely on.

Every dynamic string a page script renders goes through `esc()`.
"""

from __future__ import annotations

from html import escape

# (label, path). The path is also the key `page_shell(active=...)` matches.
NAV_LINKS: tuple[tuple[str, str], ...] = (
    ("Today", "/"),
    ("Opportunities", "/opportunities"),
    ("Applications", "/applications"),
    ("Pipeline", "/pipeline"),
    ("Candidate", "/candidate"),
    ("Searches", "/searches"),
    ("Answers", "/answers"),
    ("Settings", "/settings"),
)

SHELL_CSS = (
    ":root{--ink:#18332f;--muted:#6c7f7a;--paper:#f4f7f3;--panel:#fff;--line:#dce6df;"
    "--sage:#62877a;--pale:#e6f0ea;--warn:#b3261e}"
    "*{box-sizing:border-box}"
    "body{margin:0;background:var(--paper);color:var(--ink);"
    'font:15px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}'
    ".topnav{display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:12px 24px;"
    "background:#edf4ef;border-bottom:1px solid var(--line)}"
    ".topnav .brand{font-size:20px;font-weight:750;letter-spacing:-.04em;margin-right:8px}"
    ".topnav nav{display:flex;gap:4px;flex-wrap:wrap}"
    ".topnav nav a{padding:7px 12px;border-radius:8px;color:var(--muted);text-decoration:none;"
    "font-weight:600}"
    ".topnav nav a:hover,.topnav nav a.active{background:var(--panel);color:var(--ink);"
    "box-shadow:0 1px 4px #18332f10}"
    ".wrap{max-width:960px;margin:0 auto;padding:32px 24px 64px}"
    ".wrap h1{font-size:34px;letter-spacing:-.04em;margin:8px 0 6px}"
    ".wrap h2{font-size:18px;margin:0 0 10px;letter-spacing:-.02em}"
    ".wrap a{color:var(--sage);font-weight:700;text-decoration:none}"
    ".muted,.wrap p{color:var(--muted)}"
    ".card{background:var(--panel);border:1px solid var(--line);border-radius:12px;"
    "padding:20px;margin:16px 0}"
    ".btn,.wrap button{background:var(--ink);color:#fff;border:0;border-radius:8px;"
    "padding:9px 15px;font-weight:700;cursor:pointer;font:inherit;font-weight:700}"
    ".wrap button:disabled{background:#9aada6;cursor:not-allowed}"
    ".wrap button.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}"
    ".wrap input,.wrap select,.wrap textarea{width:100%;padding:8px;border:1px solid #cbd9d1;"
    "border-radius:7px;font:inherit;background:#fff;color:var(--ink)}"
    ".wrap label{display:block;color:var(--muted);font-size:13px;margin:10px 0 4px}"
    ".wrap label.check{display:inline-flex;align-items:center;gap:6px;margin:6px 14px 6px 0;"
    "color:var(--ink);font-size:14px}"
    ".wrap label.check input{width:auto}"
    ".grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}"
    ".grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:0 18px}"
    ".pill{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12px;"
    "font-weight:700;background:var(--pale);color:var(--ink)}"
    ".pill.VERIFIED{background:#d7efe0}.pill.INFERRED{background:#f4eadb}"
    ".pill.UNKNOWN{background:#eee}"
    ".wrap [hidden]{display:none}"
    ".line{color:var(--muted);min-height:1.4em}"
    ".err{color:var(--warn)}"
    "@media(max-width:700px){.grid2,.grid3{grid-template-columns:1fr}.wrap{padding:20px 16px}}"
)

ESC_JS = (
    "function esc(s){return String(s??'').replace(/[&<>\"']/g,"
    "c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));}"
    "async function readBody(r){const text=await r.text();let d={};"
    "try{d=JSON.parse(text)}catch(e){d={detail:text}}return {ok:r.ok,d,text};}"
    "function detailOf(b,fallback){const d=b.d&&b.d.detail;"
    "if(typeof d==='string')return d;"
    "if(Array.isArray(b.d&&b.d.errors)&&b.d.errors.length)"
    "return b.d.errors.map(e=>e.field+': '+e.message).join('; ');"
    "return b.text||fallback;}"
)


def page_shell(title: str, body: str, *, active: str, css: str = "") -> str:
    """Wrap a page body in the shared head and navigation.

    `active` is the nav path to highlight (for example "/opportunities"). `css` is
    page-specific CSS appended after the shared styles.
    """

    current = ' class="active" aria-current="page"'
    links = "".join(
        f'<a href="{path}"{current if path == active else ""}>{escape(label)}</a>'
        for label, path in NAV_LINKS
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Sabra | {escape(title)}</title>"
        f"<style>{SHELL_CSS}{css}</style></head>\n"
        '<body><header class="topnav"><span class="brand">Sabra</span>'
        f'<nav aria-label="Main">{links}</nav></header>\n{body}</body></html>'
    )


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------

TODAY_CSS = (
    ".attention{list-style:none;margin:0;padding:0}"
    ".attention li{padding:12px 0;border-top:1px solid var(--line);font-size:17px}"
    ".attention li:first-child{border-top:0}"
    ".attention strong{font-size:24px;margin-right:6px}"
    ".metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}"
    ".metric{padding:14px 18px;background:var(--panel);border:1px solid var(--line);"
    "border-radius:12px}.metric-label{color:var(--muted);font-size:12px}"
    ".metric-value{font-size:26px;font-weight:750}"
    ".queue-item{display:flex;justify-content:space-between;gap:18px;padding:12px 0;"
    "border-top:1px solid var(--line)}.queue-item span{color:var(--muted);font-size:13px}"
    ".score{color:var(--sage);font-weight:750}"
    ".stage{padding:6px 0;border-top:1px solid var(--line)}.stage:first-child{border-top:0}"
    "@media(max-width:700px){.metrics{grid-template-columns:repeat(2,1fr)}}"
)

TODAY_BODY = """<main class="wrap">
<h1>What requires my attention</h1>
<p id="today-sub">Loading...</p>
<section class="card"><ul class="attention" id="attention"><li>Loading...</li></ul>
<p class="line" id="seen-line"></p></section>
<section class="card" id="run-panel"><h2>Run the search</h2>
<p id="run-last">Loading...</p>
<label class="check"><input type="checkbox" id="run-prepare"> Also prepare packages for the top roles (nothing is submitted)</label><br>
<button type="button" id="run-now">Run now</button>
<div id="run-progress"></div><p class="line" id="run-line"></p></section>
<section class="metrics" id="metrics">
<div class="metric"><div class="metric-label">Jobs discovered</div><div class="metric-value" data-key="jobs_discovered">-</div></div>
<div class="metric"><div class="metric-label">Opportunities</div><div class="metric-value" data-key="opportunities">-</div></div>
<div class="metric"><div class="metric-label">Shortlisted</div><div class="metric-value" data-key="shortlisted">-</div></div>
<div class="metric"><div class="metric-label">Packages ready</div><div class="metric-value" data-key="packages_latest">-</div></div>
</section>
<section class="card" id="opportunities"><h2>Opportunity queue</h2>
<div id="overview-queue"><div class="queue-item"><div><strong>Loading the opportunity queue...</strong></div></div></div>
<p><a href="/opportunities">See the full queue</a></p></section>
<p class="line" id="status-line"></p>
</main>
<script>
__ESC__
// Each count links to the page that acts on it; the Applications page reads ?filter= and
// shows just the rows the count is made of.
const ATTENTION=[
  ['ready_for_approval','packages ready for approval','/applications?filter=ready'],
  ['needs_information','applications need more information','/applications?filter=needs_information'],
  ['handoffs_waiting','submissions waiting for your report','/applications?filter=handoffs'],
  ['new_high_fit','new high-fit opportunities since the last run','/opportunities'],
  ['employer_responses','employer responses since you last looked','/pipeline']
];
let pollTimer=null;
function renderRun(run){
  if(!run)return '';
  const p=run.progress||{};
  const stages=Object.entries(p.stages||{}).map(([name,st])=>{
    const counts=Object.entries(st).filter(([k,v])=>typeof v==='number').map(([k,v])=>esc(k.replace(/_/g,' '))+' '+esc(v)).join(' · ');
    return `<div class="stage"><strong>${esc(name)}</strong> ${esc(st.status||'')}${counts?' · '+counts:''}</div>`;
  }).join('');
  const sources=(p.sources||[]).map(s=>`<div class="stage">${esc(s.display_name||s.source_id)}: ${s.ok?'ok':'FAILED'}, ${esc(s.jobs_seen)} seen, ${esc(s.jobs_new)} new${s.error?' · '+esc(s.error):''}</div>`).join('');
  const warn=(p.warnings||[]).map(w=>`<div class="stage">${esc(w)}</div>`).join('');
  const err=run.error?`<div class="stage err">${esc(run.error)}</div>`:'';
  return `<div><strong>${esc(run.status)}</strong> · ${esc(run.kind)}${p.summary?' · '+esc(p.summary):''}</div>${stages}${warn}${err}<details><summary>Sources (${(p.sources||[]).length})</summary>${sources||'<div class="stage">None yet.</div>'}</details>`;
}
function renderToday(d){
  const c=d.counts||{};
  const items=ATTENTION.filter(a=>c[a[0]]>0).map(a=>{
    const extra=a[0]==='needs_information'&&d.open_questions?` <span class="muted">(${esc(d.open_questions)} open questions)</span>`:'';
    return `<li><a href="${a[2]}"><strong>${esc(c[a[0]])}</strong> ${esc(a[1])}</a>${extra}</li>`;
  });
  const hints=[];
  if(d.has_resume===false)hints.push('<li><a href="/candidate">Upload your resume</a> so packages can be prepared.</li>');
  document.querySelector('#attention').innerHTML=items.concat(hints).join('')||'<li>Nothing needs your attention right now.</li>';
  document.querySelector('#today-sub').textContent=(d.search_profile_name?`Searching: ${d.search_profile_name}. `:'')+(d.automation_dry_run===false?'Real submission is ON.':'Dry run is on: nothing is submitted.');
  const last=d.active_run||d.last_run;
  document.querySelector('#run-last').textContent=last?'':'No run yet. Press Run now to search.';
  document.querySelector('#run-progress').innerHTML=renderRun(last);
  document.querySelector('#run-now').disabled=!!d.active_run;
  if(c.employer_responses>0){document.querySelector('#seen-line').innerHTML='<button type="button" class="secondary" id="mark-seen">Mark responses as seen</button>';const b=document.querySelector('#mark-seen');if(b)b.addEventListener('click',markSeen);}
  else document.querySelector('#seen-line').textContent='';
  if(d.active_run)poll(d.active_run.id);
}
async function markSeen(){await fetch('/api/today/seen',{method:'POST'});loadToday();}
async function loadToday(){
  const sub=document.querySelector('#today-sub');
  try{
    const b=await readBody(await fetch('/api/today'));
    if(!b.ok)throw new Error(detailOf(b,'The overview could not be loaded.'));
    renderToday(b.d);
  }catch(error){sub.textContent=error.message;}
}
function poll(id){
  if(pollTimer)return;
  pollTimer=setInterval(async()=>{
    const b=await readBody(await fetch('/api/runs/'+encodeURIComponent(id)));
    if(!b.ok)return;
    document.querySelector('#run-progress').innerHTML=renderRun(b.d);
    if(b.d.status==='COMPLETED'||b.d.status==='FAILED'){clearInterval(pollTimer);pollTimer=null;document.querySelector('#run-now').disabled=false;loadToday();refresh();}
  },2000);
}
async function runNow(){
  const line=document.querySelector('#run-line');
  line.textContent='';
  const prepare=!!(document.querySelector('#run-prepare')||{}).checked;
  const b=await readBody(await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'full',prepare:prepare})}));
  if(!b.ok){line.textContent=detailOf(b,'The run could not start.');return;}
  document.querySelector('#run-now').disabled=true;
  document.querySelector('#run-progress').innerHTML=renderRun(b.d);
  poll(b.d.id);
}
async function loadQueue(){
  const q=document.querySelector('#overview-queue');
  try{
    const r=await fetch('/api/opportunities');const text=await r.text();
    if(!r.ok)throw new Error(text||('status '+r.status));
    const rows=(JSON.parse(text).opportunities||[]).slice(0,5);
    if(!rows.length){q.innerHTML='<div class="queue-item"><div><strong>No shortlisted opportunities yet.</strong><span>Press Run now to search.</span></div></div>';return;}
    q.innerHTML=rows.map(o=>`<div class="queue-item"><div><strong>${esc(o.rank_position??'')}. ${esc(o.title)}</strong><span>${esc(o.company)} · ${esc(o.location||'Location not listed')}</span></div><span class="score">${o.fit_score==null?'Fit pending':`Fit ${Math.round(o.fit_score*100)}%`}</span></div>`).join('');
  }catch(error){q.innerHTML='<div class="queue-item"><div><strong>The opportunity queue could not be loaded.</strong><span></span></div></div>';q.querySelector('span').textContent=error.message;}
}
async function refresh(){
  const line=document.querySelector('#status-line');
  try{
    const r=await fetch('/api/status');const text=await r.text();
    if(!r.ok)throw new Error(text||('status '+r.status));
    const d=JSON.parse(text);
    document.querySelectorAll('[data-key]').forEach(e=>e.textContent=d[e.dataset.key]??0);
    line.textContent='Updated '+new Date(d.generated_at).toLocaleTimeString()+'.';
  }catch(e){line.textContent='The local engine could not be reached. '+e.message;}
  await loadQueue();
}
document.querySelector('#run-now').addEventListener('click',runNow);
loadToday();refresh();
</script>
""".replace("__ESC__", ESC_JS)


# ---------------------------------------------------------------------------
# Candidate
# ---------------------------------------------------------------------------

IDENTITY_FORM_FIELDS = (
    ("first_name", "First name", 'autocomplete="given-name"'),
    ("last_name", "Last name", 'autocomplete="family-name"'),
    ("email", "Email", 'type="email" autocomplete="email"'),
    ("phone", "Phone", 'autocomplete="tel"'),
    ("street_address", "Street address", 'autocomplete="street-address"'),
    ("city", "City", ""),
    ("state", "State", ""),
    ("postal_code", "Postal code", ""),
    ("country", "Country", ""),
    ("linkedin_url", "LinkedIn URL", ""),
    ("headline", "Preferred headline", ""),
)


def _identity_fields_html() -> str:
    cells = "".join(
        f'<div><label>{escape(label)}<input name="{name}" {attrs}></label>'
        f'<span id="lvl-{name}"></span> '
        f'<label class="check" id="confirm-{name}" hidden>'
        f'<input type="checkbox" name="confirm_{name}"> This is right</label></div>'
        for name, label, attrs in IDENTITY_FORM_FIELDS
    )
    return f'<div class="grid2">{cells}</div>'


CANDIDATE_BODY = """<main class="wrap">
<h1>Candidate</h1>
<p>What Sabra knows about you, and how sure it is. <span class="pill VERIFIED">VERIFIED</span> is
something you stated. <span class="pill INFERRED">INFERRED</span> was read from your resume and is
never typed onto a form until you confirm it. <span class="pill UNKNOWN">UNKNOWN</span> is not
known.</p>
<section class="card"><h2>Your details</h2>
<p>Only what you change here, or tick <em>This is right</em> for, is recorded as stated by you
(VERIFIED), and that wins over anything read from a resume. Every other box keeps what it was and
how sure Sabra was of it; the tag beside each box shows that. A blank box changes nothing.</p>
<form id="identity-form">
__IDENTITY_FIELDS__
<p><button type="submit">Save my details</button></p>
<p class="line" id="identity-line"></p></form></section>
<section class="card"><h2>Resume</h2>
<div id="resumes">Loading...</div>
<form id="resume-form" style="margin-top:14px">
<label for="resume">Upload a master resume (.pdf, .docx or .txt)</label>
<input id="resume" name="resume" type="file" accept=".pdf,.docx,.txt" required>
<p><button type="submit">Upload resume</button></p>
<p class="line" id="upload-line"></p></form></section>
<section class="card"><h2>Profile</h2><div id="profile">Loading...</div></section>
</main>
<script>
__ESC__
const FIELDS=['first_name','last_name','email','phone','street_address','city','state','postal_code','country','linkedin_url','headline'];
function renderSections(sections){
  if(!sections||!sections.length)return '<p>No profile yet. Upload a resume or save your details.</p>';
  return sections.map(s=>`<h3>${esc(s.title)}</h3>`+(s.rows.length?s.rows.map(r=>`<div class="stage"><span class="pill ${esc(r.level)}">${esc(r.level)}</span> <strong>${esc(r.label)}</strong>${r.detail?' · '+esc(r.detail):''}</div>`).join(''):'<p>Nothing recorded.</p>')).join('');
}
function renderResumes(list){
  if(!list||!list.length)return '<p>No resume stored yet.</p>';
  return list.map(r=>`<div class="stage"><strong>Version ${esc(r.version)}</strong>${r.is_master?' <span class="pill VERIFIED">master</span>':''} · ${esc(r.label||'')} · ${esc(r.source_format)} · ${esc(String(r.content_hash||'').slice(0,12))}</div>`).join('');
}
async function load(){
  const b=await readBody(await fetch('/api/candidate'));
  if(!b.ok){document.querySelector('#profile').textContent=detailOf(b,'The profile could not be loaded.');return;}
  const d=b.d;
  document.querySelector('#profile').innerHTML=renderSections(d.sections);
  document.querySelector('#resumes').innerHTML=renderResumes(d.resumes);
  const form=document.querySelector('#identity-form');
  const ident=d.identity||{};
  const levels=d.identity_levels||{};
  prefilled=Object.assign({},ident);
  FIELDS.forEach(f=>{
    const el=form.elements?form.elements[f]:null;if(el)el.value=ident[f]||'';
    const box=form.elements?form.elements['confirm_'+f]:null;if(box)box.checked=false;
    const lv=levels[f]||'UNKNOWN';
    document.querySelector('#lvl-'+f).innerHTML=`<span class="pill ${esc(lv)}">${esc(lv)}</span>`;
    const wrap=document.querySelector('#confirm-'+f);
    if(wrap)wrap.hidden=(lv==='VERIFIED'||!ident[f]);
  });
}
let prefilled={};
function collectIdentity(f,before){
  // Only what the owner changed, or explicitly confirmed. A value left as it was prefilled
  // (read from the resume, or recorded but never verified) is not a statement by the owner.
  const body={};
  FIELDS.forEach(k=>{
    const v=(f.get(k)||'').toString().trim();
    if(v==='')return;
    const was=String(before[k]||'').trim();
    if(v!==was||f.get('confirm_'+k))body[k]=v;
  });
  return body;
}
document.querySelector('#identity-form').addEventListener('submit',async e=>{
  e.preventDefault();
  const line=document.querySelector('#identity-line');
  const body=collectIdentity(new FormData(e.target),prefilled);
  if(!Object.keys(body).length){line.textContent='Nothing to save. Change a box, or tick "This is right" beside a value you want to confirm.';return;}
  const b=await readBody(await fetch('/api/candidate/identity',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
  line.textContent=b.ok?'Saved. Recorded as stated by you: '+Object.keys(body).join(', ')+'.':detailOf(b,'Save failed');
  if(b.ok)load();
});
document.querySelector('#resume-form').addEventListener('submit',async e=>{
  e.preventDefault();
  const file=document.querySelector('#resume').files[0];
  const line=document.querySelector('#upload-line');
  if(!file){line.textContent='Choose a PDF, Word or text resume first.';return;}
  line.textContent='Uploading and reading...';
  const body=new FormData();body.append('resume',file);
  const b=await readBody(await fetch('/api/candidate/resume',{method:'POST',body}));
  line.textContent=b.ok?'Resume stored as inferred evidence. Confirm your details above to make them yours.':detailOf(b,'Upload failed');
  if(b.ok)load();
});
load();
</script>
""".replace("__IDENTITY_FIELDS__", _identity_fields_html()).replace("__ESC__", ESC_JS)


# ---------------------------------------------------------------------------
# Searches
# ---------------------------------------------------------------------------

EMPLOYMENT_TYPES = (
    "FULL_TIME",
    "PART_TIME",
    "CONTRACT",
    "TEMPORARY",
    "INTERNSHIP",
    "COMMISSION_ONLY",
)

SEARCHES_BODY = """<main class="wrap">
<h1>Searches</h1>
<p>A search says what kind of job to look for. The active one is what Run now uses.</p>
<section class="card"><h2>Your searches</h2><div id="list">Loading...</div>
<p><button type="button" class="secondary" id="new-search">New search</button></p></section>
<section class="card"><h2 id="form-title">Edit search</h2>
<form id="search-form">
<label>Name<input name="name" required></label>
<div class="grid2">
<div><label>Target job titles (one per line)<textarea name="target_titles" rows="5"></textarea></label></div>
<div><label>Also acceptable titles (one per line)<textarea name="acceptable_titles" rows="5"></textarea></label></div>
<div><label>Never show these titles (one per line)<textarea name="excluded_titles" rows="4"></textarea></label></div>
<div><label>Excluded keywords (one per line)<textarea name="excluded_keywords" rows="4"></textarea></label></div>
<div><label>Role family words (one per line, e.g. office, administrative)<textarea name="role_family_terms" rows="4"></textarea></label></div>
<div><label>Level words (one per line, e.g. coordinator, manager)<textarea name="role_level_terms" rows="4"></textarea></label></div>
<div><label>Industries (one per line)<textarea name="industries" rows="3"></textarea></label></div>
<div><label>Industries to avoid (one per line)<textarea name="excluded_industries" rows="3"></textarea></label></div>
<div><label>Cities (one per line)<textarea name="cities" rows="3"></textarea></label></div>
<div><label>States (one per line)<textarea name="states" rows="3"></textarea></label></div>
<div><label>Metro areas (one per line)<textarea name="metro_areas" rows="3"></textarea></label></div>
<div><label>Employment types</label>__EMPLOYMENT__</div>
</div>
<label>Where the work happens</label>
<label class="check"><input type="checkbox" name="allow_remote"> Remote</label>
<label class="check"><input type="checkbox" name="allow_hybrid"> Hybrid</label>
<label class="check"><input type="checkbox" name="allow_onsite"> On site</label>
<label class="check"><input type="checkbox" name="allow_relocation"> Willing to relocate</label>
<div class="grid3">
<div><label>Minimum pay<input name="compensation_minimum" type="number" min="0" step="any"></label></div>
<div><label>Target pay<input name="compensation_target" type="number" min="0" step="any"></label></div>
<div><label>Currency<input name="compensation_currency"></label></div>
<div><label>Longest acceptable travel (% of time)<input name="travel_max_percent" type="number" min="0" max="100"></label></div>
<div><label>Ignore postings older than (days). Leave blank for no limit.<input name="max_age_days" type="number" min="1"></label></div>
<div><label>Shortlist size<input name="shortlist_target" type="number" min="1"></label></div>
<div><label>Prepare packages for the top<input name="prepare_target" type="number" min="0"></label></div>
</div>
<p><button type="submit" id="save">Save search</button> <button type="button" class="secondary" id="activate">Make this the active search</button></p>
<p class="line" id="line"></p></form></section>
</main>
<script>
__ESC__
const LIST_FIELDS=['target_titles','acceptable_titles','excluded_titles','excluded_keywords','role_family_terms','role_level_terms','industries','excluded_industries'];
const LOC_FIELDS={cities:'cities',states:'states',metro_areas:'metro_areas'};
const TYPES=__TYPES__;
let current=null;let currentId=null;let isNew=false;
function lines(v){return String(v||'').split(/\\r?\\n/).map(x=>x.trim()).filter(Boolean);}
function num(v){const t=String(v==null?'':v).trim();return t===''?null:Number(t);}
function renderList(d){
  const rows=d.searches||[];
  if(!rows.length)return '<p>No searches yet.</p>';
  return rows.map(s=>`<div class="stage"><strong>${esc(s.name)}</strong>${s.active?' <span class="pill VERIFIED">active</span>':''} · ${esc((s.target_titles||[]).slice(0,3).join(', '))} <button type="button" class="secondary" data-edit="${esc(s.id)}">Edit</button></div>`).join('');
}
function fill(p){
  const f=document.querySelector('#search-form');
  const el=n=>f.elements?f.elements[n]:null;
  const set=(n,v)=>{const e=el(n);if(e)e.value=v;};
  const chk=(n,v)=>{const e=el(n);if(e)e.checked=!!v;};
  set('name',p.name||'');
  LIST_FIELDS.forEach(k=>set(k,(p[k]||[]).join('\\n')));
  const loc=p.locations||{};
  Object.keys(LOC_FIELDS).forEach(k=>set(k,(loc[k]||[]).join('\\n')));
  chk('allow_remote',loc.allow_remote);chk('allow_hybrid',loc.allow_hybrid);chk('allow_onsite',loc.allow_onsite);chk('allow_relocation',loc.allow_relocation);
  const comp=p.compensation||{};
  set('compensation_minimum',comp.minimum==null?'':comp.minimum);
  set('compensation_target',comp.target==null?'':comp.target);
  set('compensation_currency',comp.currency||'USD');
  set('travel_max_percent',p.travel_max_percent==null?'':p.travel_max_percent);
  set('max_age_days',p.max_age_days==null?'':p.max_age_days);
  set('shortlist_target',p.shortlist_target==null?'':p.shortlist_target);
  set('prepare_target',p.prepare_target==null?'':p.prepare_target);
  TYPES.forEach(t=>chk('type_'+t,(p.employment_types||[]).includes(t)));
}
function collect(f){
  const g=n=>f.get(n);
  const p=Object.assign({},current||{});
  p.name=(g('name')||'').toString().trim();
  LIST_FIELDS.forEach(k=>{p[k]=lines(g(k));});
  const loc=Object.assign({},p.locations||{});
  Object.keys(LOC_FIELDS).forEach(k=>{loc[k]=lines(g(k));});
  loc.allow_remote=!!g('allow_remote');loc.allow_hybrid=!!g('allow_hybrid');loc.allow_onsite=!!g('allow_onsite');loc.allow_relocation=!!g('allow_relocation');
  p.locations=loc;
  const comp=Object.assign({},p.compensation||{});
  comp.minimum=num(g('compensation_minimum'));comp.target=num(g('compensation_target'));
  comp.currency=((g('compensation_currency')||'USD').toString().trim())||'USD';
  p.compensation=comp;
  p.travel_max_percent=num(g('travel_max_percent'));
  p.max_age_days=num(g('max_age_days'));
  const sl=num(g('shortlist_target'));if(sl!==null)p.shortlist_target=sl;
  const pt=num(g('prepare_target'));if(pt!==null)p.prepare_target=pt;
  p.employment_types=TYPES.filter(t=>!!g('type_'+t));
  return p;
}
async function loadList(){
  const b=await readBody(await fetch('/api/searches'));
  const list=document.querySelector('#list');
  if(!b.ok){list.textContent=detailOf(b,'The searches could not be loaded.');return b;}
  list.innerHTML=renderList(b.d);
  list.querySelectorAll('button[data-edit]').forEach(btn=>btn.addEventListener('click',()=>edit(btn.dataset.edit)));
  return b;
}
async function edit(id){
  const b=await readBody(await fetch('/api/searches/'+encodeURIComponent(id)));
  if(!b.ok){document.querySelector('#line').textContent=detailOf(b,'The search could not be loaded.');return;}
  current=b.d.profile;currentId=id;isNew=false;
  document.querySelector('#form-title').textContent='Edit search';
  fill(current);
  document.querySelector('#activate').disabled=!!b.d.active;
  document.querySelector('#line').textContent='';
}
function startNew(){
  current={};currentId=null;isNew=true;
  document.querySelector('#form-title').textContent='New search';
  fill({name:'',locations:{allow_remote:true,allow_hybrid:true,allow_onsite:true},compensation:{currency:'USD'},employment_types:['FULL_TIME'],shortlist_target:10,prepare_target:3,max_age_days:90});
  document.querySelector('#activate').disabled=true;
  document.querySelector('#line').textContent='';
}
document.querySelector('#search-form').addEventListener('submit',async e=>{
  e.preventDefault();
  const line=document.querySelector('#line');
  const body=collect(new FormData(e.target));
  const url=isNew?'/api/searches':'/api/searches/'+encodeURIComponent(currentId);
  const b=await readBody(await fetch(url,{method:isNew?'POST':'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
  if(!b.ok){line.textContent=detailOf(b,'Save failed');return;}
  line.textContent='Saved.';
  current=b.d.profile||body;currentId=(b.d.profile||{}).id||currentId;isNew=false;
  await loadList();
});
document.querySelector('#activate').addEventListener('click',async()=>{
  const line=document.querySelector('#line');
  if(!currentId)return;
  const b=await readBody(await fetch('/api/searches/'+encodeURIComponent(currentId)+'/activate',{method:'POST'}));
  line.textContent=b.ok?'This search is now active.':detailOf(b,'Activation failed');
  if(b.ok){document.querySelector('#activate').disabled=true;await loadList();}
});
document.querySelector('#new-search').addEventListener('click',startNew);
(async()=>{const b=await loadList();if(b&&b.ok){const active=(b.d.searches||[]).find(s=>s.active)||(b.d.searches||[])[0];if(active)await edit(active.id);}})();
</script>
"""

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

SETTINGS_BODY = """<main class="wrap">
<h1>Settings</h1>
<p>What Sabra is set to do. The values below are changed outside the browser; the job boards can be changed here.</p>
<section class="card"><div id="settings">Loading...</div></section>
<section class="card">
<h2>Job boards</h2>
<p>The company boards Sabra reads each run. A role on a board Sabra cannot fill itself (anything except Greenhouse and Lever) is opened in your browser for you to complete.</p>
<div id="sources">Loading...</div>
<p class="line" id="sources-line"></p>
</section>
<section class="card">
<h2>Add a board</h2>
<p>Paste a careers URL (Greenhouse, Lever or Ashby). Sabra reads the board once, shows what it found, and adds it only when you press Add.</p>
<form id="probe-form">
<label for="board-url">Careers URL or board name</label>
<input id="board-url" name="url" autocomplete="off" placeholder="https://jobs.ashbyhq.com/company">
<div class="grid3">
<div><label for="board-kind">Service (only for a bare name)</label>
<select id="board-kind" name="kind"><option value="">Detect from the URL</option><option value="GREENHOUSE">Greenhouse</option><option value="LEVER">Lever</option><option value="ASHBY">Ashby</option></select></div>
<div><label for="board-name">Company name (optional)</label><input id="board-name" name="display_name"></div>
<div><label for="board-industry">Industry (optional)</label><input id="board-industry" name="industry"></div>
</div>
<p><button type="submit" id="probe-btn">Check this board</button></p>
</form>
<div id="probe-result" class="line"></div>
<p><button type="button" id="add-btn" hidden>Add this board</button></p>
</section>
</main>
<script>
__ESC__
function row(label,value,note){return `<div class="stage"><strong>${esc(label)}</strong><br>${esc(value)}${note?`<br><span class="muted">${esc(note)}</span>`:''}</div>`;}
async function load(){
  const box=document.querySelector('#settings');
  const b=await readBody(await fetch('/api/settings'));
  if(!b.ok){box.textContent=detailOf(b,'Settings could not be loaded.');return;}
  const s=b.d;
  box.innerHTML=[
    row('Submission mode',s.automation_dry_run?'Dry run (nothing is opened, typed or submitted)':'Real submission is ON','Change with the environment variable '+s.dry_run_env_var+' and restart the dashboard.'),
    row('Active search',s.active_search_profile_name||'(none yet)',s.active_search_profile_id||''),
    row('Language model',(s.llm_provider||'none')+(s.llm_model?' / '+s.llm_model:'')),
    row('Database',s.database_url),
    row('Private folder',s.private_dir||'(not set)'),
    row('Artifacts folder',s.artifacts_dir),
    row('Wait for you after a CAPTCHA',s.handoff_timeout_seconds+' seconds'),
    row('Browser for hand-offs (Playwright Chromium)',s.playwright.chromium_installed?'Installed':'Not installed','Install with: uv run playwright install chromium'),
    row('Version',s.version)
  ].join('');
}
function sourceRow(x){
  const state=x.enabled?'On':'Off';
  const verified=x.verified_jobs==null?'':x.verified_jobs+' postings when added';
  const found=(x.total_discovered||0)+' found so far';
  const err=x.last_error?`<br><span class="err">${esc(x.last_error)}</span>`:'';
  const act=x.enabled?'disable':'enable';
  return `<div class="stage"><strong>${esc(x.display_name)}</strong> <span class="pill">${esc(x.kind)}</span> <span class="pill">${esc(state)}</span> <span class="pill">${esc(x.health||'UNKNOWN')}</span><br><span class="muted">${esc(x.id)} · ${esc(verified?verified+' · '+found:found)}</span>${err}<br><button type="button" class="secondary" data-act="${act}" data-id="${esc(x.id)}">${x.enabled?'Disable':'Enable'}</button></div>`;
}
async function loadSources(){
  const box=document.querySelector('#sources');
  const b=await readBody(await fetch('/api/sources'));
  if(!b.ok){box.textContent=detailOf(b,'The job boards could not be loaded.');return;}
  const list=Array.isArray(b.d.sources)?b.d.sources:[];
  box.innerHTML=list.length?list.map(sourceRow).join(''):'No job boards are set up yet.';
}
let probed=null;
function probeSummary(r){
  if(!r.live)return 'This board did not answer as a live job board'+(r.error?': '+r.error:'')+'. Nothing can be added.';
  const where=r.profile_has_cities?'your cities or remote':'remote';
  const titles=(r.sample_titles||[]).length?' Titles that fit your search: '+r.sample_titles.join('; ')+'.':' No titles match your search yet.';
  const dup=r.already_in_registry?' It is already in your list as '+r.already_in_registry+'.':'';
  return 'Live. '+r.posting_count+' postings, '+r.local_or_remote_count+' mention '+where+'.'+titles+dup;
}
async function probeBoard(ev){
  if(ev&&ev.preventDefault)ev.preventDefault();
  const line=document.querySelector('#probe-result');
  const addBtn=document.querySelector('#add-btn');
  addBtn.hidden=true;probed=null;
  const url=document.querySelector('#board-url').value;
  const kind=document.querySelector('#board-kind').value;
  line.textContent='Checking...';
  const b=await readBody(await fetch('/api/sources/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url_or_token:url,kind:kind||null})}));
  if(!b.ok){line.textContent=detailOf(b,'The board could not be checked.');return;}
  line.textContent=probeSummary(b.d);
  if(b.d.live&&!b.d.already_in_registry){probed={url:url,kind:kind};addBtn.hidden=false;}
}
async function addBoard(){
  if(!probed)return;
  const line=document.querySelector('#probe-result');
  const addBtn=document.querySelector('#add-btn');
  addBtn.disabled=true;
  const body={url_or_token:probed.url,kind:probed.kind||null,display_name:document.querySelector('#board-name').value||null,industry:document.querySelector('#board-industry').value||null};
  const b=await readBody(await fetch('/api/sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
  addBtn.disabled=false;
  if(!b.ok){line.textContent=detailOf(b,'The board could not be added.');return;}
  line.textContent='Added '+(b.d.source&&b.d.source.display_name?b.d.source.display_name:'the board')+'. It is read on the next run.';
  addBtn.hidden=true;probed=null;
  await loadSources();
}
async function toggleSource(act,id){
  const line=document.querySelector('#sources-line');
  const b=await readBody(await fetch('/api/sources/'+encodeURIComponent(id)+'/'+act,{method:'POST'}));
  line.textContent=b.ok?'':detailOf(b,'The change was refused.');
  await loadSources();
}
document.querySelector('#sources').addEventListener('click',ev=>{
  const t=ev.target&&ev.target.closest?ev.target.closest('button[data-act]'):null;
  if(t)toggleSource(t.dataset.act,t.dataset.id);
});
document.querySelector('#probe-form').addEventListener('submit',probeBoard);
document.querySelector('#add-btn').addEventListener('click',addBoard);
load();
loadSources();
</script>
""".replace("__ESC__", ESC_JS)


def searches_body() -> str:
    boxes = "".join(
        f'<label class="check"><input type="checkbox" name="type_{kind}"> '
        f"{kind.replace('_', ' ').title()}</label>"
        for kind in EMPLOYMENT_TYPES
    )
    import json

    return (
        SEARCHES_BODY.replace("__EMPLOYMENT__", boxes)
        .replace("__TYPES__", json.dumps(list(EMPLOYMENT_TYPES)))
        .replace("__ESC__", ESC_JS)
    )
