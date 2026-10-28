"""Career plan layout translated from the selected ARTEMIS reference."""

PLAN_BODY = r"""<main class="wrap reference-plan">
<h1>Career plan</h1><p>Turn your goals into a clear strategy.</p>
<div class="reference-tabs" role="tablist" aria-label="Career plan sections">
<button id="tab-overview" role="tab" type="button" aria-selected="true" aria-controls="panel-overview" data-panel="overview">Overview</button>
<button id="tab-goals" role="tab" type="button" aria-selected="false" aria-controls="panel-goals" tabindex="-1" data-panel="goals">Goals</button>
<button id="tab-skills" role="tab" type="button" aria-selected="false" aria-controls="panel-skills" tabindex="-1" data-panel="skills">Skills</button>
<button id="tab-industries" role="tab" type="button" aria-selected="false" aria-controls="panel-industries" tabindex="-1" data-panel="industries">Industries</button>
<button id="tab-locations" role="tab" type="button" aria-selected="false" aria-controls="panel-locations" tabindex="-1" data-panel="locations">Locations</button>
</div>
<p class="line" id="line" aria-live="polite"></p>
<div id="plan"><p>Loading your career plan…</p></div>
</main><script>
__ESC__
let currentPanel='overview';
function list(values){return values&&values.length?'<ul class="career-facts">'+values.map(v=>'<li>'+esc(v)+'</li>').join('')+'</ul>':'<p class="muted">Not set.</p>';}
function pref(icon,title,value){return '<div class="career-pref"><img src="/brand/icons/'+icon+'.svg" alt=""><div><strong>'+esc(value||'Not set')+'</strong><small>'+esc(title)+'</small></div></div>';}
function panel(name,body){return '<section role="tabpanel" id="panel-'+name+'" aria-labelledby="tab-'+name+'"'+(name===currentPanel?'':' hidden')+'>'+body+'</section>';}
function switchPanel(name){
  currentPanel=name;
  document.querySelectorAll('[data-panel]').forEach(b=>{const selected=b.dataset.panel===name;b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;});
  document.querySelectorAll('[role=tabpanel]').forEach(p=>{p.hidden=p.id!=='panel-'+name;});
}
function render(d){
  const box=document.querySelector('#plan'),s=d.search;
  if(!s){box.innerHTML='<section class="card career-empty"><h2>Your direction starts here</h2><p>Set your target roles, goals and preferences to build a career plan from your search.</p><a class="btn" href="/searches">Define your search →</a></section>';return;}
  const c=s.compensation||{},l=s.locations||{};
  const money=v=>v==null?'':(c.currency||'USD')+' '+Number(v).toLocaleString();
  const compensation=c.target?money(c.target):(c.minimum?'Minimum '+money(c.minimum):'Not set');
  const places=[...(l.cities||[]),...(l.metro_areas||[]),...(l.states||[])];
  const work=[l.allow_remote?'Remote':'',l.allow_hybrid?'Hybrid':'',l.allow_onsite?'On site':''].filter(Boolean);
  const goals=pref('dollar-sign','Target compensation',compensation)+pref('map-pin','Location preference',places.join(', '))+pref('briefcase','Preferred industries',(s.industries||[]).join(', '))+pref('globe','Work preference',work.join(' / '));
  const roles='<section class="card"><h2>Your target roles</h2>'+(s.target_titles&&s.target_titles.length?'<ul class="target-roles">'+s.target_titles.map((t,i)=>'<li>'+esc(t)+(i===0?'<span class="pill">Target</span>':'')+'</li>').join('')+'</ul>':'<p class="muted">No target roles set.</p>')+'<p><a href="/searches">+ Add role</a></p></section>';
  const p=d.progress||{};
  const actions=(d.actions||[]).map(a=>'<li><a href="'+esc(a.href)+'">'+esc(a.label)+'</a>'+(a.reason?'<p>'+esc(a.reason)+'</p>':'')+'</li>').join('');
  const overview='<div class="career-overview">'+roles+'<section class="card"><h2>Goals and preferences</h2>'+goals+'</section></div><div class="plan-more"><p class="career-note">'+esc(s.name)+' · '+esc(p.shortlisted||0)+' shortlisted · '+esc(p.prepared||0)+' complete packages. These are stored search preferences, not inferred qualifications.</p>'+(actions?'<details><summary>Recommended next steps</summary><ul>'+actions+'</ul></details>':'')+'</div>';
  const goalBody='<section class="card"><h2>Your goals</h2>'+pref('dollar-sign','Target compensation',compensation)+(c.minimum?pref('target','Minimum compensation',money(c.minimum)):'')+'<p>Shortlist target: '+esc(s.shortlist_target??'not set')+'. Preparation target: '+esc(s.prepare_target??'not set')+'.</p><p><a href="/searches">Edit goals and search limits →</a></p></section>';
  const gaps=d.skill_gaps||[];
  const skills='<section class="card"><h2>Skills and development</h2><p>These gaps are engine assessments, not verified deficits in your experience. Check the underlying evidence before acting.</p>'+(gaps.length?'<ul class="career-facts">'+gaps.map(g=>'<li>'+esc(g.gap)+' <span class="muted">· '+esc(g.count)+' shortlisted role'+(g.count===1?'':'s')+'</span></li>').join('')+'</ul>':'<p class="muted">No assessment gaps have been recorded for this shortlist.</p>')+(d.practical_actions&&d.practical_actions.length?'<h3>Suggested actions</h3><ul>'+d.practical_actions.map(a=>'<li><strong>'+esc(a.label)+'</strong><p>'+esc(a.reason||'')+'</p></li>').join('')+'</ul>':'')+'<p><a href="/opportunities">Review opportunity evidence →</a></p></section>';
  const industries='<section class="card"><h2>Preferred industries</h2>'+list(s.industries)+'<h3>Excluded industries</h3>'+list(s.excluded_industries)+'<p><a href="/searches">Edit industries →</a></p></section>';
  const locations='<section class="card"><h2>Locations and work preferences</h2>'+list(places)+pref('globe','Countries open to remote work',(l.countries||[]).join(', '))+pref('briefcase','Work preference',work.join(' / '))+'<p>Relocation: '+(l.allow_relocation?'allowed':'not enabled')+'. Travel limit: '+esc(s.travel_max_percent==null?'not set':s.travel_max_percent+'%')+'.</p><p><a href="/searches">Edit location constraints →</a></p></section>';
  box.innerHTML=panel('overview',overview)+panel('goals',goalBody)+panel('skills',skills)+panel('industries',industries)+panel('locations',locations);
}
document.querySelectorAll('[data-panel]').forEach((button,index)=>{
  button.addEventListener('click',()=>switchPanel(button.dataset.panel));
  button.addEventListener('keydown',event=>{
    const tabs=Array.from(document.querySelectorAll('[data-panel]'));
    let next=null;if(event.key==='ArrowRight')next=(index+1)%tabs.length;if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;if(event.key==='Home')next=0;if(event.key==='End')next=tabs.length-1;
    if(next!==null){event.preventDefault();switchPanel(tabs[next].dataset.panel);tabs[next].focus();}
  });
});
async function load(){try{const b=await readBody(await fetch('/api/career-plan'));if(!b.ok){document.querySelector('#line').textContent=detailOf(b,'The career plan could not be loaded.');return;}render(b.d);}catch(e){document.querySelector('#line').textContent='The career plan could not be reached. '+e.message;}}
load();
</script>"""
