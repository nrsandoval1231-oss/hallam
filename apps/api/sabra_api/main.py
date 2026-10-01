"""Local browser surface over the Sabra engine."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sabra_automation.form_keys import answer_key_choices
from sabra_domain import models
from sabra_domain.enums import ApplicationStatus, PackageStatus, TruthLevel
from sabra_domain.errors import (
    ApprovalRequired,
    ApprovalScopeMismatch,
    InvalidTransition,
    NotFound,
    SabraError,
)
from sabra_domain.repositories import ApplicationRepository, OpportunityRepository
from sabra_engine.answer_memory import AnswerMemoryService
from sabra_engine.approval import ApprovalService
from sabra_engine.candidate_intake import ensure_candidate
from sabra_engine.context import EngineContext
from sabra_engine.documents import DocumentService, package_summary
from sabra_engine.execution import ExecutionService
from sabra_engine.refresh import PackageRefreshService

app = FastAPI(title="Sabra Job Engine", version="0.1.0")


@app.exception_handler(SabraError)
async def sabra_error_handler(_request: Request, exc: SabraError) -> JSONResponse:
    """Operator mistakes are JSON the pages can show, not a plain-text 500."""

    status = 422
    if isinstance(exc, (InvalidTransition, ApprovalRequired, ApprovalScopeMismatch)):
        status = 409
    elif isinstance(exc, NotFound):
        status = 404
    lowered = str(exc).lower()
    if "already approved" in lowered or "was not refreshed" in lowered or "already submitted" in lowered:
        status = 409
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _latest_packages(repos: Any, candidate_id: str | None) -> list[Any]:
    """One row per opportunity: the highest revision. Older incomplete rows stay stored."""

    if candidate_id is None:
        return []
    latest: dict[str, Any] = {}
    for row in repos.packages.list_all(candidate_id):
        current = latest.get(row.opportunity_id)
        if current is None or (row.revision or 0) > (current.revision or 0):
            latest[row.opportunity_id] = row
    return list(latest.values())


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
                "packages_latest": len(_latest_packages(repos, candidate.id)) if candidate else 0,
                "automation_dry_run": ctx.settings.automation_dry_run,
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


def _annotate_package(repos: Any, row: Any, *, dry_run: bool) -> dict[str, Any]:
    summary = package_summary(row)
    opportunity = repos.opportunities.get(row.opportunity_id)
    application = repos.applications.for_opportunity(row.opportunity_id)
    payload = dict(row.payload or {})
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
    summary["cover_letter"] = payload.get("cover_letter")
    summary["hiring_manager_message"] = payload.get("hiring_manager_message")
    summary["tailored_resume_markdown"] = payload.get("tailored_resume_markdown")
    summary["candidate_summary"] = payload.get("candidate_summary")
    summary["unresolved_questions"] = list(payload.get("unresolved_questions") or [])
    if application is not None and application.status == ApplicationStatus.APPROVED.value:
        summary["review_label"] = "Approved"
        summary["next_step"] = (
            "Dry run is on, so Apply stays off until SABRA_AUTOMATION_DRY_RUN=false. "
            "Then confirm Apply. You solve any CAPTCHA; the engine does not."
            if dry_run
            else (
                "Apply. A form with no challenge is submitted. "
                "A CAPTCHA opens in a headed browser for you to solve."
            )
        )
    elif summary["approvable"]:
        summary["review_label"] = "ready to approve"
        summary["next_step"] = "Read the package, then approve it with the others you select."
    else:
        summary["review_label"] = "not approvable"
        summary["next_step"] = None
    return summary


def _package_rows() -> tuple[list[dict[str, Any]], bool]:
    ctx = EngineContext.create()
    try:
        dry_run = ctx.settings.automation_dry_run
        with ctx.read_repos() as repos:
            candidate = repos.candidates.primary()
            if candidate is None:
                return [], dry_run
            rows = [
                _annotate_package(repos, row, dry_run=dry_run)
                for row in _latest_packages(repos, candidate.id)
            ]
            return rows, dry_run
    finally:
        ctx.close()


@app.get("/api/packages")
def package_list() -> dict[str, Any]:
    rows, dry_run = _package_rows()
    return {"packages": rows, "automation_dry_run": dry_run}


@app.get("/api/packages/{package_id}")
def package_detail(package_id: str) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        with ctx.read_repos() as repos:
            row = repos.packages.get(package_id)
            return _annotate_package(repos, row, dry_run=ctx.settings.automation_dry_run)
    finally:
        ctx.close()


class ApplyRequest(BaseModel):
    confirm: bool = False


@app.post("/api/applications/refresh-unsubmitted")
def refresh_unsubmitted(body: ApplyRequest) -> dict[str, Any]:
    """Revoke approval and rebuild every approved application that has not been submitted."""

    if not body.confirm:
        raise HTTPException(
            status_code=422,
            detail="Confirm before refreshing unsubmitted applications. Nothing was changed.",
        )
    ctx = EngineContext.create()
    try:
        return PackageRefreshService(ctx).refresh_all_unsubmitted().as_dict()
    finally:
        ctx.close()


@app.post("/api/applications/{application_id}/refresh")
def refresh_application(application_id: str) -> dict[str, Any]:
    ctx = EngineContext.create()
    try:
        return PackageRefreshService(ctx).refresh(application_id).as_dict()
    finally:
        ctx.close()


@app.post("/api/applications/{application_id}/apply")
def apply_application(application_id: str, body: ApplyRequest) -> dict[str, Any]:
    """Run the same submission path as `sabra apply`.

    Refused while dry-run is on, and refused without an explicit confirm.
    A CAPTCHA is never solved here.
    """

    if not body.confirm:
        raise HTTPException(
            status_code=422,
            detail="Confirm the submission before applying. Nothing was opened or submitted.",
        )
    ctx = EngineContext.create()
    try:
        if ctx.settings.automation_dry_run:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Dry run is on. Set SABRA_AUTOMATION_DRY_RUN=false to apply. "
                    "Nothing was opened or submitted."
                ),
            )
        result = ExecutionService(ctx).submit(application_id, allow_real_submission=True)
        return result.as_dict()
    finally:
        ctx.close()


@app.get("/api/answer-keys")
def answer_keys() -> dict[str, Any]:
    return {"keys": answer_key_choices()}


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
<body><main><a href="/">Back to overview</a><h1>Opportunities</h1><p>Ranked roles from the current discovery and intelligence run.</p><section class="summary"><div class="stat"><strong>__OPPORTUNITIES__</strong> opportunities</div><div class="stat"><strong>__SHORTLISTED__</strong> shortlisted</div><div class="stat"><strong>__JOBS__</strong> jobs discovered</div></section><form class="filters" id="filters"><input name="location" placeholder="Location"><input name="min_salary" type="number" placeholder="Min salary"><input name="max_salary" type="number" placeholder="Max salary"><input name="industry" placeholder="Industry"><button type="submit">Filter jobs</button><button type="button" id="clear">Clear</button></form><section class="queue" id="queue"><h2>Shortlist</h2><div class="row"><div><strong>Loading shortlisted opportunities...</strong></div></div></section><p id="error"></p><script>let all=[];function showError(message){{const line=document.querySelector('#error');line.textContent=message||'The request failed.';}}async function prepare(id){{const r=await fetch(`/api/opportunities/${{id}}/prepare`,{{method:'POST'}});const text=await r.text();let d={{}};try{{d=JSON.parse(text)}}catch(e){{d={{detail:text}}}}if(!r.ok){{const detail=typeof d.detail==='string'?d.detail:(text||'Preparation failed');showError(detail);alert(detail);return}}showError('');alert(`Package ${{d.status}}. Unresolved questions: ${{(d.unresolved_questions||[]).length}}`)}}function render(rows){{const q=document.querySelector('#queue');q.innerHTML='<h2>Shortlist</h2>'+ (rows.length?rows.map(o=>`<div class="row"><div><strong>${{o.rank_position??''}}. ${{o.title}}</strong><br><span>${{o.company}} · ${{o.location||'Location not listed'}} · ${{o.status}}</span><br><span>${{o.why_this_job||o.shortlist_reason||'Shortlisted by the ranking engine.'}}</span></div><span class="score">${{o.fit_score==null?'Fit pending':`Fit ${{Math.round(o.fit_score*100)}}%`}}<br><button type="button" onclick="prepare('${{o.id}}')">Prepare application</button></span></div>`).join(''):'<div class="row"><div><strong>No jobs match those filters.</strong></div></div>') }fetch('/api/opportunities').then(async r=>{{if(!r.ok)throw new Error(await r.text());return r.json()}}).then(d=>{{all=d.opportunities;render(all)}}).catch(error=>{{document.querySelector('#queue').innerHTML='<h2>Shortlist</h2><div class="row"><div><strong>The opportunity queue could not be loaded.</strong></div></div>';showError(error.message)}});document.querySelector('#filters').addEventListener('submit',e=>{{e.preventDefault();const f=new FormData(e.target),loc=(f.get('location')||'').toString().toLowerCase(),ind=(f.get('industry')||'').toString().toLowerCase(),min=Number(f.get('min_salary'))||0,max=Number(f.get('max_salary'))||Infinity;render(all.filter(o=>(!loc||`${{o.location}}`.toLowerCase().includes(loc))&&(!ind||`${{o.company}} ${{o.title}}`.toLowerCase().includes(ind))&&(!min||o.salary_max>=min)&&(!isFinite(max)||o.salary_min<=max)))}});document.querySelector('#clear').onclick=()=>{{document.querySelector('#filters').reset();render(all)}};</script></main></body></html>"""
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
<style>body{margin:0;background:#f4f7f3;color:#18332f;font:15px/1.5 system-ui,sans-serif}main{max-width:920px;margin:0 auto;padding:48px 24px}a{color:#62877a;font-weight:700;text-decoration:none}h1{font-size:38px;letter-spacing:-.04em;margin:28px 0 8px}p{color:#6c7f7a}.row{display:flex;gap:12px;align-items:flex-start;background:#fff;border:1px solid #dce6df;border-radius:10px;padding:14px;margin:10px 0}button{background:#18332f;color:#fff;border:0;border-radius:7px;padding:11px 16px;font-weight:700}button.copy,button.apply,button.refresh-one{padding:6px 10px;margin-left:8px}button:disabled{background:#9aada6;cursor:not-allowed}code{font-size:12px}.why{color:#6c7f7a;display:block;margin-top:6px}dialog{border:1px solid #dce6df;border-radius:12px;padding:22px;max-width:440px;background:#fff;color:#18332f}dialog::backdrop{background:rgba(24,51,47,.45)}dialog p{margin:0 0 16px;color:#18332f}dialog button{margin-right:8px}</style></head>
<body><main><a href="/">Back to overview</a><h1>Applications</h1>
<p>Select complete packages and approve them together. Each approval is stored against that package's content hash, so a later edit invalidates it. The list shows the latest revision of each application.</p>
<form id="batch"><div id="list">Loading packages...</div><button type="submit" id="approve">Approve selected</button> <button type="button" id="refresh-all">Refresh all unsubmitted</button></form>
<dialog id="confirm-refresh">
<p>Refresh every approved application that has not been submitted? Each approval is revoked. You will need to approve the new packages.</p>
<form method="dialog">
<button type="submit" value="cancel">Cancel</button>
<button type="submit" id="confirm-refresh-yes" value="refresh">Refresh packages</button>
</form>
</dialog>
<p id="result"></p>
<script>
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function readBody(r){const text=await r.text();let d={};try{d=JSON.parse(text)}catch(e){d={detail:text}}return {ok:r.ok,d,text};}
async function load(){
  const line=document.querySelector('#result');
  const r=await fetch('/api/packages');
  const body=await readBody(r);
  const list=document.querySelector('#list');
  if(!body.ok){list.textContent='Packages could not be loaded.';line.textContent=body.d.detail||body.text||'Request failed';return;}
  const d=body.d;
  const dry=d.automation_dry_run!==false;
  const rows=(d.packages||[]);
  if(!rows.length){list.textContent='No packages yet.';return;}
  list.innerHTML=rows.map(p=>{
    const label=p.review_label||(p.approvable?'ready to approve':'not approvable');
    const next=p.next_step?`<span class="why">${esc(p.next_step)}</span>`:'';
    const appId=p.application_id||'';
    const refresh=p.application_status==='APPROVED'?`<button type="button" class="refresh-one" data-id="${esc(appId)}">Refresh package</button>`:'';
    const apply=p.application_status==='APPROVED'?`<button type="button" class="apply" data-id="${esc(appId)}" ${dry?'disabled':''}>Apply</button>${dry?'<span class="why">Dry run is on, so Apply is off. Set SABRA_AUTOMATION_DRY_RUN=false and restart the dashboard.</span>':''}`:'';
    return `<div class="row"><label><input type="checkbox" name="package" value="${esc(p.package_id)}" data-hash="${esc(p.content_hash)}" ${p.approvable?'':'disabled'}></label><span><strong>${esc(p.title||'Package')} at ${esc(p.company||'the company')}</strong><br><a href="/packages/${esc(p.package_id)}">Read package</a><br>${esc(p.status)} · ${esc(label)}${next}<br>Application ID <code class="app-id">${esc(appId)}</code><button type="button" class="copy" data-copy="${esc(appId)}">Copy</button>${refresh}${apply}<br><code>${esc(p.content_hash)}</code></span></div>`;
  }).join('');
  list.querySelectorAll('button.copy').forEach(btn=>btn.addEventListener('click',async()=>{
    const id=btn.dataset.copy||'';
    try{await navigator.clipboard.writeText(id);}catch(e){}
    btn.textContent='Copied';
  }));
  list.querySelectorAll('button.refresh-one').forEach(btn=>btn.addEventListener('click',async()=>{
    const response=await fetch('/api/applications/'+encodeURIComponent(btn.dataset.id)+'/refresh',{method:'POST'});
    const refreshed=await readBody(response);
    line.textContent=refreshed.ok?'Package refreshed. Approve the new revision; the previous approval no longer applies.':(refreshed.d.detail||refreshed.text||'Refresh failed');
    if(refreshed.ok) load();
  }));
  list.querySelectorAll('button.apply').forEach(btn=>btn.addEventListener('click',async()=>{
    if(btn.disabled)return;
    const ok=confirm('Submit this approved application? A CAPTCHA opens in a headed browser for you to solve. This does not solve a CAPTCHA.');
    if(!ok)return;
    const response=await fetch('/api/applications/'+encodeURIComponent(btn.dataset.id)+'/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});
    const applied=await readBody(response);
    line.textContent=applied.ok?(applied.d.outcome+': '+(applied.d.detail||'')):(applied.d.detail||applied.text||'Apply failed');
  }));
}
document.querySelector('#batch').addEventListener('submit',async e=>{
  e.preventDefault();
  const chosen=[...document.querySelectorAll('input[name=package]:checked')].map(el=>({package_id:el.value,content_hash:el.dataset.hash}));
  const line=document.querySelector('#result');
  if(!chosen.length){line.textContent='Select at least one complete package.';return;}
  const r=await fetch('/api/packages/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approved_by:'owner',packages:chosen})});
  const body=await readBody(r);
  if(!body.ok){line.textContent=body.d.detail||body.text||'Approval failed';return;}
  const d=body.d;
  const ok=(d.approved||[]).length, no=(d.refused||[]).length;
  line.textContent=`Approved ${ok}. Refused ${no}.`+(no?(' '+d.refused.map(x=>x.message).join(' ')):'');
  load();
});
document.querySelector('#refresh-all').addEventListener('click',async()=>{
  const dialog=document.querySelector('#confirm-refresh');
  const choice=await new Promise(resolve=>{
    dialog.addEventListener('close',()=>resolve(dialog.returnValue),{once:true});
    dialog.showModal();
  });
  if(choice!=='refresh')return;
  const line=document.querySelector('#result');
  const r=await fetch('/api/applications/refresh-unsubmitted',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});
  const body=await readBody(r);
  if(!body.ok){line.textContent=body.d.detail||body.text||'Refresh failed';return;}
  const n=(body.d.refreshed||[]).length, no=(body.d.refused||[]).length;
  line.textContent=`Refreshed ${n}. Refused ${no}. Approve the new packages; the old approvals no longer apply.`;
  load();
});
load();
</script></main></body></html>"""


@app.get("/packages/{package_id}", response_class=HTMLResponse)
def package_page(package_id: str) -> str:
    safe_id = package_id.replace("&", "").replace("<", "").replace('"', "")
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sabra | Package</title>
<style>body{margin:0;background:#f4f7f3;color:#18332f;font:15px/1.5 system-ui,sans-serif}main{max-width:920px;margin:0 auto;padding:48px 24px}a{color:#62877a;font-weight:700;text-decoration:none}h1{font-size:32px;letter-spacing:-.04em}pre,section{background:#fff;border:1px solid #dce6df;border-radius:10px;padding:14px;white-space:pre-wrap}p{color:#6c7f7a}code{font-size:12px}</style></head>
<body><main><a href="/applications">Back to applications</a><h1 id="title">Package</h1>
<p id="meta"></p><p id="error"></p>
<section><h2>Cover letter</h2><div id="cover"></div></section>
<section><h2>Hiring manager note</h2><div id="hiring"></div></section>
<section><h2>Answers</h2><div id="answers"></div></section>
<section><h2>Unresolved</h2><div id="unresolved"></div></section>
<section><h2>Tailored resume</h2><pre id="resume"></pre></section>
<script>
const id="__PACKAGE_ID__";
function text(sel, value){document.querySelector(sel).textContent=value||'';}
fetch('/api/packages/'+encodeURIComponent(id)).then(async r=>{
  const raw=await r.text();
  let d={};
  try{d=JSON.parse(raw)}catch(e){d={detail:raw}}
  if(!r.ok){text('#error', d.detail||raw||'This package could not be loaded.');return;}
  text('#title', (d.title||'Package')+' at '+(d.company||'the company'));
  text('#meta', (d.status||'')+' · application '+(d.application_id||'')+' · hash '+(d.content_hash||''));
  text('#cover', d.cover_letter||'No cover letter on this package.');
  text('#hiring', d.hiring_manager_message||'No hiring-manager note on this package.');
  text('#answers', (d.answers||[]).map(a=>(a.question_key||'')+': '+(a.answer||'(blank)')).join('\\n')||'No answers.');
  text('#unresolved', (d.unresolved_questions||[]).join('\\n')||'None.');
  text('#resume', d.tailored_resume_markdown||'');
}).catch(error=>{text('#error', error.message);});
</script></main></body></html>""".replace("__PACKAGE_ID__", safe_id)


@app.get("/answers", response_class=HTMLResponse)
def answers_page() -> str:
    options = "".join(f'<option value="{key}">{key}</option>' for key in answer_key_choices())
    options += '<option value="__custom__">Custom key</option>'
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sabra | Answers</title>
<style>body{margin:0;background:#f4f7f3;color:#18332f;font:15px/1.5 system-ui,sans-serif}main{max-width:920px;margin:0 auto;padding:48px 24px}a{color:#62877a;font-weight:700;text-decoration:none}h1{font-size:38px;letter-spacing:-.04em;margin:28px 0 8px}p,label{color:#6c7f7a}form,li{background:#fff;border:1px solid #dce6df;border-radius:10px;padding:14px;margin:12px 0}input,select{display:block;width:100%;margin:6px 0 12px;padding:8px;border:1px solid #cbd9d1;border-radius:7px}button{background:#18332f;color:#fff;border:0;border-radius:7px;padding:9px 14px;font-weight:700}</style></head>
<body><main><a href="/">Back to overview</a><h1>Answer memory</h1>
<p>Record an answer, then verify it. Only a verified answer is typed onto a form. Salary, background check, and the other sensitive questions stay blank until you verify them.</p>
<form id="record"><label>Question key<select name="key" id="key">__OPTIONS__</select></label><label id="custom-label">Custom key<input name="custom_key" id="custom-key" placeholder="for example prior_employer"></label><label>Answer<input name="value" required></label><label>Question wording, optional<input name="question_text"></label><button type="submit">Record answer</button></form>
<p id="line"></p><ul id="saved"></ul>
<script>
async function readBody(r){const text=await r.text();let d={};try{d=JSON.parse(text)}catch(e){d={detail:text}}return {ok:r.ok,d};}
function syncCustom(){const custom=document.querySelector('#custom-label');custom.hidden=document.querySelector('#key').value!=='__custom__';}
async function load(){
  const r=await fetch('/api/answers');
  const body=await readBody(r);
  const ul=document.querySelector('#saved');
  if(!body.ok){ul.textContent=body.d.detail||'Answers could not be loaded.';return;}
  const d=body.d;
  ul.innerHTML=(d.answers||[]).map(a=>`<li><strong>${a.question_key}</strong> · ${a.level}${a.sensitive?' · sensitive':''}<br>${a.value??''} ${a.verified?'':`<button type="button" data-key="${a.question_key}">Verify</button>`}</li>`).join('')||'<li>No answers recorded.</li>';
  ul.querySelectorAll('button[data-key]').forEach(btn=>btn.addEventListener('click',async()=>{
    const response=await fetch('/api/answers/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:btn.dataset.key,value:''})});
    const verified=await readBody(response);
    document.querySelector('#line').textContent=verified.ok?`${verified.d.question_key} verified.`:(verified.d.detail||'Verify failed');
    load();
  }));
}
document.querySelector('#key').addEventListener('change',syncCustom);
syncCustom();
document.querySelector('#record').addEventListener('submit',async e=>{
  e.preventDefault();
  const f=new FormData(e.target);
  let key=f.get('key');
  if(key==='__custom__') key=(f.get('custom_key')||'').toString().trim();
  const line=document.querySelector('#line');
  if(!key){line.textContent='Enter a question key.';return;}
  const r=await fetch('/api/answers/record',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:key,value:f.get('value'),question_text:f.get('question_text')||null})});
  const body=await readBody(r);
  line.textContent=body.ok?`${body.d.question_key} recorded. Verify it before it can fill a form.`:(body.d.detail||'Record failed');
  e.target.querySelector('[name=value]').value='';
  load();
});
load();
</script></main></body></html>""".replace("__OPTIONS__", options)


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
<body><div class="shell"><aside class="rail"><div class="brand">Sabra<small>Job search operator</small></div><nav class="nav"><a class="active" href="/">Overview</a><a href="/opportunities">Opportunities</a><a href="/applications">Applications</a><a href="/answers">Answers</a><a href="#candidate">Candidate</a></nav><div class="rail-note">You approve complete packages in a batch. A CAPTCHA is solved only by you.</div></aside><main class="main"><header class="top"><div><div class="eyebrow">Operator overview</div><h1 class="title">Find the work worth pursuing.</h1><p class="sub">A truthful queue of opportunities, prepared applications, and the next decision that belongs to you.</p></div><button class="button" id="refresh">Refresh data</button></header><section class="metrics" id="metrics"><div class="metric"><div class="metric-label">Jobs discovered</div><div class="metric-value" data-key="jobs_discovered">-</div></div><div class="metric"><div class="metric-label">Opportunities</div><div class="metric-value" data-key="opportunities">-</div></div><div class="metric"><div class="metric-label">Shortlisted</div><div class="metric-value" data-key="shortlisted">-</div></div><div class="metric"><div class="metric-label">Packages ready</div><div class="metric-value" data-key="packages_latest">-</div></div></section><section class="content"><div class="panel" id="opportunities"><h2>Opportunity queue</h2><p>The strongest roles rise here after deterministic filtering and fit assessment.</p><div class="queue" id="overview-queue"><div class="queue-item"><div><strong>Loading the opportunity queue...</strong><span>Ranking uses your search intent and candidate evidence.</span></div><span class="score">Awaiting data</span></div></div></div><div class="panel" id="candidate"><h2>Candidate readiness</h2><p id="candidate-copy">Loading candidate profile...</p><form id="resume-form" style="margin-top:16px"><label for="resume" style="display:block;color:var(--muted);font-size:12px;margin-bottom:6px">Master resume</label><input id="resume" name="resume" type="file" accept=".pdf,.txt" required><button class="button" type="submit" style="margin-top:10px">Upload resume</button><div class="status-line" id="upload-line"></div></form><div class="readiness"><strong>Truth boundary</strong><span>Verified, inferred, and unknown facts remain distinct before an application is prepared.</span></div><div class="status-line" id="status-line">Connecting to the local engine...</div></div></section></main></div><script>
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function loadQueue(){const q=document.querySelector('#overview-queue');try{const r=await fetch('/api/opportunities');const text=await r.text();if(!r.ok)throw new Error(text||('status '+r.status));const d=JSON.parse(text);const rows=d.opportunities||[];if(!rows.length){q.innerHTML='<div class="queue-item"><div><strong>No shortlisted opportunities yet.</strong><span>Ranking uses your search intent and candidate evidence.</span></div></div>';return;}q.innerHTML=rows.map(o=>`<div class="queue-item"><div><strong>${o.rank_position??''}. ${esc(o.title)}</strong><span>${esc(o.company)} · ${esc(o.location||'Location not listed')}</span></div><span class="score">${o.fit_score==null?'Fit pending':`Fit ${Math.round(o.fit_score*100)}%`}</span></div>`).join('');}catch(error){q.innerHTML='<div class="queue-item"><div><strong>The opportunity queue could not be loaded.</strong><span></span></div></div>';q.querySelector('span').textContent=error.message;}}
async function refresh(){const line=document.querySelector('#status-line');try{const r=await fetch('/api/status');const text=await r.text();if(!r.ok)throw new Error(text||('status '+r.status));const d=JSON.parse(text);document.querySelectorAll('[data-key]').forEach(e=>e.textContent=d[e.dataset.key]??0);document.querySelector('#candidate-copy').textContent=d.candidate_name?`${d.candidate_name} is loaded in the local engine.`:'No candidate profile loaded yet.';line.textContent=`Updated ${new Date(d.generated_at).toLocaleTimeString()}.`;}catch(e){line.textContent='The local engine could not be reached. '+e.message;console.error(e);}await loadQueue();}document.querySelector('#refresh').addEventListener('click',refresh);refresh();document.querySelector('#resume-form').addEventListener('submit',async e=>{e.preventDefault();const file=document.querySelector('#resume').files[0];const line=document.querySelector('#upload-line');if(!file){line.textContent='Choose a PDF or text resume first.';return;}line.textContent='Uploading and parsing...';const body=new FormData();body.append('resume',file);try{const r=await fetch('/api/candidate/resume',{method:'POST',body});const text=await r.text();let d={};try{d=JSON.parse(text)}catch(err){d={detail:text}}if(!r.ok)throw new Error(d.detail||'Upload failed');line.textContent='Resume stored as inferred evidence. Refreshing profile...';refresh()}catch(error){line.textContent=error.message}});
</script></body></html>"""
