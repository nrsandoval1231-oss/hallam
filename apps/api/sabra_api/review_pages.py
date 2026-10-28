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
function section(title,inner,cls,id){return `<section class="card${cls?' '+esc(cls):''}"${id?` id="${esc(id)}" tabindex="-1"`:''}><h2>${esc(title)}</h2>${inner}</section>`;}
function textBlock(t,empty){return t&&String(t).trim()?`<div class="text">${esc(t)}</div>`:`<p class="muted">${esc(empty||'Nothing recorded.')}</p>`;}
// A deliberately small document formatter: escape first, then add only our own
// headings, paragraphs and lists. Source HTML, links and images remain inert text.
function documentBlock(t,empty){
  if(!t||!String(t).trim())return textBlock('',empty);
  let list=false;
  const out=[];
  const close=()=>{if(list){out.push('</ul>');list=false;}};
  String(t).split(/\r?\n/).forEach(line=>{
    const value=esc(line.trim()).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/(^|\s)_([^_]+)_(?=\s|$)/g,'$1<em>$2</em>');
    if(!value){close();return;}
    const heading=value.match(/^(#{1,6})\s+(.+)$/);
    const bullet=value.match(/^[-*]\s+(.+)$/);
    if(heading){close();const tag=heading[1].length===1?'h3':'h4';out.push('<'+tag+'>'+heading[2]+'</'+tag+'>');}
    else if(bullet){if(!list){out.push('<ul>');list=true;}out.push('<li>'+bullet[1]+'</li>');}
    else{close();out.push('<p>'+value+'</p>');}
  });
  close();return '<article class="document-preview">'+out.join('')+'</article>';
}
function facts(rows){return '<dl class="facts">'+rows.filter(r=>r[1]!==null&&r[1]!==undefined&&r[1]!=='').map(r=>`<dt>${esc(r[0])}</dt><dd>${r[2]?r[1]:esc(r[1])}</dd>`).join('')+'</dl>';}
function go(url){if(typeof location!=='undefined')location.href=url;}
"""

DETAIL_CSS = (
    ".decision-nav{display:flex;gap:8px 16px;flex-wrap:wrap;margin:12px 0 20px;padding:12px 16px;"
    "border:1px solid var(--line);border-radius:12px;background:rgba(255,255,255,.48)}"
    ".decision-nav a{font-size:13px;font-weight:600;text-underline-offset:3px}"
    ".readiness{border-left:3px solid var(--forest,#29483E);padding:10px 14px;margin:12px 0;background:var(--stone,#E8E5DC);border-radius:0 10px 10px 0}"
    ".readiness strong{display:block;margin-bottom:3px}.readiness p{margin:0}"
    ".evidence-label{font-size:11px;letter-spacing:.09em;text-transform:uppercase;font-weight:700;color:var(--muted)}"
    ".table-scroll{max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}"
    ".table-scroll table{min-width:520px}.card{scroll-margin-top:22px}"
    ".card:focus{outline:2px solid var(--forest,#29483E);outline-offset:3px}"
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
    ".opp-hero{display:grid;grid-template-columns:58px minmax(0,1fr) auto;gap:17px;align-items:center;margin:12px 0 24px}"
    ".company-tile{width:58px;height:58px;display:grid;place-items:center;background:#E9ECE5;color:var(--forest);border:1px solid var(--line);border-radius:8px;font:600 23px/1 'Artemis Sans',sans-serif;letter-spacing:.04em}"
    ".opp-title{margin:0!important;font:500 32px/1.1 'Artemis Display',Georgia,serif!important;color:var(--evergreen)}"
    ".opp-meta{display:flex;gap:5px 12px;flex-wrap:wrap;margin-top:7px;font-size:13px}.opp-meta .pill{align-self:center}"
    ".opp-confidence{text-align:right;color:var(--muted);font-size:12px}.opp-confidence strong{display:block;color:var(--forest);font-size:17px}"
    ".opportunity-tabs,.studio-steps{display:flex;gap:22px;align-items:stretch;border-bottom:1px solid var(--line);overflow-x:auto;scrollbar-width:thin}"
    ".opportunity-tabs [role=tab],.studio-steps [role=tab]{flex:0 0 auto;padding:10px 1px;border:0;border-radius:0;background:transparent;color:var(--muted);font:600 13px 'Artemis Sans',sans-serif;box-shadow:none}"
    ".opportunity-tabs [aria-selected=true],.studio-steps [aria-selected=true]{color:var(--evergreen);border-bottom:2px solid var(--forest)}"
    ".opportunity-tabs [role=tab]:hover,.studio-steps [role=tab]:hover{color:var(--evergreen);background:transparent}"
    ".opportunity-tabs [role=tab]:focus-visible,.studio-steps [role=tab]:focus-visible{outline:2px solid var(--forest);outline-offset:2px}"
    ".opportunity-panel{padding-top:16px}.overview-grid{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(240px,.9fr);gap:32px}"
    ".overview-grid h2{font:500 20px 'Artemis Display',Georgia,serif}.key-insights{border-left:1px solid var(--line);padding-left:24px}"
    ".key-insights ul{list-style:none;padding:0}.key-insights li{padding:7px 0;border-bottom:1px solid var(--line)}"
    ".opportunity-panel .actions{margin-top:22px}.opportunity-panel #prepare{background:var(--evergreen);color:#fff;border-radius:5px;padding:11px 22px}"
    ".studio-title{font:500 34px/1.1 'Artemis Display',Georgia,serif!important;margin:0!important}"
    ".studio-subtitle{margin:0;font-size:14px}.studio-steps{gap:0;justify-content:space-between;margin:0 0 10px}"
    ".reference-studio{padding-top:14px!important}.studio-titlebar{display:flex;justify-content:space-between;align-items:start;gap:12px}"
    ".studio-back{font-size:12px;white-space:nowrap}.studio-role-meta{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;margin:-8px 0 2px;font-size:12px;color:var(--muted)}"
    ".studio-role-meta strong{color:var(--graphite);font-weight:600}.studio-errorline{min-height:0!important;margin:0 0 3px;font-size:12px}"
    ".studio-steps [role=tab]{display:flex;align-items:center;gap:9px;padding:12px 8px}"
    ".step-number{display:grid;place-items:center;width:23px;height:23px;border:1px solid var(--line);border-radius:50%;font-size:11px}"
    ".studio-steps [aria-selected=true] .step-number{background:var(--evergreen);border-color:var(--evergreen);color:white}"
    ".studio-readiness{display:flex;gap:8px;align-items:baseline;padding:2px 11px;border:1px solid var(--line);border-left:3px solid var(--forest);background:#F0EFE9;margin:0 0 3px;font-size:12px}"
    ".studio-readiness strong{flex:0 0 auto;color:var(--evergreen)}.studio-readiness p{margin:0}"
    ".studio-layout{display:grid;grid-template-columns:180px minmax(0,1fr);gap:24px;align-items:start}"
    ".studio-nav{border-right:1px solid var(--line);padding-right:14px;display:grid;gap:4px;position:sticky;top:12px}"
    ".studio-nav button{width:100%;text-align:left;padding:8px 10px;background:transparent;color:var(--muted);border-radius:4px;font-size:12px;font-weight:500;box-shadow:none}"
    ".studio-nav button[aria-current=true]{background:#E9ECE5;color:var(--evergreen);font-weight:700}"
    ".studio-nav button:hover{background:#E9ECE5;color:var(--evergreen)}"
    ".studio-content{min-width:0}.studio-panel{min-width:0}.document-preview{max-width:740px;margin:0 auto;padding:26px 30px;background:#fff;border:1px solid var(--line);box-shadow:0 3px 14px rgba(20,45,41,.05);color:var(--graphite)}"
    ".document-preview h3,.document-preview h4{color:var(--evergreen);font-family:'Artemis Display',Georgia,serif;font-weight:500;margin:12px 0 4px}"
    ".document-preview p{color:var(--graphite);margin:5px 0}.studio-panel .card{margin-top:0}"
    ".studio-nav-mobile{display:none}"
    "@media(max-width:700px){.cols{grid-template-columns:1fr}"
    ".facts{grid-template-columns:1fr}}"
    "@media(max-width:700px){.overview-grid{grid-template-columns:1fr;gap:16px}.key-insights{border-left:0;border-top:1px solid var(--line);padding:15px 0 0}"
    ".studio-layout{grid-template-columns:1fr;gap:12px}.studio-nav{position:static;border:0;padding:0;display:flex;flex-wrap:wrap;gap:7px;overflow:visible}"
    ".studio-nav button{width:auto;white-space:normal;border:1px solid var(--line);padding:7px 10px}.studio-nav button[aria-current=true]{border-color:var(--forest)}"
    ".studio-steps{gap:8px;justify-content:flex-start}.studio-steps [role=tab]{font-size:12px}}"
    "@media(max-width:480px){.decision-nav{gap:10px 14px;padding:10px}.facts dd{margin-bottom:6px}"
    ".table-scroll table{min-width:480px}.opp-hero{grid-template-columns:44px minmax(0,1fr);gap:12px}.company-tile{width:44px;height:44px;font-size:18px}"
    ".opp-title{font-size:24px!important}.opp-confidence{grid-column:2;text-align:left}.studio-title{font-size:29px!important}.document-preview{padding:18px 16px}.studio-steps{gap:2px}.studio-steps [role=tab]{gap:5px;padding:9px 4px;font-size:11px}}"
)

# ---------------------------------------------------------------------------
# Opportunity detail (requirement 30)
# ---------------------------------------------------------------------------

OPPORTUNITY_DETAIL_BODY = r"""<main class="wrap">
<p><a href="/opportunities">Back to opportunities</a></p>
<div class="opp-hero"><div class="company-tile" id="company-tile" aria-hidden="true"></div><div><p class="eyebrow">Opportunity</p><h1 class="opp-title" id="title">Opportunity</h1><div class="opp-meta" id="meta"></div></div><div class="opp-confidence" id="confidence"></div></div>
<p class="line" id="line"></p>
<div class="opportunity-tabs" role="tablist" aria-label="Opportunity details" id="section-nav"><button type="button" role="tab" id="op-tab-overview" aria-controls="op-panel-overview" aria-selected="true" tabindex="0" data-tab="overview">Overview</button><button type="button" role="tab" id="op-tab-fit" aria-controls="op-panel-fit" aria-selected="false" tabindex="-1" data-tab="fit">Why it fits</button><button type="button" role="tab" id="op-tab-company" aria-controls="op-panel-company" aria-selected="false" tabindex="-1" data-tab="company">Company</button><button type="button" role="tab" id="op-tab-role" aria-controls="op-panel-role" aria-selected="false" tabindex="-1" data-tab="role">Role</button><button type="button" role="tab" id="op-tab-insights" aria-controls="op-panel-insights" aria-selected="false" tabindex="-1" data-tab="insights">Insights</button><button type="button" role="tab" id="op-tab-next" aria-controls="op-panel-next" aria-selected="false" tabindex="-1" data-tab="next">Next steps</button></div>
<div id="sections"><section class="card"><p>Loading...</p></section></div>
</main>
<script>
__ESC__
__FORMAT__
const OPP_ID=__ID__;
let detail=null;
function renderMeta(d){
  const o=d.opportunity,j=d.job;
  const company=o.company||j.company||'Company';
  document.querySelector('#title').textContent=(o.title||j.title||'Opportunity');
  document.querySelector('#company-tile').textContent=company.split(/\s+/).filter(Boolean).slice(0,2).map(x=>x[0]).join('').toUpperCase()||'•';
  document.querySelector('#meta').innerHTML=`<span>${esc(company)}</span><span>·</span><span>${esc(j.location||o.location||'Location not listed')}</span><span>·</span><span>${esc(j.compensation_text||'Pay not listed')}</span><span class="pill">${esc(o.status)}</span>`;
  const confidence=d.fit&&d.fit.confidence!=null?`<strong>${esc(pct(d.fit.confidence))}</strong>Confidence`:'<strong>—</strong>Confidence not assessed';
  document.querySelector('#confidence').innerHTML=confidence;
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
  return section('Why Artemis recommends it',`<p class="evidence-label">Recorded recommendation</p>`+(why?`<p class="text">${esc(why)}</p>`:'<p class="muted">No recommendation has been recorded yet.</p>')+facts([
    ['Rank',r.position==null?'unranked':'#'+r.position+' of '+r.of],
    ['Rank score',r.score==null?'':Number(r.score).toFixed(3)],
    ['Shortlisted',d.opportunity.shortlisted?'yes':'no']]),'', 'recommendation');
}
function renderOverview(d){
  const f=d.fit||{};
  const insightItems=(f.strengths||[]).slice(0,3).map(x=>`<li>${esc(x)}</li>`);
  (f.gaps||[]).slice(0,2).forEach(x=>insightItems.push(`<li>${esc(x)} <span class="muted">· gap to consider</span></li>`));
  const insights=insightItems.length?'<ul>'+insightItems.join('')+'</ul>':'<p class="muted">No fit insights have been recorded yet.</p>';
  return `<div class="overview-grid"><div><p class="eyebrow">Why this opportunity</p>${renderWhy(d)}</div><div class="key-insights"><p class="eyebrow">Key insights</p><h3>Evidence to weigh</h3>${insights}</div></div><div class="actions" id="actions"></div>`;
}
function renderFit(d){
  const f=d.fit;
  if(!f)return section('Candidate fit','<p class="muted">This opportunity has not been scored yet.</p>', '', 'fit');
  const rows=(f.components||[]).map(c=>`<tr><td>${esc(c.label)}</td><td>${esc(pct(c.score))}</td><td>${esc(c.rationale||'')}</td></tr>`).join('');
  return section('Candidate fit',`<p class="evidence-label">Engine assessment · ${esc(f.engine||'')}</p><p><strong>Fit ${esc(pct(f.fit_score))}</strong> · confidence ${esc(pct(f.confidence))}</p>
<div class="table-scroll" role="region" aria-label="Fit component scores" tabindex="0"><table class="plain"><thead><tr><th>Component</th><th>Score</th><th>Why</th></tr></thead><tbody>${rows||'<tr><td colspan="3" class="muted">No component detail recorded.</td></tr>'}</tbody></table></div>
<div class="cols"><div><h3>Strengths</h3>${bullets(f.strengths)}</div><div><h3>Gaps</h3>${bullets(f.gaps)}</div><div><h3>Risks</h3>${bullets(f.risks)}</div></div>`, '', 'fit');
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
  return section('The posting',`<p class="evidence-label">Source: original employer posting</p>`+facts([
    ['Company',j.company],['Location',j.location],['Remote',j.remote_status],['Employment',j.employment_type],
    ['Pay',j.compensation_text],['Travel',j.travel],['Department',j.department],['Seniority',j.seniority],
    ['Posted',j.date_posted],['Discovered',when(j.date_discovered)],['Last verified',when(j.last_verified)],
    ['Source',j.source],['Posting link',link(j.source_url),true]])+postings+
    '<h3>Description</h3>'+textBlock(j.description,'No description was captured.')+
    '<h3>Responsibilities</h3>'+textBlock(j.responsibilities,'Not listed separately.')+
    '<h3>Requirements</h3>'+textBlock(j.requirements,'Not listed separately.')+
    '<h3>Preferred qualifications</h3>'+textBlock(j.preferred_qualifications,'Not listed separately.'), '', 'posting');
}
function renderResearch(d){
  const r=d.research;
  if(!r)return section('Company research','<p class="muted">No research has been done on this company yet.</p>', '', 'research');
  const context=facts([['Industry',r.industry],['Website',link(r.company_website),true],['About',r.company_description],['Role context',r.role_context],['Reporting line',r.likely_reporting_organization],['Hiring context',r.hiring_context]]);
  return section('Company research',`<h3>Verified facts <span class="evidence-label">Source-backed</span></h3>${bullets(r.verified_facts)}<h3>Inferences <span class="evidence-label">Unverified</span></h3>${bullets(r.inferences)}${(r.recent_developments||[]).length?'<h3>Recent developments</h3>'+bullets(r.recent_developments):''}${(r.candidate_company_intersections||[]).length?'<h3>Where you and the company meet</h3>'+bullets(r.candidate_company_intersections):''}<h3>Context</h3>${context}<h3>Sources</h3>${bullets(r.sources)}<p class="muted">${esc(r.engine||'')} research, updated ${esc(when(r.updated_at))}</p>`, '', 'research');
}
function renderApplication(d){
  const a=d.application,p=d.package;
  if(!a&&!p)return section('Application','<p class="muted">No application has been prepared. Press Prepare application to build one for review.</p>');
  const rows=facts([
    ['Application status',a?a.status:''],['Submitted',a&&a.submission_time?when(a.submission_time):''],['Method',a?a.submission_method:''],
    ['Package',p?'revision '+p.revision+' · '+p.status:''],['Truth-clean',p?(p.truth_clean?'yes':'no'):''],
    ['Unresolved questions',p?p.unresolved_count:''],['Content hash',p?'<code>'+esc(p.content_hash)+'</code>':'',true],['Prepared',p?when(p.generated_at):'']]);
  const readiness=p?(p.unresolved_count?`${p.unresolved_count} unresolved question${p.unresolved_count===1?'':'s'} remain. Review the package before approval.`:p.truth_clean?'No unresolved questions are recorded; review the package and its hash before approval.':'Truth checks need review before approval.'):'No package is prepared yet.';
  return section('Application',`<div class="readiness"><strong>Readiness</strong><p>${esc(readiness)}</p></div>`+rows+(p?`<p><a href="${esc('/packages/'+encodeURIComponent(p.id))}">Open the review page</a></p>`:''), '', 'application');
}
function renderHistory(d){
  const h=d.history||[];
  if(!h.length)return section('History','<p class="muted">No events recorded.</p>', '', 'history');
  return section('History','<ul class="history">'+h.slice().reverse().map(e=>`<li><span class="when">${esc(when(e.occurred_at))}</span><strong>${esc(e.event_type)}</strong> <span class="muted">${esc(e.entity_type)}${e.actor?' · '+esc(e.actor):''}</span>${e.summary?'<br>'+esc(e.summary):''}</li>`).join('')+'</ul>', '', 'history');
}
function renderInsights(d){
  const h=d.hard_filter||{},r=d.research||{},f=d.fit||{};
  const filters=(h.reasons||[]).map(x=>`${esc(x)}${h.detail&&h.detail[x]?' · '+esc(h.detail[x]):''}`);
  const research=[...(r.recent_developments||[]),...(r.candidate_company_intersections||[])];
  return `<section class="card" id="insight-records" tabindex="-1"><h3>Recorded insights</h3><h4>Screening</h4>${h.summary?`<p>${esc(h.summary)}</p>`:'<p class="muted">No screening summary is recorded.</p>'}${filters.length?'<ul>'+filters.map(x=>`<li>${x}</li>`).join('')+'</ul>':''}<h4>Fit gaps and risks</h4>${bullets([...(f.gaps||[]),...(f.risks||[])])}<h4>Company developments and intersections</h4>${bullets(research)}</section>`;
}
function activateOpportunityTab(name,focus){
  ['overview','fit','company','role','insights','next'].forEach(key=>{
    const tab=document.querySelector('#op-tab-'+key),panel=document.querySelector('#op-panel-'+key);
    if(!tab||!panel)return;
    const active=key===name;
    tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;panel.hidden=!active;
    if(active&&focus)tab.focus();
  });
}
function wireOpportunityTabs(){
  const keys=['overview','fit','company','role','insights','next'];
  keys.forEach(key=>{const tab=document.querySelector('#op-tab-'+key);tab.onclick=()=>activateOpportunityTab(key,false);});
  const list=document.querySelector('#section-nav');
  list.onkeydown=event=>{
    const current=keys.findIndex(key=>document.activeElement===document.querySelector('#op-tab-'+key));
    if(current<0)return;
    let next=current;
    if(event.key==='ArrowRight')next=(current+1)%keys.length;
    else if(event.key==='ArrowLeft')next=(current+keys.length-1)%keys.length;
    else if(event.key==='Home')next=0;
    else if(event.key==='End')next=keys.length-1;
    else return;
    event.preventDefault();activateOpportunityTab(keys[next],true);
  };
}
function render(d){
  detail=d;
  renderMeta(d);
  document.querySelector('#sections').innerHTML=
    `<div class="opportunity-panel" role="tabpanel" id="op-panel-overview" aria-labelledby="op-tab-overview">${renderOverview(d)}</div>`+
    `<div class="opportunity-panel" role="tabpanel" id="op-panel-fit" aria-labelledby="op-tab-fit" hidden>${renderFit(d)}${renderFilter(d)}</div>`+
    `<div class="opportunity-panel" role="tabpanel" id="op-panel-role" aria-labelledby="op-tab-role" hidden>${renderPosting(d)}</div>`+
    `<div class="opportunity-panel" role="tabpanel" id="op-panel-company" aria-labelledby="op-tab-company" hidden>${renderResearch(d)}</div>`+
    `<div class="opportunity-panel" role="tabpanel" id="op-panel-insights" aria-labelledby="op-tab-insights" hidden>${renderInsights(d)}</div>`+
    `<div class="opportunity-panel" role="tabpanel" id="op-panel-next" aria-labelledby="op-tab-next" hidden>${renderApplication(d)}${renderHistory(d)}</div>`;
  renderActions(d);
  wireOpportunityTabs();
  activateOpportunityTab('overview',false);
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

REVIEW_BODY = r"""<main class="wrap reference-studio">
<div class="studio-titlebar"><div><h1 class="studio-title">Application studio</h1><p class="studio-subtitle">Review and approve before submission.</p></div><a class="studio-back" href="/applications">Back to applications</a></div>
<div class="studio-role-meta"><strong id="title">Application</strong><span id="meta"></span></div>
<p class="line studio-errorline" id="line"></p>
<div class="studio-readiness" id="studio-readiness" aria-live="polite"><strong id="studio-status">Review status</strong><p id="studio-readiness-text">Loading package checks…</p></div>
<div class="studio-steps" role="tablist" aria-label="Application review steps" id="studio-steps"><button type="button" role="tab" id="studio-tab-resume" aria-controls="studio-panel-resume" aria-selected="true" tabindex="0" data-step="resume"><span class="step-number">1</span>Résumé</button><button type="button" role="tab" id="studio-tab-cover" aria-controls="studio-panel-cover" aria-selected="false" tabindex="-1" data-step="cover"><span class="step-number">2</span>Cover letter</button><button type="button" role="tab" id="studio-tab-answers" aria-controls="studio-panel-answers" aria-selected="false" tabindex="-1" data-step="answers"><span class="step-number">3</span>Answers</button><button type="button" role="tab" id="studio-tab-review" aria-controls="studio-panel-review" aria-selected="false" tabindex="-1" data-step="review"><span class="step-number">4</span>Review</button></div>
<div class="studio-layout"><nav class="studio-nav" aria-label="Application review sections" id="studio-nav"><button type="button" data-section="resume" data-step="resume" aria-current="true"><img src="/brand/icons/file-text.svg" width="13" height="13" alt="">Tailored résumé</button><button type="button" data-section="cover" data-step="cover" aria-current="false"><img src="/brand/icons/file-text.svg" width="13" height="13" alt="">Cover letter</button><button type="button" data-section="answers" data-step="answers" aria-current="false"><img src="/brand/icons/check-circle.svg" width="13" height="13" alt="">Application answers</button><button type="button" data-section="messages" data-step="review" aria-current="false"><img src="/brand/icons/briefcase.svg" width="13" height="13" alt="">Supporting materials</button><button type="button" data-section="decision" data-step="review" aria-current="false"><img src="/brand/icons/check-circle.svg" width="13" height="13" alt="">Review &amp; approve</button></nav><div class="studio-content" id="sections"><section class="card"><p>Loading...</p></section></div></div>
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
  document.querySelector('#meta').innerHTML=`Package rev. ${esc(d.package.revision)} · ${esc(d.package.status)} · ${esc(d.application_status||'no application state')}`;
  const actions=d.actions||{},unresolved=d.unresolved_questions||[];
  const answers=(d.materials||{}).answers||[],unverified=answers.filter(a=>a.level!=='VERIFIED').length;
  const warnings=(d.warnings||[]).length;
  const truthNeedsReview=d.package.truth_clean===false;
  const notes=[];
  if(unresolved.length)notes.push(`${unresolved.length} unresolved question${unresolved.length===1?'':'s'}`);
  if(unverified)notes.push(`${unverified} unverified answer${unverified===1?'':'s'}`);
  if(warnings)notes.push(`${warnings} warning${warnings===1?'':'s'}`);
  if(truthNeedsReview&&!unresolved.length&&!unverified)notes.push('Truth checks need review');
  const needsReview=unresolved.length||unverified||truthNeedsReview;
  const state=needsReview?'Needs information':actions.can_approve?'Ready for owner review':'Review required';
  if(!notes.length)notes.push(actions.can_approve?'No unresolved questions or warnings are recorded. Review the package and its content hash.': 'Review the package requirements and approval reason below.');
  document.querySelector('#studio-status').textContent=state;
  document.querySelector('#studio-readiness-text').textContent=notes.join(' · ');
}
function renderOpening(d){
  const j=d.job;
  return section('1. The original opening',facts([
    ['Title',j.title],['Company',j.company],['Location',j.location],['Pay',j.compensation_text],['Employment',j.employment_type],
    ['Posted',j.date_posted],['Posting link',link(j.source_url),true]])+
    `<details><summary>Full description</summary>${textBlock(j.description,'No description was captured.')}</details>`, '', 'opening');
}
function renderFit(d){
  const f=d.fit,a=d.analysis||{};
  const head=f?`<p><strong>Fit ${esc(pct(f.fit_score))}</strong> · confidence ${esc(pct(f.confidence))}</p>${f.why_this_job?`<p class="text">${esc(f.why_this_job)}</p>`:''}<div class="cols"><div><h3>Strengths</h3>${bullets(f.strengths)}</div><div><h3>Gaps</h3>${bullets(f.gaps)}</div><div><h3>Risks</h3>${bullets(f.risks)}</div></div>`:`<p class="muted">No stored fit assessment.</p><h3>Gaps</h3>${bullets(a.qualification_gaps)}`;
  return section('2. Fit analysis',`<p class="evidence-label">${f?'Engine assessment':'Recorded analysis'}</p>`+head+(a.fit_explanation?`<h3>Explanation</h3>${textBlock(a.fit_explanation)}`:''), '', 'fit-review');
}
function renderResume(d){return section('3. Tailored résumé',documentBlock((d.materials||{}).tailored_resume,'No tailored résumé on this package.'), '', 'resume');}
function renderCover(d){return section('4. Cover letter',documentBlock((d.materials||{}).cover_letter,'No cover letter on this package.'), '', 'cover');}
function renderAnswers(d){
  const rows=((d.materials||{}).answers||[]).map(a=>{
    const flags=[a.level==='VERIFIED'?'<span class="pill ok">verified</span>':'<span class="pill flag">not verified</span>'];
    if(a.sensitive)flags.push('<span class="pill flag">sensitive</span>');
    if(a.requires_user_input)flags.push('<span class="pill bad">needs your input</span>');
    return `<tr><td>${esc(a.question_text||a.question_key)}</td><td>${a.answer?esc(a.answer):'<span class="muted">(blank)</span>'}</td><td><span class="pill ${esc(a.level)}">${esc(a.level)}</span> ${flags.join(' ')}</td></tr>`;
  }).join('');
  return section('5. Proposed answers',rows?`<div class="table-scroll" role="region" aria-label="Proposed application answers" tabindex="0"><table class="plain"><thead><tr><th>Question</th><th>Answer</th><th>Truth level</th></tr></thead><tbody>${rows}</tbody></table></div>`:'<p class="muted">No answers proposed.</p>', '', 'answers');
}
function renderUnresolved(d){
  const u=d.unresolved_questions||[];
  return section('6. Unresolved questions',u.length?`<p>These must be answered before this package can be approved. Record them on the <a href="/answers">Answers page</a>, then refresh the package.</p>${bullets(u)}`:'<p>None recorded for this package.</p>', '', 'questions');
}
function renderWarnings(d){
  const w=d.warnings||[],g=d.generation_notes||[];
  return section('7. Warnings',`<h3>Package warnings</h3>${bullets(w)}<h3>How this package was written</h3>${bullets(g)}`, '', 'warnings');
}
function renderMessages(d){
  const m=d.materials||{};
  return `<section class="card" id="messages" tabindex="-1"><details><summary>8. Recruiter and hiring-manager messages, interview preparation</summary><h3>Recruiter message</h3>${textBlock(m.recruiter_message,'None.')}<h3>Hiring-manager message</h3>${textBlock(m.hiring_manager_message,'None.')}<h3>Interview preparation</h3>${textBlock(m.interview_preparation,'None.')}</details></section>`;
}
function renderDecision(d){
  const a=d.actions||{};
  const hash=d.package.content_hash;
  const refreshLabel='Refresh package';
  const unresolved=(d.unresolved_questions||[]).length;
  const readiness=a.can_approve?'Available for owner review. Approval applies to the exact hash shown below.':unresolved?`${unresolved} unresolved question${unresolved===1?'':'s'} remain.`:'Owner approval is unavailable for this package.';
  return `<section class="card decision" id="decision" tabindex="-1"><h2>Decision</h2><div class="readiness"><strong>Approval readiness</strong><p>${esc(readiness)}${unresolved?` ${unresolved} unresolved question${unresolved===1?'':'s'} recorded.`:''}</p></div>
<p>Approving binds to this exact package. Content hash: <code>${esc(hash)}</code></p>
<div class="actions"><button type="button" id="approve" ${a.can_approve?'':'disabled'}>Approve application</button><button type="button" class="secondary" id="refresh" ${a.refresh?'':'disabled'}>${refreshLabel}</button><button type="button" class="secondary" id="reject" ${a.can_reject?'':'disabled'}>Reject</button></div>
${a.can_approve?'':`<p class="err" id="why-not">${esc(a.approve_reason||'')}</p>`}
${a.refresh?'':`<p class="muted">${esc(a.refresh_reason||'')}</p>`}
${a.can_reject?'':`<p class="muted">${esc(a.reject_reason||'')}</p>`}</section>`;
}
function render(d){
  review=d;
  renderMeta(d);
  document.querySelector('#sections').innerHTML=
    `<div class="studio-panel" role="tabpanel" id="studio-panel-resume" aria-labelledby="studio-tab-resume" data-step-panel="resume">${renderResume(d)}</div>`+
    `<div class="studio-panel" role="tabpanel" id="studio-panel-cover" aria-labelledby="studio-tab-cover" data-step-panel="cover" hidden>${renderCover(d)}</div>`+
    `<div class="studio-panel" role="tabpanel" id="studio-panel-answers" aria-labelledby="studio-tab-answers" data-step-panel="answers" hidden>${renderAnswers(d)}</div>`+
    `<div class="studio-panel" role="tabpanel" id="studio-panel-review" aria-labelledby="studio-tab-review" data-step-panel="review" hidden>${renderOpening(d)}${renderFit(d)}</div>`+
    `<div class="studio-panel studio-review-followup" role="tabpanel" id="studio-panel-review-followup" aria-labelledby="studio-tab-review" data-step-panel="review" hidden>${renderUnresolved(d)}${renderWarnings(d)}${renderMessages(d)}${renderDecision(d)}</div>`;
  const wire=(sel,fn)=>{const el=document.querySelector(sel);if(el)el.addEventListener('click',fn);};
  wire('#approve',approve);wire('#refresh',refresh);wire('#reject',reject);
  wireStudioNavigation();
  activateStudioStep('resume',false);
}
function studioSectionStep(target){return target==='resume'?'resume':target==='cover'?'cover':target==='answers'?'answers':'review';}
function activateStudioStep(name,focus){
  ['resume','cover','answers','review'].forEach(key=>{
    const tab=document.querySelector('#studio-tab-'+key),panel=document.querySelector('#studio-panel-'+key);
    if(!tab||!panel)return;
    const active=key===name;
    tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;
    panel.hidden=!active;
    if(key==='review'){const followup=document.querySelector('#studio-panel-review-followup');if(followup)followup.hidden=!active;}
    if(active&&focus)tab.focus();
  });
  const first=document.querySelector(`#studio-nav button[data-step="${name}"]`);
  document.querySelectorAll('#studio-nav button').forEach(btn=>btn.setAttribute('aria-current',String(btn===first)));
}
function activateStudioSection(target,focus){
  const step=studioSectionStep(target);
  activateStudioStep(step,false);
  const sectionButton=document.querySelector(`#studio-nav button[data-section="${target}"]`);
  document.querySelectorAll('#studio-nav button').forEach(btn=>btn.setAttribute('aria-current',String(btn===sectionButton)));
  const section=document.getElementById(target);
  if(section){section.scrollIntoView({block:'start'});if(focus)section.focus();}
}
function wireStudioNavigation(){
  const keys=['resume','cover','answers','review'];
  keys.forEach(key=>{const tab=document.querySelector('#studio-tab-'+key);tab.onclick=()=>activateStudioStep(key,false);});
  document.querySelector('#studio-steps').onkeydown=event=>{
    const current=keys.findIndex(key=>document.activeElement===document.querySelector('#studio-tab-'+key));
    if(current<0)return;
    let next=current;
    if(event.key==='ArrowRight')next=(current+1)%keys.length;
    else if(event.key==='ArrowLeft')next=(current+keys.length-1)%keys.length;
    else if(event.key==='Home')next=0;
    else if(event.key==='End')next=keys.length-1;
    else return;
    event.preventDefault();activateStudioStep(keys[next],true);
  };
  document.querySelectorAll('#studio-nav button').forEach(btn=>{btn.onclick=()=>activateStudioSection(btn.dataset.section,true);});
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
    ".stage-nav{display:flex;gap:8px 12px;flex-wrap:wrap;margin:12px 0 18px}"
    ".stage-nav a{display:inline-flex;text-decoration:none}"
    ".app{background:rgba(255,255,255,.28);border-radius:10px;padding:14px;margin:8px 0}"
    ".app:focus-within{outline:2px solid var(--forest,#29483E);outline-offset:2px}"
    ".app-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}"
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
<p class="eyebrow">Pursuit pipeline</p><h1>Pipeline</h1>
<p>Every application past approval, by where it stands. Record what an employer does here; each outcome moves the application through the same lifecycle the engine uses and leaves an event.</p>
<p class="line" id="line"></p>
<nav class="stage-nav chips" aria-label="Application stages" id="chips"></nav>
<div id="stages"><section class="card"><p>Loading...</p></section></div>
</main>
<script>
__ESC__
__FORMAT__
let labels={};
function stageId(label,index){return 'stage-'+index;}
function renderRow(a){
  const kinds=(a.allowed_outcomes||[]).map(k=>`<option value="${esc(k)}">${esc(labels[k]||k)}</option>`).join('');
  const control=kinds?`<div class="outcome" data-id="${esc(a.application_id)}"><select class="oc-kind" aria-label="Outcome">${kinds}</select><input class="oc-note" placeholder="Note (optional)" maxlength="4000" aria-label="Note"><input class="oc-contact" placeholder="Who (optional)" maxlength="500" aria-label="Contact"><input class="oc-at" type="date" aria-label="Date it happened (optional)"><button type="button" class="oc-go small">Record outcome</button></div>`:'<p class="muted">No outcome can be recorded in this state.</p>';
  const last=a.last_event?`<br><span class="muted">Last: ${esc(a.last_event.event_type)} · ${esc(when(a.last_event.occurred_at))}${a.last_event.summary?' · '+esc(a.last_event.summary):''}</span>`:'';
  const sent=a.submitted_at?`Submitted ${esc(when(a.submitted_at))}${a.submission_method?' by '+esc(String(a.submission_method).replace(/_/g,' ').toLowerCase()):''}`:'Not submitted';
  return `<article class="app"><div class="top"><div><strong>${esc(a.title)}</strong> at ${esc(a.company)} <span class="pill">${esc(a.application_status)}</span><br><span class="muted">${sent}</span>${last}</div><div class="app-actions"><a href="${esc('/packages/'+encodeURIComponent(a.package_id))}">Review application</a><button type="button" class="secondary small hist" data-id="${esc(a.application_id)}">View history</button></div></div>${control}<div class="hist-box" hidden></div></article>`;
}
function renderStages(stages){
  const filled=stages.filter(s=>s.count>0);
  document.querySelector('#chips').innerHTML=stages.map((s,i)=>s.count?`<a class="pill" href="#${stageId(s.label,i)}">${esc(s.label)} ${esc(s.count)}</a>`:`<span class="pill">${esc(s.label)} ${esc(s.count)}</span>`).join('');
  document.querySelector('#stages').innerHTML=filled.length?stages.map((s,i)=>s.count?section(s.label+' ('+s.count+')',s.applications.map(renderRow).join(''), '', stageId(s.label,i)):'').join(''):'<section class="card"><p>Nothing has been approved yet. Approved and submitted applications appear here.</p></section>';
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
