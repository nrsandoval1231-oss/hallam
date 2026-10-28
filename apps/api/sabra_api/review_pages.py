"""The decision pages: opportunity detail, application review and the pipeline.

Same conventions as `pages.py`: the page body carries no markup built from data, each page
has exactly one `<script>` (the offline tests read the first), every rendered string goes
through `esc()`, and links built from posting data are limited to http(s).

The server decides what may be done (`actions` in each payload, with the reason when it
may not); the script only shows it. Nothing here approves, submits or writes except
through the existing routes.
"""

from __future__ import annotations

import json

from .pages import ASK_JS, ESC_JS

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def js_string(value: str) -> str:
    """A JavaScript string literal that is safe inside a `<script>` element."""

    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


FORMAT_JS = r"""
function when(v){if(!v)return '';const d=new Date(v);return isNaN(d.getTime())?String(v):d.toLocaleString();}
function pct(x){return x==null||x===''?'not assessed':Math.round(Number(x)*100)+'%';}
function safeUrl(u){return /^https?:\/\//i.test(String(u||''))?String(u):'';}
function link(u){const s=safeUrl(u);return s?`<a href="${esc(s)}" target="_blank" rel="noopener noreferrer">${esc(s)}</a>`:esc(u||'');}
function bullets(items){return items&&items.length?'<ul>'+items.map(i=>`<li>${esc(i)}</li>`).join('')+'</ul>':'<p class="muted">None.</p>';}
function section(title,inner,cls){return `<section class="card${cls?' '+esc(cls):''}"><h2>${esc(title)}</h2>${inner}</section>`;}
function textBlock(t,empty){return t&&String(t).trim()?`<div class="text">${esc(t)}</div>`:`<p class="muted">${esc(empty||'Nothing recorded.')}</p>`;}
function facts(rows){return '<dl class="facts">'+rows.filter(r=>r[1]!==null&&r[1]!==undefined&&r[1]!=='').map(r=>`<dt>${esc(r[0])}</dt><dd>${r[2]?r[1]:esc(r[1])}</dd>`).join('')+'</dl>';}
function go(url){if(typeof location!=='undefined')location.href=url;}
"""

DETAIL_CSS = (
    ".facts{display:grid;grid-template-columns:max-content 1fr;gap:4px 18px;margin:0}"
    ".facts dt{color:var(--muted)}.facts dd{margin:0;overflow-wrap:anywhere}"
    ".text{white-space:pre-wrap;overflow-wrap:anywhere}"
    "table.plain{width:100%;border-collapse:collapse}"
    "table.plain th,table.plain td{text-align:left;padding:6px 8px;"
    "border-top:1px solid var(--line);vertical-align:top}"
    "table.plain th{color:var(--muted);font-weight:600;font-size:13px;border-top:0}"
    ".actions{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:14px 0}"
    ".wrap a.btn{color:#fff;display:inline-block;padding:9px 15px;border-radius:8px}"
    ".history{list-style:none;margin:0;padding:0}"
    ".history li{padding:6px 0;border-top:1px solid var(--line);font-size:14px}"
    ".history .when{color:var(--muted);margin-right:8px}"
    ".cols{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}"
    ".wrap h3{font-size:15px;margin:14px 0 4px}"
    ".wrap ul{margin:4px 0 8px;padding-left:20px}"
    "details>summary{cursor:pointer;font-weight:700;margin:6px 0}"
    ".pill.passed,.pill.ok{background:#D9EAE2}.pill.rejected,.pill.bad{background:#F2DCD6}"
    ".pill.flag{background:#F0E9DB}"
    "code{font-size:12px;overflow-wrap:anywhere}"
    ".decision{border:2px solid var(--ink)}"
    "@media(max-width:700px){.cols{grid-template-columns:1fr}"
    ".facts{grid-template-columns:1fr}}"
)

# ---------------------------------------------------------------------------
# Opportunity detail (requirement 30)
# ---------------------------------------------------------------------------

OPPORTUNITY_DETAIL_BODY = r"""<main class="wrap">
<p><a href="/opportunities">Back to opportunities</a></p>
<h1 id="title">Opportunity</h1>
<p class="muted" id="meta"></p>
<div class="actions" id="actions"></div>
<p class="line" id="line"></p>
<div id="sections"><section class="card"><p>Loading...</p></section></div>
</main>
<script>
__ESC__
__FORMAT__
const OPP_ID=__ID__;
let detail=null;
function renderMeta(d){
  const o=d.opportunity,j=d.job;
  document.querySelector('#title').textContent=(o.title||'Opportunity')+' at '+(o.company||'the company');
  document.querySelector('#meta').innerHTML=`<span class="pill">${esc(o.status)}</span> ${esc(j.location||o.location||'Location not listed')} · ${esc(j.compensation_text||'pay not listed')}`;
}
function renderActions(d){
  const a=d.actions||{},p=d.package;
  const notes=[];
  if(!a.can_prepare)notes.push('Preparing is available before approval, for a role you have not passed on.');
  if(a.passed)notes.push('You passed on this role.');
  else if(!a.can_pass)notes.push('A role that is approved or submitted is handled from its application.');
  const review=p?`<a class="btn" href="${esc('/packages/'+encodeURIComponent(p.id))}">Review application</a>`:'';
  document.querySelector('#actions').innerHTML=`<button type="button" id="prepare" ${a.can_prepare?'':'disabled'}>Prepare application</button><button type="button" class="secondary" id="pass" ${a.can_pass?'':'disabled'}>Pass</button>${review}${notes.length?'<span class="muted">'+esc(notes.join(' '))+'</span>':''}`;
  const prepareBtn=document.querySelector('#prepare');
  if(prepareBtn)prepareBtn.addEventListener('click',prepare);
  const passBtn=document.querySelector('#pass');
  if(passBtn)passBtn.addEventListener('click',pass);
}
function renderWhy(d){
  const f=d.fit,r=d.rank||{};
  const why=f&&f.why_this_job?f.why_this_job:(d.opportunity.shortlist_reason||'');
  return section('Why Velora recommends it',(why?`<p class="text">${esc(why)}</p>`:'<p class="muted">No recommendation has been recorded yet.</p>')+facts([
    ['Rank',r.position==null?'unranked':'#'+r.position+' of '+r.of],
    ['Rank score',r.score==null?'':Number(r.score).toFixed(3)],
    ['Shortlisted',d.opportunity.shortlisted?'yes':'no']]));
}
function renderFit(d){
  const f=d.fit;
  if(!f)return section('Candidate fit','<p class="muted">This opportunity has not been scored yet.</p>');
  const rows=(f.components||[]).map(c=>`<tr><td>${esc(c.label)}</td><td>${esc(pct(c.score))}</td><td>${esc(c.rationale||'')}</td></tr>`).join('');
  return section('Candidate fit',`<p><strong>Fit ${esc(pct(f.fit_score))}</strong> · confidence ${esc(pct(f.confidence))} · ${esc(f.engine||'')} scoring</p>
<table class="plain"><thead><tr><th>Component</th><th>Score</th><th>Why</th></tr></thead><tbody>${rows}</tbody></table>
<div class="cols"><div><h3>Strengths</h3>${bullets(f.strengths)}</div><div><h3>Gaps</h3>${bullets(f.gaps)}</div><div><h3>Risks</h3>${bullets(f.risks)}</div></div>`);
}
function renderFilter(d){
  const h=d.hard_filter||{};
  const cls=h.decision==='passed'?'passed':(h.decision==='rejected'?'rejected':'');
  const reasons=(h.reasons||[]).map(r=>`<li><strong>${esc(r)}</strong>${h.detail&&h.detail[r]?' · '+esc(h.detail[r]):''}</li>`).join('');
  return section('Hard filter',`<p><span class="pill ${esc(cls)}">${esc(String(h.decision||'').replace('_',' '))}</span> ${esc(h.summary||'')}</p>${reasons?'<ul>'+reasons+'</ul>':''}${h.owner_passed?'<p>You passed on this role.</p>':''}${h.evaluated_at?`<p class="muted">Evaluated ${esc(when(h.evaluated_at))}. Checks run: ${esc((h.checks_run||[]).join(', ')||'none')}.${(h.checks_skipped||[]).length?' Skipped: '+esc(h.checks_skipped.join(', '))+'.':''}</p>`:''}`);
}
function renderPosting(d){
  const j=d.job;
  const postings=(j.postings||[]).length>1?`<p class="muted">${esc(j.postings.length)} postings were merged into this opportunity.</p>`:'';
  return section('The posting',facts([
    ['Company',j.company],['Location',j.location],['Remote',j.remote_status],['Employment',j.employment_type],
    ['Pay',j.compensation_text],['Travel',j.travel],['Department',j.department],['Seniority',j.seniority],
    ['Posted',j.date_posted],['Discovered',when(j.date_discovered)],['Last verified',when(j.last_verified)],
    ['Source',j.source],['Posting link',link(j.source_url),true]])+postings+
    '<h3>Description</h3>'+textBlock(j.description,'No description was captured.')+
    '<h3>Responsibilities</h3>'+textBlock(j.responsibilities,'Not listed separately.')+
    '<h3>Requirements</h3>'+textBlock(j.requirements,'Not listed separately.')+
    '<h3>Preferred qualifications</h3>'+textBlock(j.preferred_qualifications,'Not listed separately.'));
}
function renderResearch(d){
  const r=d.research;
  if(!r)return section('Company research','<p class="muted">No research has been done on this company yet.</p>');
  const context=facts([['Industry',r.industry],['Website',link(r.company_website),true],['About',r.company_description],['Role context',r.role_context],['Reporting line',r.likely_reporting_organization],['Hiring context',r.hiring_context]]);
  return section('Company research',`<h3>Verified facts</h3><p class="muted">Read from a source.</p>${bullets(r.verified_facts)}<h3>Inferences</h3><p class="muted">Concluded by Velora, not verified.</p>${bullets(r.inferences)}${(r.recent_developments||[]).length?'<h3>Recent developments</h3>'+bullets(r.recent_developments):''}${(r.candidate_company_intersections||[]).length?'<h3>Where you and the company meet</h3>'+bullets(r.candidate_company_intersections):''}<h3>Context</h3>${context}<h3>Sources</h3>${bullets(r.sources)}<p class="muted">${esc(r.engine||'')} research, updated ${esc(when(r.updated_at))}</p>`);
}
function renderApplication(d){
  const a=d.application,p=d.package;
  if(!a&&!p)return section('Application','<p class="muted">No application has been prepared. Press Prepare application to build one for review.</p>');
  const rows=facts([
    ['Application status',a?a.status:''],['Submitted',a&&a.submission_time?when(a.submission_time):''],['Method',a?a.submission_method:''],
    ['Package',p?'revision '+p.revision+' · '+p.status:''],['Truth-clean',p?(p.truth_clean?'yes':'no'):''],
    ['Unresolved questions',p?p.unresolved_count:''],['Content hash',p?'<code>'+esc(p.content_hash)+'</code>':'',true],['Prepared',p?when(p.generated_at):'']]);
  return section('Application',rows+(p?`<p><a href="${esc('/packages/'+encodeURIComponent(p.id))}">Open the review page</a></p>`:''));
}
function renderHistory(d){
  const h=d.history||[];
  if(!h.length)return section('History','<p class="muted">No events recorded.</p>');
  return section('History','<ul class="history">'+h.slice().reverse().map(e=>`<li><span class="when">${esc(when(e.occurred_at))}</span><strong>${esc(e.event_type)}</strong> <span class="muted">${esc(e.entity_type)}${e.actor?' · '+esc(e.actor):''}</span>${e.summary?'<br>'+esc(e.summary):''}</li>`).join('')+'</ul>');
}
function render(d){
  detail=d;
  renderMeta(d);
  renderActions(d);
  document.querySelector('#sections').innerHTML=[renderWhy(d),renderFit(d),renderFilter(d),renderPosting(d),renderResearch(d),renderApplication(d),renderHistory(d)].join('');
}
async function load(){
  const line=document.querySelector('#line');
  try{
    const b=await readBody(await fetch('/api/opportunities/'+encodeURIComponent(OPP_ID)));
    if(!b.ok){line.textContent=detailOf(b,'This opportunity could not be loaded.');return;}
    render(b.d);
  }catch(error){line.textContent=error.message;}
}
async function prepare(){
  const line=document.querySelector('#line');
  line.textContent='Preparing the package...';
  const b=await readBody(await fetch('/api/opportunities/'+encodeURIComponent(OPP_ID)+'/prepare',{method:'POST'}));
  if(!b.ok){line.textContent=detailOf(b,'Preparation failed');return;}
  line.innerHTML=`Package ${esc(b.d.status)}; ${esc((b.d.unresolved_questions||[]).length)} unresolved question(s). <a href="${esc('/packages/'+encodeURIComponent(b.d.package_id))}">Review application</a>`;
  const again=await readBody(await fetch('/api/opportunities/'+encodeURIComponent(OPP_ID)));
  if(again.ok)render(again.d);
}
async function pass(){
  if(!await ask('Pass on this role? It leaves your list and will not come back.',{confirmLabel:'Pass'}))return;
  const line=document.querySelector('#line');
  const b=await readBody(await fetch('/api/opportunities/'+encodeURIComponent(OPP_ID)+'/pass',{method:'POST'}));
  if(!b.ok){line.textContent=detailOf(b,'Pass failed');return;}
  line.textContent='Passed. This role will not come back.';
  const again=await readBody(await fetch('/api/opportunities/'+encodeURIComponent(OPP_ID)));
  if(again.ok)render(again.d);
}
load();
</script>
"""


def opportunity_detail_body(opportunity_id: str) -> str:
    return (
        OPPORTUNITY_DETAIL_BODY.replace("__ESC__", ESC_JS + ASK_JS)
        .replace("__FORMAT__", FORMAT_JS)
        .replace("__ID__", js_string(opportunity_id))
    )


# ---------------------------------------------------------------------------
# Application review (requirement 31)
# ---------------------------------------------------------------------------

REVIEW_BODY = r"""<main class="wrap">
<p><a href="/applications">Back to applications</a></p>
<h1 id="title">Application review</h1>
<p class="muted" id="meta"></p>
<p class="line" id="line"></p>
<div id="sections"><section class="card"><p>Loading...</p></section></div>
</main>
<script>
__ESC__
__FORMAT__
const PACKAGE_ID=__ID__;
let review=null;
let busy=false;
function renderMeta(d){
  const o=d.opportunity;
  document.querySelector('#title').textContent=(o.title||'Application')+' at '+(o.company||'the company');
  document.querySelector('#meta').innerHTML=`Package revision ${esc(d.package.revision)} · <span class="pill">${esc(d.package.status)}</span> · application <span class="pill">${esc(d.application_status||'none')}</span>`;
}
function renderOpening(d){
  const j=d.job;
  return section('1. The original opening',facts([
    ['Title',j.title],['Company',j.company],['Location',j.location],['Pay',j.compensation_text],['Employment',j.employment_type],
    ['Posted',j.date_posted],['Posting link',link(j.source_url),true]])+
    `<details><summary>Full description</summary>${textBlock(j.description,'No description was captured.')}</details>`);
}
function renderFit(d){
  const f=d.fit,a=d.analysis||{};
  const head=f?`<p><strong>Fit ${esc(pct(f.fit_score))}</strong> · confidence ${esc(pct(f.confidence))}</p>${f.why_this_job?`<p class="text">${esc(f.why_this_job)}</p>`:''}<div class="cols"><div><h3>Strengths</h3>${bullets(f.strengths)}</div><div><h3>Gaps</h3>${bullets(f.gaps)}</div><div><h3>Risks</h3>${bullets(f.risks)}</div></div>`:`<p class="muted">No stored fit assessment.</p><h3>Gaps</h3>${bullets(a.qualification_gaps)}`;
  return section('2. Fit analysis',head+(a.fit_explanation?`<h3>Explanation</h3>${textBlock(a.fit_explanation)}`:''));
}
function renderResume(d){return section('3. Tailored résumé',textBlock((d.materials||{}).tailored_resume,'No tailored résumé on this package.'));}
function renderCover(d){return section('4. Cover letter',textBlock((d.materials||{}).cover_letter,'No cover letter on this package.'));}
function renderAnswers(d){
  const rows=((d.materials||{}).answers||[]).map(a=>{
    const flags=[a.level==='VERIFIED'?'<span class="pill ok">verified</span>':'<span class="pill flag">not verified</span>'];
    if(a.sensitive)flags.push('<span class="pill flag">sensitive</span>');
    if(a.requires_user_input)flags.push('<span class="pill bad">needs your input</span>');
    return `<tr><td>${esc(a.question_text||a.question_key)}</td><td>${a.answer?esc(a.answer):'<span class="muted">(blank)</span>'}</td><td><span class="pill ${esc(a.level)}">${esc(a.level)}</span> ${flags.join(' ')}</td></tr>`;
  }).join('');
  return section('5. Proposed answers',rows?`<table class="plain"><thead><tr><th>Question</th><th>Answer</th><th>Truth level</th></tr></thead><tbody>${rows}</tbody></table>`:'<p class="muted">No answers proposed.</p>');
}
function renderUnresolved(d){
  const u=d.unresolved_questions||[];
  return section('6. Unresolved questions',u.length?`<p>These must be answered before this package can be approved. Record them on the <a href="/answers">Answers page</a>, then refresh the package.</p>${bullets(u)}`:'<p>None.</p>');
}
function renderWarnings(d){
  const w=d.warnings||[],g=d.generation_notes||[];
  return section('7. Warnings',`<h3>Package warnings</h3>${bullets(w)}<h3>How this package was written</h3>${bullets(g)}`);
}
function renderMessages(d){
  const m=d.materials||{};
  return `<section class="card"><details><summary>8. Recruiter and hiring-manager messages, interview preparation</summary><h3>Recruiter message</h3>${textBlock(m.recruiter_message,'None.')}<h3>Hiring-manager message</h3>${textBlock(m.hiring_manager_message,'None.')}<h3>Interview preparation</h3>${textBlock(m.interview_preparation,'None.')}</details></section>`;
}
function renderDecision(d){
  const a=d.actions||{};
  const hash=d.package.content_hash;
  const refreshLabel='Refresh package';
  return `<section class="card decision"><h2>Decision</h2>
<p>Approving binds to this exact package. Content hash: <code>${esc(hash)}</code></p>
<div class="actions"><button type="button" id="approve" ${a.can_approve?'':'disabled'}>Approve application</button><button type="button" class="secondary" id="refresh" ${a.refresh?'':'disabled'}>${refreshLabel}</button><button type="button" class="secondary" id="reject" ${a.can_reject?'':'disabled'}>Reject</button></div>
${a.can_approve?'':`<p class="err" id="why-not">${esc(a.approve_reason||'')}</p>`}
${a.refresh?'':`<p class="muted">${esc(a.refresh_reason||'')}</p>`}
${a.can_reject?'':`<p class="muted">${esc(a.reject_reason||'')}</p>`}</section>`;
}
function render(d){
  review=d;
  renderMeta(d);
  document.querySelector('#sections').innerHTML=[renderOpening(d),renderFit(d),renderResume(d),renderCover(d),renderAnswers(d),renderUnresolved(d),renderWarnings(d),renderMessages(d),renderDecision(d)].join('');
  const wire=(sel,fn)=>{const el=document.querySelector(sel);if(el)el.addEventListener('click',fn);};
  wire('#approve',approve);wire('#refresh',refresh);wire('#reject',reject);
}
async function load(){
  const line=document.querySelector('#line');
  try{
    const b=await readBody(await fetch('/api/packages/'+encodeURIComponent(PACKAGE_ID)+'/review'));
    if(!b.ok){line.textContent=detailOf(b,'This package could not be loaded.');return;}
    render(b.d);
  }catch(error){line.textContent=error.message;}
}
async function approve(){
  const d=review;
  if(!d||!d.actions.can_approve||busy)return;
  busy=true;
  const line=document.querySelector('#line');
  try{
    const b=await readBody(await fetch('/api/packages/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approved_by:'owner',packages:[{package_id:d.package.id,content_hash:d.package.content_hash}]})}));
    if(!b.ok){line.textContent=detailOf(b,'Approval failed');return;}
    const refused=b.d.refused||[];
    if(refused.length){line.textContent='Not approved: '+refused.map(r=>r.message).join(' ');return;}
    line.textContent='Approved. The approval is bound to content hash '+d.package.content_hash+'.';
    await load();
  }finally{busy=false;}
}
async function refresh(){
  const d=review;
  if(!d||!d.actions.refresh||busy)return;
  busy=true;
  const line=document.querySelector('#line');
  try{
    const url=d.actions.refresh==='refresh'?'/api/applications/'+encodeURIComponent(d.application_id)+'/refresh':'/api/opportunities/'+encodeURIComponent(d.opportunity.id)+'/prepare';
    const b=await readBody(await fetch(url,{method:'POST'}));
    if(!b.ok){line.textContent=detailOf(b,'Refresh failed');return;}
    line.textContent='Package rebuilt. Review it, then approve it; any earlier approval no longer applies.';
    go('/packages/'+encodeURIComponent(b.d.package_id));
  }finally{busy=false;}
}
async function reject(){
  const d=review;
  if(!d||!d.actions.can_reject||busy)return;
  if(!await ask('Reject this application? The role leaves your list.',{confirmLabel:'Reject'}))return;
  busy=true;
  const line=document.querySelector('#line');
  try{
    const b=await readBody(await fetch('/api/packages/'+encodeURIComponent(d.package.id)+'/reject',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}));
    if(!b.ok){line.textContent=detailOf(b,'Reject failed');return;}
    line.textContent='Rejected. Nothing was submitted.';
    await load();
  }finally{busy=false;}
}
load();
</script>
"""


def review_body(package_id: str) -> str:
    return (
        REVIEW_BODY.replace("__ESC__", ESC_JS + ASK_JS)
        .replace("__FORMAT__", FORMAT_JS)
        .replace("__ID__", js_string(package_id))
    )


# ---------------------------------------------------------------------------
# Pipeline (requirements 22-24)
# ---------------------------------------------------------------------------

PIPELINE_CSS = (
    ".app{border-top:1px solid var(--line);padding:12px 0}.app:first-of-type{border-top:0}"
    ".app .top{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}"
    ".outcome{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}"
    ".wrap .outcome select,.wrap .outcome input{width:auto;min-width:0}"
    ".wrap .outcome input.oc-note,.wrap .outcome input.oc-contact{flex:1 1 150px}"
    ".chips{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}"
    ".chips .pill{font-size:13px;padding:3px 11px}"
    ".hist-box{margin-top:8px}.hist-box ul{list-style:none;padding:0;margin:0}"
    ".hist-box li{padding:4px 0;border-top:1px solid var(--line);font-size:13px}"
    ".wrap button.small{padding:5px 10px;font-size:13px}"
)

PIPELINE_BODY = r"""<main class="wrap">
<h1>Pipeline</h1>
<p>Every application past approval, by where it stands. Record what an employer does here; each outcome moves the application through the same lifecycle the engine uses and leaves an event.</p>
<p class="line" id="line"></p>
<div class="chips" id="chips"></div>
<div id="stages"><section class="card"><p>Loading...</p></section></div>
</main>
<script>
__ESC__
__FORMAT__
let labels={};
function renderRow(a){
  const kinds=(a.allowed_outcomes||[]).map(k=>`<option value="${esc(k)}">${esc(labels[k]||k)}</option>`).join('');
  const control=kinds?`<div class="outcome" data-id="${esc(a.application_id)}"><select class="oc-kind" aria-label="Outcome">${kinds}</select><input class="oc-note" placeholder="Note (optional)" maxlength="4000" aria-label="Note"><input class="oc-contact" placeholder="Who (optional)" maxlength="500" aria-label="Contact"><input class="oc-at" type="date" aria-label="Date it happened (optional)"><button type="button" class="oc-go small">Record outcome</button></div>`:'<p class="muted">No outcome can be recorded in this state.</p>';
  const last=a.last_event?`<br><span class="muted">Last: ${esc(a.last_event.event_type)} · ${esc(when(a.last_event.occurred_at))}${a.last_event.summary?' · '+esc(a.last_event.summary):''}</span>`:'';
  const sent=a.submitted_at?`Submitted ${esc(when(a.submitted_at))}${a.submission_method?' by '+esc(String(a.submission_method).replace(/_/g,' ').toLowerCase()):''}`:'Not submitted';
  return `<div class="app"><div class="top"><div><strong>${esc(a.title)}</strong> at ${esc(a.company)} <span class="pill">${esc(a.application_status)}</span><br><span class="muted">${sent}</span>${last}</div><div><a href="${esc('/packages/'+encodeURIComponent(a.package_id))}">Review</a> · <button type="button" class="secondary small hist" data-id="${esc(a.application_id)}">History</button></div></div>${control}<div class="hist-box" hidden></div></div>`;
}
function renderStages(stages){
  const filled=stages.filter(s=>s.count>0);
  document.querySelector('#chips').innerHTML=stages.map(s=>`<span class="pill">${esc(s.label)} ${esc(s.count)}</span>`).join('');
  document.querySelector('#stages').innerHTML=filled.length?filled.map(s=>section(s.label+' ('+s.count+')',s.applications.map(renderRow).join(''))).join(''):'<section class="card"><p>Nothing has been approved yet. Approved and submitted applications appear here.</p></section>';
  document.querySelectorAll('.oc-go').forEach(btn=>btn.addEventListener('click',()=>{
    const box=btn.closest('.outcome');
    recordOutcome(box.dataset.id,box.querySelector('.oc-kind').value,box.querySelector('.oc-note').value,box.querySelector('.oc-contact').value,box.querySelector('.oc-at').value);
  }));
  document.querySelectorAll('.hist').forEach(btn=>btn.addEventListener('click',()=>toggleHistory(btn)));
}
async function load(){
  const line=document.querySelector('#line');
  try{
    const b=await readBody(await fetch('/api/pipeline'));
    if(!b.ok){line.textContent=detailOf(b,'The pipeline could not be loaded.');return;}
    labels={};
    (b.d.outcome_kinds||[]).forEach(k=>{labels[k.kind]=k.label;});
    renderStages(b.d.board||[]);
  }catch(error){line.textContent=error.message;}
}
async function recordOutcome(id,kind,note,contact,at){
  const line=document.querySelector('#line');
  const body={kind:kind};
  if(note&&note.trim())body.note=note.trim();
  if(contact&&contact.trim())body.contact=contact.trim();
  if(at)body.occurred_at=at;
  const b=await readBody(await fetch('/api/applications/'+encodeURIComponent(id)+'/outcomes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}));
  if(!b.ok){line.textContent=detailOf(b,'The outcome could not be recorded.');return;}
  line.textContent='Recorded: '+(labels[b.d.kind]||b.d.kind)+'.'+(b.d.applied_state?' The application is now '+b.d.applied_state+'.':'')+(b.d.note?' '+b.d.note:'');
  await load();
}
async function toggleHistory(btn){
  const box=btn.closest('.app').querySelector('.hist-box');
  if(!box.hidden){box.hidden=true;return;}
  box.hidden=false;
  box.textContent='Loading...';
  const b=await readBody(await fetch('/api/applications/'+encodeURIComponent(btn.dataset.id)+'/ledger'));
  if(!b.ok){box.textContent=detailOf(b,'The history could not be loaded.');return;}
  const events=(b.d.events||[]).slice().reverse().map(e=>`<li><span class="muted">${esc(when(e.occurred_at))}</span> <strong>${esc(e.event_type)}</strong>${e.actor?' · '+esc(e.actor):''}</li>`).join('');
  const outcomes=(b.d.outcomes||[]).map(o=>`<li><strong>${esc(labels[o.kind]||o.kind)}</strong> · ${esc(when(o.occurred_at))}${o.notes?' · '+esc(o.notes):''}${o.payload&&o.payload.contact?' · '+esc(o.payload.contact):''}</li>`).join('');
  box.innerHTML=`<h3>Outcomes</h3>${outcomes?'<ul>'+outcomes+'</ul>':'<p class="muted">None recorded.</p>'}<h3>Events</h3><ul>${events}</ul>`;
}
load();
</script>
"""


def pipeline_body() -> str:
    return PIPELINE_BODY.replace("__ESC__", ESC_JS).replace("__FORMAT__", FORMAT_JS)


__all__ = [
    "DETAIL_CSS",
    "PIPELINE_CSS",
    "js_string",
    "opportunity_detail_body",
    "pipeline_body",
    "review_body",
]
