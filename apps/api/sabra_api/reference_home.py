"""Reference-led Home composition for ARTEMIS.

The page consumes only values returned by ``/api/home``. Its template keeps the
existing run controls and polling contract while giving the overview a compact,
editorial Morning Brief layout.
"""

HOME_CSS = r"""
.home [hidden]{display:none!important}
.home.home {
  width: min(100%, 1008px);
  min-height: calc(100vh - 49px);
  margin: 0 auto;
  padding: 0 0 34px;
  background: var(--porcelain, #F7F6F1);
}
.home-hero {
  position: relative;
  min-height: 245px;
  display: flex;
  align-items: flex-start;
  padding: 35px 44px 20px;
  isolation: isolate;
  overflow: hidden;
  background: #ECEAE2;
}
.home-hero::before {
  position: absolute;
  content: "";
  z-index: -1;
  inset: 0;
  background-image: linear-gradient(90deg, #F7F6F1 0%, rgba(247,246,241,.97) 34%, rgba(247,246,241,.72) 51%, rgba(247,246,241,.04) 76%), url('/brand/images/artemis-architecture.webp');
  background-size: cover;
  background-position: center 53%;
}
.home-hero-copy { width: min(100%, 470px); }
.home-greeting { margin: 0 0 4px; color: var(--evergreen, #142D29); font: 500 15px/1.4 'Artemis Display', Georgia, serif; }
.home-hero h1 {
  max-width: 450px;
  margin: 0 0 9px;
  color: var(--evergreen, #142D29);
  font: 600 clamp(34px, 4vw, 40px)/.95 'Artemis Display', Georgia, serif;
  letter-spacing: -.035em;
}
.home-summary { max-width: 410px; margin: 0; color: #525D56; font-size: 12.5px; line-height: 1.45; }
.home-hero-actions { display: flex; align-items: center; gap: 12px; margin-top: 13px; }
.home .brief-cta,.home .listen-cta {
  min-height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  padding: 7px 13px;
  border: 1px solid var(--evergreen, #142D29);
  border-radius: 5px;
  color: #fff;
  background: var(--evergreen, #142D29);
  font: 600 11px/1.2 'Artemis Sans', Inter, system-ui, sans-serif;
  text-decoration: none;
  cursor: pointer;
}
.home .brief-cta:hover { color: #fff; background: var(--forest, #29483E); }
.home .brief-cta img { width: 13px; height: 13px; filter:invert(1); }
.hero-maxim{position:absolute;right:45px;top:49px;max-width:70px;font-size:7px;line-height:2.3;letter-spacing:.26em;color:#293a30;text-transform:uppercase}
.hero-maxim::after{content:"";display:block;width:23px;height:1px;margin-top:16px;background:#758174}
@media(max-width:760px){.hero-maxim{display:none}}
.home .listen-cta { gap: 8px; padding: 0; border: 0; color: var(--graphite, #26312E); background: transparent; text-align: left; }
.home .listen-cta:hover { color: var(--forest, #29483E); background: transparent; }
.listen-icon { width: 33px; height: 33px; display: inline-grid; place-items: center; border: 1px solid #D5D2C8; border-radius: 50%; background: rgba(247,246,241,.8); }
.listen-icon svg { width: 11px; height: 11px; }
.listen-copy { display: grid; gap: 1px; font-size: 10px; font-weight: 600; }
.listen-copy small { color: #667169; font-size: 9px; font-weight: 400; }
.home-hero-image { display: none; }
.mobile-brief-cta{display:none!important}
.reference-metrics {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(4,minmax(0,1fr));
  gap: 9px;
  margin: 5px 20px 20px;
  padding: 0;
}
.reference-metric {
  min-width: 0;
  min-height: 60px;
  display: grid;
  grid-template-columns: 31px minmax(0,1fr) auto;
  align-items: center;
  gap: 9px;
  padding: 9px 11px;
  border: 1px solid #E3E0D7;
  border-radius: 5px;
  background: #FBFAF6;
}
.reference-metric-icon { width: 28px; height: 28px; display: grid; place-items: center; color: var(--graphite, #26312E); }
.reference-metric-icon img { width: 22px; height: 22px; stroke: currentColor; }
.reference-metric-copy { min-width: 0; display: grid; gap: 1px; }
.reference-metric-value { color: #1A2420; font: 500 22px/1 'Artemis Display', Georgia, serif; }
.reference-metric-label { overflow: hidden; color: #58635C; font-size: 9.5px; line-height: 1.25; text-overflow: ellipsis; white-space: nowrap; }
.reference-metric-tag { padding: 3px 6px; border-radius: 999px; color: #355044; background: #E5ECE3; font-size: 8px; line-height: 1.2; white-space: nowrap; }
.home-panels { display: grid; grid-template-columns: 1.18fr 1.08fr .84fr; gap: 9px; padding: 0 11px; align-items: stretch; }
.home-panel { min-width: 0; padding: 15px 15px 12px; border: 1px solid #E5E2DA; border-radius: 5px; background: #FBFAF6; }
.home-panel-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 9px; }
.home-panel-heading h2 { margin: 0; color: var(--evergreen, #142D29); font: 500 17px/1.15 'Artemis Display', Georgia, serif; letter-spacing: -.02em; }
.home-panel-heading a { flex: none; color: #46584D; font-size: 9px; font-weight: 550; text-decoration: none; white-space: nowrap; }
.home-panel-heading a:hover { color: var(--evergreen, #142D29); text-decoration: underline; }
.brief-date { margin-left: auto; color: #707970; font: 600 7px/1.2 'Artemis Sans', Inter, sans-serif; letter-spacing: .12em; text-transform: uppercase; white-space: nowrap; }
.brief-list,.activity-list,.opportunity-list { list-style: none; margin: 0; padding: 0; }
.brief-list { counter-reset: brief; }
.brief-item { position: relative; display: grid; grid-template-columns: 25px minmax(0,1fr) 13px; gap: 7px; align-items: start; padding: 8px 0; border-top: 1px solid #E9E6DE; counter-increment: brief; }
.brief-item:first-child { border-top: 0; }
.brief-number { width: 23px; height: 23px; display: grid; place-items: center; border: 1px solid #E4E1D8; border-radius: 50%; color: #34493E; background: #F6F5F0; font: 500 11px/1 'Artemis Display', Georgia, serif; }
.brief-item-copy { min-width: 0; }
.brief-item-title { display: block; color: #27322D; font-size: 9.5px; font-weight: 650; line-height: 1.35; }
.brief-item-detail { display: block; margin-top: 2px; color: #68726A; font-size: 8.5px; line-height: 1.35; }
.brief-item-go { padding-top: 4px; color: #59685E; font-size: 13px; text-decoration: none; }
.brief-empty,.activity-empty,.opportunity-empty { margin: 10px 0; color: #667169; font-size: 10px; line-height: 1.5; }
.opportunity-row { display: grid; grid-template-columns: 28px minmax(0,1fr) auto; gap: 7px; align-items: center; padding: 8px 0; border-top: 1px solid #E9E6DE; color: inherit; text-decoration: none; }
.opportunity-row:first-child { border-top: 0; }
.opportunity-initials { width: 26px; height: 26px; display: grid; place-items: center; border-radius: 5px; color: #fff; background: var(--forest, #29483E); font: 600 11px/1 'Artemis Sans', Inter, sans-serif; }
.opportunity-copy { min-width: 0; }
.opportunity-title { display: block; overflow: hidden; color: #27322D; font-size: 9.5px; font-weight: 650; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
.opportunity-sub { display: block; overflow: hidden; margin-top: 2px; color: #68726A; font-size: 8px; line-height: 1.35; text-overflow: ellipsis; white-space: nowrap; }
.opportunity-fit { display: grid; justify-items: end; gap: 3px; color: #3E584A; font-size: 8px; white-space: nowrap; }
.opportunity-fit strong { padding: 3px 5px; border-radius: 99px; background: #E6EDE4; font-size: 8px; }
.activity-item { display: grid; grid-template-columns: 18px minmax(0,1fr); gap: 7px; align-items: start; padding: 8px 0; border-top: 1px solid #E9E6DE; }
.activity-item:first-child { border-top: 0; }
.activity-icon { width: 17px; height: 17px; display: grid; place-items: center; color: #45594D; }
.activity-icon img { width: 14px; height: 14px; stroke: currentColor; }
.activity-copy { min-width: 0; }
.activity-title { display: block; color: #2E3832; font-size: 9px; font-weight: 600; line-height: 1.35; }
.activity-detail { display: block; margin-top: 2px; color: #6B746D; font-size: 8px; line-height: 1.35; }
.activity-time { display: block; margin-top: 3px; color: #858B84; font-size: 7.5px; }
.home .mark-seen{padding:0;min-height:20px;border:0;background:none;color:#46584D;font-size:8px;font-weight:500}.home .mark-seen:hover{background:none;color:#142D29;text-decoration:underline}
.home-operations { margin: 14px 11px 0; padding: 0; }
.home-operations > summary { width: fit-content; color: #536159; font-size: 10px; cursor: pointer; }
.home-operations-body { margin-top: 9px; padding: 13px; border: 1px solid #E2DFD6; border-radius: 5px; background: #FBFAF6; }
.home-run-controls { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.home-run-controls label { display: flex; align-items: center; gap: 6px; color: #58635C; font-size: 10px; }
.home-run-controls input { accent-color: var(--forest, #29483E); }
.home .run-button { min-height: 32px; padding: 6px 11px; border: 1px solid var(--forest, #29483E); border-radius: 4px; color: #fff; background: var(--forest, #29483E); font-size: 10px; }
.home .run-button:disabled { opacity: .55; cursor: not-allowed; }
.home .line { margin: 5px 0; color: #58635C; font-size: 10px; }
#run-log { margin-top: 9px; }
#run-log > summary { color: #58635C; font-size: 10px; cursor: pointer; }
#run-progress { margin-top: 7px; }
#run-progress .glass { padding: 9px; border: 1px solid #E2DFD6; border-radius: 4px; background: #F4F2EC; font-size: 10px; }
#run-progress .stage { padding: 3px 0; color: #58635C; font-size: 9px; }
#run-progress details { margin-top: 5px; }
#status-line { padding: 0 12px; text-align: right; font-size: 9px; }

@media (max-width: 760px) {
  .home.home { width: 100%; min-height: 0; padding: 0 0 24px; }
  .home-hero { min-height: 0; display: block; padding: 25px 21px 0; overflow: visible; background: #F7F6F1; }
  .home-hero::before { content: none; }
  .home-hero-copy { width: 100%; }
  .home-greeting { margin-bottom: 5px; font-size: 14px; }
  .home-hero h1 { max-width: 360px;font-weight:500; margin-bottom: 8px; font-size: clamp(32px, 9vw, 38px); line-height: 1.02; }
  .home-summary { max-width: 420px; font-size: 11px; }
  .home-hero-actions { display:none; }
  .home-summary{position:absolute!important;width:1px;height:1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
  .home-hero-actions .brief-cta{display:none}
  .mobile-brief-cta{display:flex!important;margin:0 14px 18px;min-height:38px!important}
  .home-hero-image { height: 130px; display: block; margin: 17px 0 0; background: url('/brand/images/artemis-architecture.webp') center 55%/cover no-repeat; }
  .reference-metrics { grid-template-columns: 1fr; gap: 0; margin: 0 21px 13px; border: 0; border-radius: 0; background: transparent; }
  .reference-metric { min-height: 56px; grid-template-columns: 36px minmax(0,1fr) auto; padding: 8px 12px; border: 0; border-bottom: 1px solid #E9E6DE; border-radius: 0; background: transparent; }
  .reference-metric:last-child { border-bottom: 0; }
  .reference-metric-icon { width: 30px; }
  .reference-metric-icon img { width: 21px; height: 21px; }
  .reference-metric-value { font-size: 20px; }
  .reference-metric-label { font-size: 10px; }
  .reference-metric-tag { font-size: 8px; }
  .home-panels { grid-template-columns: 1fr; gap: 9px; padding: 0 14px; }
  .home-panel { padding: 15px; }
  .home-panel-heading h2 { font-size: 19px; }
  .brief-item { grid-template-columns: 28px minmax(0,1fr) 14px; gap: 9px; padding: 10px 0; }
  .brief-number { width: 25px; height: 25px; font-size: 12px; }
  .brief-item-title,.opportunity-title { font-size: 11px; }
  .brief-item-detail,.opportunity-sub { font-size: 9px; }
  .opportunity-row { grid-template-columns: 32px minmax(0,1fr) auto; padding: 10px 0; }
  .opportunity-initials { width: 30px; height: 30px; }
  .activity-item { grid-template-columns: 21px minmax(0,1fr); padding: 9px 0; }
  .activity-title { font-size: 10px; }
  .activity-detail,.activity-time { font-size: 9px; }
  .home .mark-seen{padding:0;min-height:20px;border:0;background:none;color:#46584D;font-size:8px;font-weight:500}.home .mark-seen:hover{background:none;color:#142D29;text-decoration:underline}
.home-operations { margin: 13px 14px 0; }
}
@media (prefers-reduced-motion: reduce) {
  .home-hero-image,.home-hero::before { scroll-behavior: auto; }
}@media(max-width:760px){
.topnav{height:75px;min-height:75px;padding:0 29px}.brand{gap:16px}.brand .mark{width:46px;height:50px}.brand .word{font-size:16px;letter-spacing:.5em}.navtoggle summary{min-width:28px}.navtoggle summary img{width:28px;height:28px}
.home.home{min-height:calc(100vh - 75px)}.home-hero{padding:25px 29px 0}.home-greeting{font-family:'Artemis Sans',sans-serif;font-size:14px;font-weight:500;line-height:1.4;margin:0 0 10px}.home-hero h1{font-family:Georgia,serif;font-size:44px;font-weight:400;line-height:1.04;letter-spacing:-.045em;margin:0 0 8px;max-width:100%}.home-hero-image{height:130px;margin:30px 0 0;border-radius:6px}
.reference-metrics{margin:0 29px 14px;border:0;background:none}.reference-metric{min-height:94px;grid-template-columns:60px minmax(0,1fr) auto;gap:13px;padding:8px 12px;border:0;background:none}.reference-metric-icon{width:34px;height:38px}.reference-metric-icon img{width:34px;height:34px}.reference-metric-value{font-family:Georgia,serif;font-size:26px;line-height:1.12;font-weight:400}.reference-metric-label{font-size:14px;line-height:1.4;white-space:normal}.reference-metric-copy{gap:2px}.mobile-brief-cta{margin:0 29px 18px!important;min-height:66px!important;font-size:16px!important;font-weight:500!important;border-radius:12px!important}.home-panels{padding:0 29px}
}

"""

HOME_BODY = r'''<main class="home">
<section class="home-hero" aria-labelledby="home-title">
  <div class="home-hero-copy">
    <p class="home-greeting" id="home-greeting">Good morning.</p>
    <h1 id="home-title" aria-label="Your next move is taking shape.">Your next move <br>is taking shape.</h1>
    <p class="home-summary" id="home-summary">Your career search, clearly in view.</p>
    <div class="home-hero-actions">
      <a class="brief-cta" href="#morning-brief">View today's brief <img src="/brand/icons/arrow-right.svg" width="13" height="13" alt=""></a>
      <button type="button" class="listen-cta" id="listen-brief" hidden aria-label="Listen to today's brief"><span class="listen-icon"><img src="/brand/icons/play.svg" width="11" height="11" alt=""></span><span class="listen-copy"><span>Listen to summary</span><small>Read aloud</small></span></button>
    </div>
  </div>
  <p class="hero-maxim">Clarity<br>creates<br>options.</p>
  <div class="home-hero-image" role="img" aria-label="Quiet modern architecture overlooking a tree-lined landscape"></div>
</section>
<section class="reference-metrics" aria-label="Career search overview">
  <article class="reference-metric"><span class="reference-metric-icon" aria-hidden="true"><img src="/brand/icons/bar-chart-2.svg" alt=""></span><span class="reference-metric-copy"><strong class="reference-metric-value" data-reference-metric="analyzed">—</strong><span class="reference-metric-label">Opportunities analyzed</span></span><span class="reference-metric-tag" data-reference-tag="analyzed" hidden></span></article>
  <article class="reference-metric"><span class="reference-metric-icon" aria-hidden="true"><img src="/brand/icons/circle.svg" alt=""></span><span class="reference-metric-copy"><strong class="reference-metric-value" data-reference-metric="high_confidence">—</strong><span class="reference-metric-label" title="Shortlisted roles with an engine confidence estimate of at least 75%">High-confidence matches</span></span><span class="reference-metric-tag" data-reference-tag="high_confidence" hidden></span></article>
  <article class="reference-metric"><span class="reference-metric-icon" aria-hidden="true"><img src="/brand/icons/file-text.svg" alt=""></span><span class="reference-metric-copy"><strong class="reference-metric-value" data-reference-metric="ready">—</strong><span class="reference-metric-label">Applications ready</span></span><span class="reference-metric-tag" data-reference-tag="ready" hidden></span></article>
  <article class="reference-metric"><span class="reference-metric-icon" aria-hidden="true"><img src="/brand/icons/calendar.svg" alt=""></span><span class="reference-metric-copy"><strong class="reference-metric-value" data-reference-metric="updates">—</strong><span class="reference-metric-label" title="Recorded employer responses not yet marked as seen">Meaningful updates</span></span><span class="reference-metric-tag" data-reference-tag="updates" hidden></span></article>
</section>
<a class="brief-cta mobile-brief-cta" href="#morning-brief">View today’s brief →</a>
<section class="home-panels" aria-label="Your career briefing">
  <section class="home-panel" id="morning-brief" aria-labelledby="brief-heading">
    <header class="home-panel-heading"><h2 id="brief-heading">Today's brief</h2><time class="brief-date" id="brief-date"></time><a href="/opportunities">View all ›</a></header>
    <ol class="brief-list" id="brief-list"><li class="brief-empty">Loading your latest findings…</li></ol>
  </section>
  <section class="home-panel" aria-labelledby="top-opportunities-heading">
    <header class="home-panel-heading"><h2 id="top-opportunities-heading">Top opportunities for you</h2><a href="/opportunities">View all</a></header>
    <ul class="opportunity-list" id="opportunity-list"><li class="opportunity-empty">Loading your shortlist…</li></ul>
  </section>
  <section class="home-panel" aria-labelledby="activity-heading">
    <header class="home-panel-heading"><h2 id="activity-heading">Recent activity</h2><button type="button" id="mark-seen" class="mark-seen" hidden>Mark seen</button><a href="/pipeline">View all</a></header>
    <ul class="activity-list" id="activity-list"><li class="activity-empty">Loading recent activity…</li></ul>
  </section>
</section>
<details class="home-operations" id="run-log" hidden><summary>Search controls and latest run</summary>
  <div class="home-operations-body"><div class="home-run-controls"><button type="button" class="run-button" id="run-now">Run search</button><label><input type="checkbox" id="run-prepare"> Prepare packages for top roles (nothing is submitted)</label></div><p class="line" id="run-line"></p><div id="run-progress"></div></div>
</details>
<p class="line" id="status-line" aria-live="polite"></p>
</main>
<script>
__ESC__
const HOME_ACTIVITY_ICONS={application:'<img src="/brand/icons/file-text.svg" alt="">',opportunity:'<img src="/brand/icons/bar-chart-2.svg" alt="">',response:'<img src="/brand/icons/message-square.svg" alt="">',insight:'<img src="/brand/icons/check-circle.svg" alt="">'};
let pollTimer=null;
function safePath(path,fallback){return typeof path==='string'&&path.startsWith('/')&&!path.startsWith('//')?path:fallback;}
function initials(name){return String(name||'?').trim().split(/\s+/).filter(Boolean).slice(0,2).map(w=>w[0]).join('').toUpperCase()||'?';}
function readableDate(value){if(!value)return '';const d=new Date(value);return isNaN(d.getTime())?'':d.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'});}
function renderReferenceMetrics(d){
  const m=d.reference_metrics||{},today=d.today||{},counts=today.counts||{},opps=Array.isArray(d.top_opportunities)?d.top_opportunities:(d.featured?[d.featured,...(d.recent||[])]:d.recent||[]);
  const confidence=opps.filter(o=>typeof o.confidence==='number'&&o.confidence>=.75).length;
  const fallback={analyzed:null,high_confidence:confidence||null,ready:counts.ready_for_approval,updates:counts.employer_responses};
  Object.keys(fallback).forEach(k=>{const v=(m[k]!==undefined&&m[k]!==null)?m[k]:fallback[k];const el=document.querySelector('[data-reference-metric="'+k+'"]');if(el)el.textContent=(v===null||v===undefined)?'—':String(v);});
  Object.keys(m).forEach(k=>{const tag=document.querySelector('[data-reference-tag="'+k+'"]');if(tag&&m[k+'_change']){tag.textContent=String(m[k+'_change']);tag.hidden=false;}});
}
function renderBriefItems(d){
  const items=Array.isArray(d.brief_items)?d.brief_items:[];
  const brief=d.morning_brief||{},next=d.next_action||{},counts=((d.today||{}).counts)||{};
  if(!Array.isArray(d.brief_items)){
    if(next.label)items.push({title:next.label,detail:next.reason||'',href:next.href});
    if(counts.new_high_fit)items.push({title:counts.new_high_fit+' shortlisted '+(counts.new_high_fit===1?'opportunity':'opportunities'),detail:'Recorded on your current shortlist.',href:'/opportunities'});
    if(counts.ready_for_approval)items.push({title:'Application ready for your review',detail:String(counts.ready_for_approval)+' package'+(counts.ready_for_approval===1?' is':'s are')+' awaiting your decision.',href:'/applications?filter=ready'});
    if(counts.employer_responses)items.push({title:'Follow up recommended',detail:String(counts.employer_responses)+' employer update'+(counts.employer_responses===1?'':'s')+' to review.',href:'/pipeline'});
    if(!items.length&&brief.narrative)items.push({title:brief.headline||'Your search, in view',detail:brief.narrative,href:'/opportunities'});
    if(!items.length)items.push({title:'Your search is ready to take shape',detail:'Run a search to build your first evidence-based brief.',href:'/searches'});
  }
  const box=document.querySelector('#brief-list');
  box.innerHTML=items.length?items.slice(0,4).map((it,i)=>'<li class="brief-item"><span class="brief-number">'+(i+1)+'</span><span class="brief-item-copy"><a class="brief-item-title" href="'+esc(safePath(it.href,'/opportunities'))+'">'+esc(it.title||it.label||'Review your search')+'</a>'+(it.detail?'<span class="brief-item-detail">'+esc(it.detail)+'</span>':'')+'</span><a class="brief-item-go" href="'+esc(safePath(it.href,'/opportunities'))+'" aria-label="Open '+esc(it.title||it.label||'brief item')+'">›</a></li>').join(''):'<li class="brief-empty">Nothing needs your attention right now.</li>';
  const date=document.querySelector('#brief-date');date.textContent=readableDate(brief.run_at||d.generated_at);
}
function renderOpportunities(d){
  let rows=Array.isArray(d.top_opportunities)?d.top_opportunities:(d.featured?[d.featured,...(d.recent||[]).filter(o=>o.id!==d.featured.id)]:d.recent||[]);
  const box=document.querySelector('#opportunity-list');
  if(!rows.length){box.innerHTML='<li class="opportunity-empty">No shortlisted opportunities yet. Run a search to review roles against your goals.</li>';return;}
  box.innerHTML=rows.slice(0,4).map(o=>{const fit=typeof o.fit_score==='number'?Math.round(o.fit_score*100)+'% match':'';const confidence=typeof o.confidence==='number'&&o.confidence>=.75?'High confidence':'';const sub=[o.company,o.location,o.compensation_text].filter(Boolean).join(' · ');return '<li><a class="opportunity-row" href="'+esc(safePath('/opportunities/'+encodeURIComponent(o.id||''),'/opportunities'))+'"><span class="opportunity-initials">'+esc(initials(o.company))+'</span><span class="opportunity-copy"><span class="opportunity-title">'+esc(o.title||'Opportunity')+'</span><span class="opportunity-sub">'+esc(sub||'Details not recorded')+'</span></span><span class="opportunity-fit">'+(fit?'<strong>'+esc(fit)+'</strong>':'')+(confidence?'<span>'+esc(confidence)+'</span>':'')+'</span></a></li>';}).join('');
}
function renderActivity(d){
  let rows=Array.isArray(d.recent_activity)?d.recent_activity:[];
  const box=document.querySelector('#activity-list');
  if(!rows.length){box.innerHTML='<li class="activity-empty">No recent activity has been recorded.</li>';return;}
  box.innerHTML=rows.slice(0,5).map(a=>{const kind=HOME_ACTIVITY_ICONS[a.kind]||HOME_ACTIVITY_ICONS.insight;const content='<span class="activity-icon" aria-hidden="true">'+kind+'</span><span class="activity-copy"><span class="activity-title">'+esc(a.label||a.title||'Activity recorded')+'</span>'+(a.detail?'<span class="activity-detail">'+esc(a.detail)+'</span>':'')+(a.occurred_at?'<time class="activity-time">'+esc(readableDate(a.occurred_at))+'</time>':'')+'</span>';return '<li class="activity-item">'+(a.href?'<a class="activity-row" href="'+esc(safePath(a.href,'/pipeline'))+'" style="display:contents">'+content+'</a>':content)+'</li>';}).join('');
}
function renderHome(d){
  const name=String(d.candidate_name||'').trim().split(/\s+/)[0];
  const greeting='Good morning';
  document.querySelector('#home-greeting').textContent=greeting+(name?', '+name+'.':'.');
  const brief=d.morning_brief||{},m=d.reference_metrics;const summary=m&&m.analyzed>0?'Artemis has analyzed '+m.analyzed+' opportunities, with '+m.high_confidence+' high-confidence matches. '+m.ready+' applications ready for review and '+m.updates+' unread employer updates.':(brief.narrative||'Your career search, clearly in view.');
  document.querySelector('#home-summary').textContent=summary;
  renderReferenceMetrics(d);renderBriefItems(d);renderOpportunities(d);renderActivity(d);
  const seen=document.querySelector('#mark-seen');seen.hidden=!(((d.today||{}).counts||{}).employer_responses);seen.onclick=async()=>{const b=await readHomeBody(await fetch('/api/today/seen',{method:'POST'}));if(b.ok)loadHome();else document.querySelector('#status-line').textContent=homeError(b,'Updates could not be marked as seen.');};
  const listen=document.querySelector('#listen-brief');
  if(typeof window!=='undefined'&&'speechSynthesis' in window&&typeof SpeechSynthesisUtterance!=='undefined'){
    listen.hidden=false;listen.onclick=()=>{window.speechSynthesis.cancel();window.speechSynthesis.speak(new SpeechSynthesisUtterance([greeting+(name?', '+name:''),brief.headline||'',summary,...(d.brief_items||[]).map(x=>x.title+'. '+(x.detail||''))].filter(Boolean).join('. ')));};
  }
  const last=(d.today&&(d.today.active_run||d.today.last_run))||null;
  const log=document.querySelector('#run-log');log.hidden=false;
  document.querySelector('#run-progress').innerHTML=last?renderRun(last):'';
  const button=document.querySelector('#run-now');button.disabled=!!(d.today&&d.today.active_run);
  if(d.today&&d.today.active_run){log.open=true;poll(d.today.active_run.id);}
  const t=d.generated_at||((d.today||{}).generated_at);document.querySelector('#status-line').textContent=t?'Updated '+new Date(t).toLocaleTimeString()+'.':'';
}
function renderRun(run){
  if(!run)return '';const p=run.progress||{};
  const stages=Object.entries(p.stages||{}).map(([name,st])=>{const counts=Object.entries(st).filter(([,v])=>typeof v==='number').map(([k,v])=>esc(k.replace(/_/g,' '))+' '+esc(v)).join(' · ');return '<div class="stage"><strong>'+esc(name)+'</strong> '+esc(st.status||'')+(counts?' · '+counts:'')+'</div>';}).join('');
  const warnings=(p.warnings||[]).map(w=>'<p class="stage">'+esc(w)+'</p>').join('');
  const sources=(p.sources||[]).map(s=>'<div class="stage">'+esc(s.display_name||s.source_id)+': '+(s.ok?'ok':'FAILED')+' · '+esc(s.jobs_seen??0)+' seen · '+esc(s.jobs_new??0)+' new'+(s.error?' · '+esc(s.error):'')+'</div>').join('');
  const err=run.error?'<div class="stage err">'+esc(run.error)+'</div>':'';
  return '<div class="glass soft"><div><strong>'+esc(run.status)+'</strong> · '+esc(run.kind)+(p.summary?' · '+esc(p.summary):'')+'</div>'+stages+warnings+err+'<details><summary>Sources ('+esc((p.sources||[]).length)+')</summary>'+sources+'</details></div>';
}
async function readHomeBody(r){const text=await r.text();let d={};try{d=JSON.parse(text);}catch(e){d={detail:text};}return {ok:r.ok,d,text};}
function homeError(b,fallback){return (b.d&&typeof b.d.detail==='string'?b.d.detail:b.text)||fallback;}
async function loadHome(){try{const b=await readHomeBody(await fetch('/api/home'));if(!b.ok)throw new Error(homeError(b,'The overview could not be loaded.'));renderHome(b.d);}catch(error){document.querySelector('#home-summary').textContent=error.message;}}
function poll(id){if(pollTimer)return;pollTimer=setInterval(async()=>{try{const b=await readHomeBody(await fetch('/api/runs/'+encodeURIComponent(id)));if(!b.ok)return;document.querySelector('#run-progress').innerHTML=renderRun(b.d);if(b.d.status==='COMPLETED'||b.d.status==='FAILED'){clearInterval(pollTimer);pollTimer=null;document.querySelector('#run-now').disabled=false;loadHome();}}catch(e){}},2000);}
async function runNow(){const line=document.querySelector('#run-line');line.textContent='';const prepare=!!document.querySelector('#run-prepare').checked;const b=await readHomeBody(await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'full',prepare:prepare})}));if(!b.ok){line.textContent=homeError(b,'The run could not start.');return;}const log=document.querySelector('#run-log');log.hidden=false;log.open=true;document.querySelector('#run-now').disabled=true;document.querySelector('#run-progress').innerHTML=renderRun(b.d);poll(b.d.id);}
document.querySelector('#run-now').addEventListener('click',runNow);
loadHome();
</script>'''

def home_body(esc_js: str) -> str:
    """Fill the page's sole script dependency from the shared shell module."""

    return HOME_BODY.replace("__ESC__", esc_js)


__all__ = ["HOME_BODY", "HOME_CSS", "home_body"]
