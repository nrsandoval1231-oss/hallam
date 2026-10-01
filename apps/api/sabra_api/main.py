"""Local browser surface over the Sabra engine."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sabra_domain import models
from sabra_domain.enums import ApplicationStatus, PackageStatus, TruthLevel
from sabra_domain.errors import SabraError
from sabra_domain.repositories import ApplicationRepository, OpportunityRepository
from sabra_engine.answer_memory import AnswerMemoryService
from sabra_engine.approval import ApprovalService
from sabra_engine.candidate_intake import ensure_candidate
from sabra_engine.context import EngineContext
from sabra_engine.documents import DocumentService, package_summary

app = FastAPI(title="Sabra Job Engine", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _status_payload() -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            profile_name = None
            if candidate:
                try:
                    profile_name = repos.candidates.active_profile(candidate.id).identity.full_name
                except Exception:
                    profile_name = None
            return {
                "generated_at": datetime.now(UTC).isoformat(),
                "candidate_id": candidate.id if candidate else None,
                "candidate_name": profile_name or (candidate.display_name if candidate else None),
                "jobs_discovered": repos.session.query(models.Job).count(),
                "opportunities": repos.session.query(models.Opportunity).count(),
                "shortlisted": sum(
                    1
                    for item in repos.opportunities.list_for_candidate(candidate.id)
                    if item.shortlisted
                )
                if candidate
                else 0,
                "packages": repos.session.query(models.ApplicationPackageRow).count(),
                "applications": repos.session.query(models.ApplicationRow).count(),
                "jobs_by_status": repos.jobs.count_by_status(),
                "opportunities_by_status": OpportunityRepository(repos.session).count_by_status(
                    candidate.id if candidate else None
                ),
                "applications_by_status": ApplicationRepository(repos.session).count_by_status(
                    candidate.id if candidate else None
                ),
            }
    finally:
        ctx.close()


@app.get("/api/candidate")
def candidate_profile() -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return {"candidate_id": None, "profile": None, "resume": None}
            try:
                profile = repos.candidates.active_profile(candidate.id)
                profile_payload = profile.model_dump(mode="json")
                provenance = {
                    key: value.model_dump(mode="json") for key, value in profile.provenance.items()
                }
            except Exception:
                profile_payload = None
                provenance = {}
            resume = repos.resumes.master(candidate.id)
            return {
                "candidate_id": candidate.id,
                "candidate_name": candidate.display_name,
                "profile": profile_payload,
                "provenance": provenance,
                "resume": {
                    "id": resume.id,
                    "version": resume.version,
                    "label": resume.label,
                    "content_hash": resume.content_hash,
                }
                if resume
                else None,
            }
    finally:
        ctx.close()


@app.post("/api/candidate/resume")
async def upload_resume(resume: Annotated[UploadFile, File(...)]) -> dict[str, Any]:
    content = await resume.read()
    if resume.filename and resume.filename.lower().endswith(".pdf"):
        import fitz

        pdf = fitz.open(stream=content, filetype="pdf")
        text = "\n".join(page.get_text() for page in pdf)
        source_format = "pdf"
    else:
        text = content.decode("utf-8")
        source_format = "text"
    if not text.strip():
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="The uploaded resume contains no readable text")
    ctx = EngineContext.create()
    try:
        candidate_id = ensure_candidate(ctx, display_name=None, create_placeholder=True)
        resume_id = DocumentService(ctx).ingest_master_resume(
            candidate_id,
            text,
            label=resume.filename or "Master resume",
            source_format=source_format,
            source_path=resume.filename,
        )
        return {
            "candidate_id": candidate_id,
            "resume_id": resume_id,
            "truth_level": TruthLevel.INFERRED.value,
            "message": "Resume stored as evidence. Resume-derived profile fields remain inferred until confirmed by the candidate.",
        }
    finally:
        ctx.close()


@app.get("/api/status")
def status() -> dict[str, Any]:
    return _status_payload()


@app.get("/api/opportunities")
def opportunity_list() -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return {"opportunities": []}
            rows = []
            for item in repos.opportunities.shortlist(candidate.id):
                jobs = repos.opportunities.list_jobs(item.id)
                job = jobs[0] if jobs else None
                fit = item.fit_assessment
                rows.append(
                    {
                        "id": item.id,
                        "title": item.title,
                        "company": item.company,
                        "location": item.location,
                        "salary_min": job.salary_min if job else None,
                        "salary_max": job.salary_max if job else None,
                        "industry": job.department if job else None,
                        "rank_score": item.rank_score,
                        "rank_position": item.rank_position,
                        "status": item.status,
                        "shortlist_reason": item.shortlist_reason,
                        "source_url": job.source_url if job else None,
                        "fit_score": fit.fit_score if fit else None,
                        "confidence": fit.confidence if fit else None,
                        "why_this_job": fit.why_this_job if fit else None,
                        "strengths": fit.strengths if fit else [],
                        "gaps": fit.gaps if fit else [],
                    }
                )
            return {"opportunities": rows}
    finally:
        ctx.close()


class AnswerWrite(BaseModel):
    key: str
    value: str = ""
    question_text: str | None = None


class PackageSelection(BaseModel):
    package_id: str
    content_hash: str


class BatchApprovalRequest(BaseModel):
    approved_by: str = "owner"
    packages: list[PackageSelection] = Field(default_factory=list)


def _package_rows() -> list[dict[str, Any]]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return []
            rows = []
            for row in repos.packages.list_all(candidate.id):
                summary = package_summary(row)
                opportunity = repos.opportunities.get(row.opportunity_id)
                application = repos.applications.for_opportunity(row.opportunity_id)
                summary["title"] = opportunity.title
                summary["company"] = opportunity.company
                summary["application_id"] = application.id if application else None
                summary["application_status"] = application.status if application else None
                summary["approvable"] = bool(
                    row.status == PackageStatus.COMPLETE.value
                    and row.truth_clean
                    and application is not None
                    and application.status == ApplicationStatus.AWAITING_APPROVAL.value
                )
                rows.append(summary)
            return rows
    finally:
        ctx.close()


@app.get("/api/packages")
def package_list() -> dict[str, Any]:
    return {"packages": _package_rows()}


@app.get("/api/answers")
def answer_list() -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return {"answers": AnswerMemoryService(ctx).list_answers()}
    except SabraError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        ctx.close()


@app.post("/api/answers/record")
def answer_record(body: AnswerWrite) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return AnswerMemoryService(ctx).record(body.key, body.value, question_text=body.question_text)
    except SabraError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        ctx.close()


@app.post("/api/answers/verify")
def answer_verify(body: AnswerWrite) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return AnswerMemoryService(ctx).verify(body.key)
    except SabraError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        ctx.close()


@app.post("/api/packages/approve")
def approve_packages(body: BatchApprovalRequest) -> dict[str, Any]:
    """Approve the selected COMPLETE packages. Each approval is bound to the hash sent."""

    ctx = EngineContext.create()
    try:
        result = ApprovalService(ctx).approve_selected(
            [item.model_dump() for item in body.packages],
            approved_by=body.approved_by,
        )
        return result.as_dict()
    finally:
        ctx.close()


@app.post("/api/opportunities/{opportunity_id}/prepare")
def prepare_opportunity(opportunity_id: str) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return DocumentService(ctx).prepare_package(opportunity_id).as_dict()
    finally:
        ctx.close()


@app.get("/opportunities", response_class=HTMLResponse)
def opportunities() -> str:
    data = _status_payload()
    html = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sabra | Opportunities</title>
<style>body{{margin:0;background:#f4f7f3;color:#18332f;font:15px/1.5 system-ui,sans-serif}}main{{max-width:920px;margin:0 auto;padding:48px 24px}}a{{color:#62877a;font-weight:700;text-decoration:none}}h1{{font-size:38px;letter-spacing:-.04em;margin:28px 0 8px}}p{{color:#6c7f7a}}.summary{{display:flex;gap:12px;flex-wrap:wrap;margin:28px 0}}.stat{{background:#fff;border:1px solid #dce6df;border-radius:10px;padding:14px 18px}}.stat strong{{display:block;font-size:25px}}.filters{{display:flex;gap:10px;flex-wrap:wrap;background:#fff;border:1px solid #dce6df;border-radius:12px;padding:16px;margin-bottom:18px}}input{{border:1px solid #cbd9d1;border-radius:7px;padding:9px;width:150px}}button{{background:#18332f;color:#fff;border:0;border-radius:7px;padding:9px 14px;font-weight:700}}.queue{{background:#fff;border:1px solid #dce6df;border-radius:12px;padding:22px}}.row{{border-top:1px solid #dce6df;padding:16px 0;display:flex;justify-content:space-between;gap:16px}}.row:first-child{{margin-top:8px}}.score{{color:#62877a;font-weight:750}}</style></head>
<body><main><a href="/">Back to overview</a><h1>Opportunities</h1><p>Ranked roles from the current discovery and intelligence run.</p><section class="summary"><div class="stat"><strong>__OPPORTUNITIES__</strong> opportunities</div><div class="stat"><strong>__SHORTLISTED__</strong> shortlisted</div><div class="stat"><strong>__JOBS__</strong> jobs discovered</div></section><form class="filters" id="filters"><input name="location" placeholder="Location"><input name="min_salary" type="number" placeholder="Min salary"><input name="max_salary" type="number" placeholder="Max salary"><input name="industry" placeholder="Industry"><button type="submit">Filter jobs</button><button type="button" id="clear">Clear</button></form><section class="queue" id="queue"><h2>Shortlist</h2><div class="row"><div><strong>Loading shortlisted opportunities...</strong></div></div></section><script>let all=[];async function prepare(id){{const r=await fetch(`/api/opportunities/${{id}}/prepare`,{{method:'POST'}});const d=await r.json();alert(r.ok?`Package ${{d.status}}. Unresolved questions: ${{d.unresolved_questions.length}}`:d.detail||'Preparation failed')}}function render(rows){{const q=document.querySelector('#queue');q.innerHTML='<h2>Shortlist</h2>'+ (rows.length?rows.map(o=>`<div class="row"><div><strong>${{o.rank_position??''}}. ${{o.title}}</strong><br><span>${{o.company}} · ${{o.location||'Location not listed'}} · ${{o.status}}</span><br><span>${{o.why_this_job||o.shortlist_reason||'Shortlisted by the ranking engine.'}}</span></div><span class="score">${{o.fit_score==null?'Fit pending':`Fit ${{Math.round(o.fit_score*100)}}%`}}<br><button type="button" onclick="prepare('${{o.id}}')">Prepare application</button></span></div>`).join(''):'<div class="row"><div><strong>No jobs match those filters.</strong></div></div>') }fetch('/api/opportunities').then(r=>r.json()).then(d=>{{all=d.opportunities;render(all)}}).catch(()=>{{document.querySelector('#queue').innerHTML='<h2>Shortlist</h2><div class="row"><div><strong>The opportunity queue could not be loaded.</strong></div></div>'}});document.querySelector('#filters').addEventListener('submit',e=>{{e.preventDefault();const f=new FormData(e.target),loc=(f.get('location')||'').toString().toLowerCase(),ind=(f.get('industry')||'').toString().toLowerCase(),min=Number(f.get('min_salary'))||0,max=Number(f.get('max_salary'))||Infinity;render(all.filter(o=>(!loc||`${{o.location}}`.toLowerCase().includes(loc))&&(!ind||`${{o.company}} ${{o.title}}`.toLowerCase().includes(ind))&&(!min||o.salary_max>=min)&&(!isFinite(max)||o.salary_min<=max)))}});document.querySelector('#clear').onclick=()=>{{document.querySelector('#filters').reset();render(all)}};</script></main></body></html>"""
    return (
        html.replace("{{", "{")
        .replace("}}", "}")
        .replace("__OPPORTUNITIES__", str(data["opportunities"]))
        .replace("__SHORTLISTED__", str(data["shortlisted"]))
        .replace("__JOBS__", str(data["jobs_discovered"]))
    )


@app.get("/applications", response_class=HTMLResponse)
def applications_page() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sabra | Applications</title>
<style>body{margin:0;background:#f4f7f3;color:#18332f;font:15px/1.5 system-ui,sans-serif}main{max-width:920px;margin:0 auto;padding:48px 24px}a{color:#62877a;font-weight:700;text-decoration:none}h1{font-size:38px;letter-spacing:-.04em;margin:28px 0 8px}p{color:#6c7f7a}.row{display:flex;gap:12px;align-items:flex-start;background:#fff;border:1px solid #dce6df;border-radius:10px;padding:14px;margin:10px 0}button{background:#18332f;color:#fff;border:0;border-radius:7px;padding:11px 16px;font-weight:700}code{font-size:12px}</style></head>
<body><main><a href="/">Back to overview</a><h1>Applications</h1>
<p>Select complete packages and approve them together. Each approval is stored against that package's content hash, so a later edit invalidates it.</p>
<form id="batch"><div id="list">Loading packages...</div><button type="submit" id="approve">Approve selected</button></form>
<p id="result"></p>
<script>
async function load(){
  const r=await fetch('/api/packages');
  const d=await r.json();
  const list=document.querySelector('#list');
  const rows=(d.packages||[]);
  if(!rows.length){list.textContent='No packages yet.';return;}
  list.innerHTML=rows.map(p=>`<label class="row"><input type="checkbox" name="package" value="${p.package_id}" data-hash="${p.content_hash}" ${p.approvable?'':'disabled'}><span><strong>${p.title||'Package'} at ${p.company||'the company'}</strong><br>${p.status}${p.approvable?' · ready to approve':' · not approvable'}<br><code>${p.content_hash}</code></span></label>`).join('');
}
document.querySelector('#batch').addEventListener('submit',async e=>{
  e.preventDefault();
  const chosen=[...document.querySelectorAll('input[name=package]:checked')].map(el=>({package_id:el.value,content_hash:el.dataset.hash}));
  const line=document.querySelector('#result');
  if(!chosen.length){line.textContent='Select at least one complete package.';return;}
  const r=await fetch('/api/packages/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approved_by:'owner',packages:chosen})});
  const d=await r.json();
  const ok=(d.approved||[]).length, no=(d.refused||[]).length;
  line.textContent=`Approved ${ok}. Refused ${no}.`+(no?(' '+d.refused.map(x=>x.message).join(' ')):'');
  load();
});
load();
</script></main></body></html>"""


@app.get("/answers", response_class=HTMLResponse)
def answers_page() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sabra | Answers</title>
<style>body{margin:0;background:#f4f7f3;color:#18332f;font:15px/1.5 system-ui,sans-serif}main{max-width:920px;margin:0 auto;padding:48px 24px}a{color:#62877a;font-weight:700;text-decoration:none}h1{font-size:38px;letter-spacing:-.04em;margin:28px 0 8px}p,label{color:#6c7f7a}form,li{background:#fff;border:1px solid #dce6df;border-radius:10px;padding:14px;margin:12px 0}input,select{display:block;width:100%;margin:6px 0 12px;padding:8px;border:1px solid #cbd9d1;border-radius:7px}button{background:#18332f;color:#fff;border:0;border-radius:7px;padding:9px 14px;font-weight:700}</style></head>
<body><main><a href="/">Back to overview</a><h1>Answer memory</h1>
<p>Record an answer, then verify it. Only a verified answer is typed onto a form. Salary, background check, and the other sensitive questions stay blank until you verify them.</p>
<form id="record"><label>Question key<select name="key"><option>salary_expectations</option><option>background_check</option><option>veteran_status</option><option>disability_status</option><option>gender</option><option>race</option><option>ethnicity</option><option>date_of_birth</option><option>criminal_history</option><option>work_authorization</option><option>sponsorship</option><option>relocation</option><option>travel</option><option>start_date</option></select></label><label>Answer<input name="value" required></label><label>Question wording, optional<input name="question_text"></label><button type="submit">Record answer</button></form>
<p id="line"></p><ul id="saved"></ul>
<script>
async function load(){
  const r=await fetch('/api/answers');
  const d=await r.json();
  const ul=document.querySelector('#saved');
  ul.innerHTML=(d.answers||[]).map(a=>`<li><strong>${a.question_key}</strong> · ${a.level}${a.sensitive?' · sensitive':''}<br>${a.value??''} ${a.verified?'':`<button type="button" data-key="${a.question_key}">Verify</button>`}</li>`).join('')||'<li>No answers recorded.</li>';
  ul.querySelectorAll('button[data-key]').forEach(btn=>btn.addEventListener('click',async()=>{
    const r=await fetch('/api/answers/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:btn.dataset.key,value:''})});
    const d=await r.json();
    document.querySelector('#line').textContent=r.ok?`${d.question_key} verified.`:(d.detail||'Verify failed');
    load();
  }));
}
document.querySelector('#record').addEventListener('submit',async e=>{
  e.preventDefault();
  const f=new FormData(e.target);
  const r=await fetch('/api/answers/record',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:f.get('key'),value:f.get('value'),question_text:f.get('question_text')||null})});
  const d=await r.json();
  document.querySelector('#line').textContent=r.ok?`${d.question_key} recorded. Verify it before it can fill a form.`:(d.detail||'Record failed');
  e.target.querySelector('[name=value]').value='';
  load();
});
load();
</script></main></body></html>"""


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sabra | Job search operator</title>
<style>
:root{color-scheme:light;--ink:#18332f;--muted:#6c7f7a;--paper:#f4f7f3;--panel:#fff;--line:#dce6df;--sage:#62877a;--pale:#e6f0ea;--warm:#f4eadb}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.shell{min-height:100vh;display:grid;grid-template-columns:232px 1fr}.rail{padding:28px 20px;border-right:1px solid var(--line);background:#edf4ef}.brand{font-size:24px;font-weight:750;letter-spacing:-.04em}.brand small{display:block;margin-top:3px;color:var(--muted);font-size:11px;letter-spacing:.12em;text-transform:uppercase}.nav{margin-top:64px;display:grid;gap:6px}.nav a{padding:10px 12px;border-radius:9px;color:var(--muted);text-decoration:none}.nav a.active,.nav a:hover{background:var(--panel);color:var(--ink);box-shadow:0 1px 4px #18332f10}.rail-note{position:absolute;bottom:24px;width:190px;color:var(--muted);font-size:12px}.main{max-width:1320px;width:100%;padding:36px 48px 64px}.top{display:flex;align-items:end;justify-content:space-between;gap:20px}.eyebrow{color:var(--sage);font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase}.title{margin:7px 0 0;font-size:38px;line-height:1.05;letter-spacing:-.045em}.sub{margin:12px 0 0;color:var(--muted);max-width:580px}.button{border:0;border-radius:8px;padding:11px 16px;background:var(--ink);color:#fff;font-weight:700;cursor:pointer}.button:hover{background:#2b554b}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:34px 0}.metric{padding:18px 20px;background:var(--panel);border:1px solid var(--line);border-radius:12px}.metric-label{color:var(--muted);font-size:12px}.metric-value{margin-top:6px;font-size:30px;font-weight:750;letter-spacing:-.05em}.content{display:grid;grid-template-columns:1.35fr .65fr;gap:18px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px}.panel h2{margin:0;font-size:18px;letter-spacing:-.02em}.panel p{color:var(--muted);font-size:13px}.queue{margin-top:18px;display:grid;gap:10px}.queue-item{display:flex;justify-content:space-between;gap:18px;padding:14px 0;border-top:1px solid var(--line)}.queue-item strong{display:block}.queue-item span{color:var(--muted);font-size:13px}.score{color:var(--sage);font-weight:750}.readiness{background:var(--pale);padding:16px;border-radius:9px;margin-top:16px}.readiness strong{display:block;margin-bottom:5px}.empty{color:var(--muted);padding:26px 0}.status-line{margin-top:18px;color:var(--muted);font-size:12px}
@media(max-width:850px){.shell{display:block}.rail{border-right:0;padding:18px 20px}.nav{margin-top:18px;display:flex;overflow:auto}.rail-note{display:none}.main{padding:28px 20px}.top{display:block}.top .button{margin-top:18px}.metrics{grid-template-columns:repeat(2,1fr)}.content{grid-template-columns:1fr}}
</style></head>
<body><div class="shell"><aside class="rail"><div class="brand">Sabra<small>Job search operator</small></div><nav class="nav"><a class="active" href="/">Overview</a><a href="/opportunities">Opportunities</a><a href="/applications">Applications</a><a href="/answers">Answers</a><a href="#candidate">Candidate</a></nav><div class="rail-note">You approve complete packages in a batch. A CAPTCHA is solved only by you.</div></aside><main class="main"><header class="top"><div><div class="eyebrow">Operator overview</div><h1 class="title">Find the work worth pursuing.</h1><p class="sub">A truthful queue of opportunities, prepared applications, and the next decision that belongs to you.</p></div><button class="button" id="refresh">Refresh data</button></header><section class="metrics" id="metrics"><div class="metric"><div class="metric-label">Jobs discovered</div><div class="metric-value" data-key="jobs_discovered">-</div></div><div class="metric"><div class="metric-label">Opportunities</div><div class="metric-value" data-key="opportunities">-</div></div><div class="metric"><div class="metric-label">Shortlisted</div><div class="metric-value" data-key="shortlisted">-</div></div><div class="metric"><div class="metric-label">Packages ready</div><div class="metric-value" data-key="packages">-</div></div></section><section class="content"><div class="panel" id="opportunities"><h2>Opportunity queue</h2><p>The strongest roles rise here after deterministic filtering and fit assessment.</p><div class="queue"><div class="queue-item"><div><strong>Run ranking to populate this queue</strong><span>Ranking uses your search intent and candidate evidence.</span></div><span class="score">Awaiting data</span></div></div></div><div class="panel" id="candidate"><h2>Candidate readiness</h2><p id="candidate-copy">Loading candidate profile...</p><form id="resume-form" style="margin-top:16px"><label for="resume" style="display:block;color:var(--muted);font-size:12px;margin-bottom:6px">Master resume</label><input id="resume" name="resume" type="file" accept=".pdf,.txt" required><button class="button" type="submit" style="margin-top:10px">Upload resume</button><div class="status-line" id="upload-line"></div></form><div class="readiness"><strong>Truth boundary</strong><span>Verified, inferred, and unknown facts remain distinct before an application is prepared.</span></div><div class="status-line" id="status-line">Connecting to the local engine...</div></div></section></main></div><script>
async function refresh(){const line=document.querySelector('#status-line');try{const r=await fetch('/api/status');if(!r.ok)throw new Error('status '+r.status);const d=await r.json();document.querySelectorAll('[data-key]').forEach(e=>e.textContent=d[e.dataset.key]??0);document.querySelector('#candidate-copy').textContent=d.candidate_name?`${d.candidate_name} is loaded in the local engine.`:'No candidate profile loaded yet.';line.textContent=`Updated ${new Date(d.generated_at).toLocaleTimeString()}.`; }catch(e){line.textContent='The local engine could not be reached.';console.error(e)}}document.querySelector('#refresh').addEventListener('click',refresh);refresh();document.querySelector('#resume-form').addEventListener('submit',async e=>{e.preventDefault();const file=document.querySelector('#resume').files[0];const line=document.querySelector('#upload-line');if(!file){line.textContent='Choose a PDF or text resume first.';return;}line.textContent='Uploading and parsing...';const body=new FormData();body.append('resume',file);try{const r=await fetch('/api/candidate/resume',{method:'POST',body});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Upload failed');line.textContent='Resume stored as inferred evidence. Refreshing profile...';refresh()}catch(error){line.textContent=error.message}});
</script></body></html>"""
