"""Page shell and the pages added with the browser-first workflow.

`page_shell` is the one place that wraps a page's title, navigation and body, and every
HTML page goes through it. It deliberately contains no `<script>`: each page's own script
is the first (and only) script in the document, which is what the offline JavaScript
tests rely on. Shared header behaviour (the Ask Artemis dialog, the notification count)
is not in the shell either; it rides inside each page's script as `CHROME_JS`, appended
to `ESC_JS` and `ASK_JS`, guarded so including it twice is harmless.

Every dynamic string a page script renders goes through `esc()`.

The visual system is ARTEMIS architectural editorial visual language (see BRAND.md): porcelain workspaces, precise dividers and editorial typography. No photography anywhere.
"""

from __future__ import annotations

from html import escape

from .artemis_theme import EDITORIAL_CSS
from .reference_home import HOME_CSS as REFERENCE_HOME_CSS
from .reference_home import home_body as reference_home_body
from .reference_plan import PLAN_BODY as REFERENCE_PLAN_BODY
from .reference_theme import REFERENCE_CSS

# (label, path). The path is also the key `page_shell(active=...)` matches.
# The five frozen primary destinations; the remaining work surfaces (Profile, Searches,
# Answers, Pipeline, Settings) live behind the account menu so the bar stays minimal.
NAV_LINKS: tuple[tuple[str, str], ...] = (
    ("Home", "/"),
    ("Opportunities", "/opportunities"),
    ("Research", "/research"),
    ("Career Plan", "/career-plan"),
    ("Applications", "/applications"),
)

MENU_LINKS: tuple[tuple[str, str], ...] = (
    ("Profile", "/candidate"),
    ("Pipeline", "/pipeline"),
    ("Searches", "/searches"),
    ("Answers", "/answers"),
    ("Settings", "/settings"),
)

SHELL_CSS = (
    ":root{--cloud:#F7F6F1;--mist:#E8E5DC;--silver:#D6C6A8;--sky:#A6B8A7;"
    "--slate:#849C8D;--storm:#29483E;--ink:#26312E;--muted:#64736C;"
    "--glass:rgba(255,255,255,.60);--glass-soft:rgba(255,255,255,.42);"
    "--glass-high:rgba(255,255,255,.80);--glass-line:rgba(255,255,255,.72);"
    "--line:rgba(214,198,168,.55);"
    "--shadow:0 14px 34px rgba(20,45,41,.10),0 2px 8px rgba(20,45,41,.05);"
    "--shadow-soft:0 8px 22px rgba(20,45,41,.07);"
    "--shadow-high:0 24px 52px rgba(20,45,41,.15),0 3px 10px rgba(20,45,41,.06);"
    "--radius:18px;--radius-sm:12px;--warn:#9C4A38}"
    "*{box-sizing:border-box}"
    "html{-webkit-text-size-adjust:100%}"
    "body{margin:0;color:var(--ink);font:15px/1.55 Inter,system-ui,-apple-system,"
    'BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh;'
    "background:radial-gradient(1200px 700px at 88% -12%,rgba(166,184,167,.55),transparent 62%),"
    "radial-gradient(960px 640px at -12% 18%,rgba(232,229,220,.95),transparent 58%),"
    "radial-gradient(1100px 800px at 52% 112%,rgba(214,198,168,.45),transparent 62%),"
    "linear-gradient(165deg,#FBF9F6 0%,var(--cloud) 42%,#ECF1F2 100%);"
    "background-attachment:fixed}"
    "body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;"
    "background:repeating-linear-gradient(115deg,rgba(113,140,154,.05) 0 1px,transparent 1px 150px)}"
    "body>*{position:relative;z-index:1}"
    "h1,h2,h3,.display{font-family:Sora,Inter,system-ui,sans-serif}"
    "a{color:var(--storm);font-weight:600;text-decoration:none}"
    "a:hover{color:var(--ink)}"
    ":focus-visible{outline:2px solid var(--storm);outline-offset:2px;border-radius:6px}"

    # --- header -------------------------------------------------------------
    ".topnav{display:flex;align-items:center;gap:26px;max-width:1360px;margin:0 auto;"
    "padding:20px 40px 6px;position:relative;z-index:10}"
    ".brand{display:flex;align-items:center;gap:12px;text-decoration:none}"
    ".brand .mark{width:30px;height:30px;flex:none}"
    ".brand .word{font-family:Sora,Inter,sans-serif;font-size:16px;font-weight:600;"
    "letter-spacing:.34em;color:var(--ink);padding-left:.34em}"
    ".topnav nav.primary{display:flex;gap:22px;margin-left:34px}"
    ".topnav nav.primary a{padding:6px 1px;color:#43555F;font-weight:500;font-size:14.5px;"
    "border-bottom:2px solid transparent}"
    ".topnav nav.primary a:hover{color:var(--ink)}"
    ".topnav nav.primary a.active{color:var(--ink);border-bottom-color:var(--storm)}"
    ".nav-right{margin-left:auto;display:flex;align-items:center;gap:12px}"
    ".ask-pill{display:flex;align-items:center;gap:9px;border:1px solid var(--glass-line);"
    "background:var(--glass-soft);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);"
    "border-radius:999px;padding:9px 18px;min-width:216px;color:var(--muted);font:inherit;"
    "font-size:13.5px;cursor:pointer;box-shadow:var(--shadow-soft);text-align:left}"
    ".ask-pill:hover{background:var(--glass);color:var(--ink)}"
    ".ask-pill svg{flex:none}"
    ".bell{position:relative;display:flex;align-items:center;justify-content:center;width:38px;"
    "height:38px;border-radius:50%;border:1px solid var(--glass-line);background:var(--glass-soft);"
    "backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);box-shadow:var(--shadow-soft)}"
    ".bell:hover{background:var(--glass)}"
    ".bell .badge{position:absolute;top:-4px;right:-4px;min-width:17px;height:17px;"
    "border-radius:999px;background:var(--storm);color:#fff;font-size:10.5px;font-weight:700;"
    "display:flex;align-items:center;justify-content:center;padding:0 4px}"
    ".usermenu{position:relative}"
    ".usermenu summary{list-style:none;cursor:pointer}"
    ".usermenu summary::-webkit-details-marker{display:none}"
    ".avatar{display:flex;align-items:center;justify-content:center;width:38px;height:38px;"
    "border-radius:50%;background:linear-gradient(140deg,var(--sky),var(--storm));color:#fff;"
    "font-size:12.5px;font-weight:700;letter-spacing:.04em;border:1px solid var(--glass-line);"
    "box-shadow:var(--shadow-soft)}"
    ".usermenu .menu{position:absolute;right:0;top:48px;min-width:180px;padding:8px;z-index:30;"
    "background:var(--glass-high);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);"
    "border:1px solid var(--glass-line);border-radius:14px;box-shadow:var(--shadow)}"
    ".usermenu .menu a{display:block;padding:8px 12px;border-radius:9px;color:var(--ink);"
    "font-weight:500;font-size:14px}"
    ".usermenu .menu a:hover{background:rgba(166,184,167,.25)}"
    ".navtoggle{display:none;position:relative}"
    ".navtoggle summary{list-style:none;cursor:pointer;padding:8px;color:var(--ink)}"
    ".navtoggle summary::-webkit-details-marker{display:none}"
    ".navtoggle .mobilemenu{position:absolute;right:0;top:44px;min-width:200px;padding:8px;"
    "z-index:30;background:var(--glass-high);backdrop-filter:blur(18px);"
    "-webkit-backdrop-filter:blur(18px);border:1px solid var(--glass-line);border-radius:14px;"
    "box-shadow:var(--shadow)}"
    ".navtoggle .mobilemenu a{display:block;padding:9px 12px;border-radius:9px;color:var(--ink);"
    "font-weight:500}"
    ".navtoggle .mobilemenu a.active{background:rgba(166,184,167,.3)}"

    # --- glass boards ---------------------------------------------------------
    ".glass,.card{background:var(--glass);backdrop-filter:blur(16px) saturate(1.08);"
    "-webkit-backdrop-filter:blur(16px) saturate(1.08);border:1px solid var(--glass-line);"
    "border-radius:var(--radius);box-shadow:var(--shadow)}"
    ".card{padding:22px;margin:16px 0}"
    ".glass.soft{background:var(--glass-soft);box-shadow:var(--shadow-soft)}"
    ".glass.elevated{background:var(--glass-high);box-shadow:var(--shadow-high);position:relative;"
    "overflow:hidden}"
    ".glass.elevated::before{content:'';position:absolute;inset:0 0 auto;height:120px;"
    "background:linear-gradient(180deg,rgba(255,255,255,.55),transparent);pointer-events:none}"
    ".glass.interactive{transition:transform .18s ease,box-shadow .18s ease}"
    ".glass.interactive:hover{transform:translateY(-2px);box-shadow:var(--shadow-high)}"

    # --- shared primitives ------------------------------------------------------
    ".wrap{max-width:1080px;margin:0 auto;padding:36px 24px 72px}"
    ".wrap h1{font-size:32px;letter-spacing:-.02em;margin:8px 0 6px}"
    ".wrap h2{font-size:17px;margin:0 0 10px;letter-spacing:-.01em}"
    ".muted,.wrap p{color:var(--muted)}"
    ".eyebrow{font-size:11.5px;font-weight:600;letter-spacing:.22em;color:var(--slate);"
    "text-transform:uppercase;margin:0}"
    ".btn,.wrap button{background:linear-gradient(140deg,#3E5769,var(--ink));color:#fff;border:0;"
    "border-radius:11px;padding:10px 18px;cursor:pointer;font:inherit;font-weight:600}"
    ".btn{display:inline-block;text-decoration:none}"
    ".btn:hover{color:#fff;filter:brightness(1.08)}"
    ".wrap button:disabled{background:var(--slate);cursor:not-allowed}"
    ".wrap button.secondary,.btn.ghost,.ghost{background:var(--glass-soft);color:var(--ink);"
    "border:1px solid var(--silver);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px)}"
    ".ghost{border-radius:11px;padding:10px 18px;cursor:pointer;font:inherit;font-weight:600}"
    ".wrap button.secondary:hover,.btn.ghost:hover,.ghost:hover{background:var(--glass)}"
    ".wrap input,.wrap select,.wrap textarea{width:100%;padding:9px 11px;border:1px solid var(--silver);"
    "border-radius:9px;font:inherit;background:rgba(255,255,255,.75);color:var(--ink)}"
    ".wrap label{display:block;color:var(--muted);font-size:13px;margin:10px 0 4px}"
    ".wrap label.check{display:inline-flex;align-items:center;gap:6px;margin:6px 14px 6px 0;"
    "color:var(--ink);font-size:14px}"
    ".wrap label.check input{width:auto}"
    ".grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 18px}"
    ".grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:0 18px}"
    ".pill{display:inline-block;border-radius:999px;padding:2px 10px;font-size:12px;"
    "font-weight:600;background:var(--mist);color:var(--ink)}"
    ".pill.VERIFIED{background:#D9EAE2}.pill.INFERRED{background:#F0E9DB}"
    ".pill.UNKNOWN{background:var(--mist)}"
    ".wrap [hidden]{display:none}"
    ".line{color:var(--muted);min-height:1.4em}"
    ".err{color:var(--warn)}"
    "dialog{border:1px solid var(--glass-line);border-radius:16px;padding:24px;max-width:460px;"
    "background:var(--glass-high);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);"
    "color:var(--ink);box-shadow:var(--shadow-high)}"
    "dialog::backdrop{background:rgba(20,45,41,.35)}"
    "dialog p{margin:0 0 16px;color:var(--ink)}"
    "dialog input{width:100%;padding:9px;border:1px solid var(--silver);border-radius:9px;"
    "font:inherit;margin:0 0 14px;background:rgba(255,255,255,.8)}"
    "dialog [hidden]{display:none}"
    "dialog .ask-actions{display:flex;gap:8px;justify-content:flex-end}"
    "dialog button{background:linear-gradient(140deg,#3E5769,var(--ink));color:#fff;border:0;"
    "border-radius:10px;padding:9px 15px;font:inherit;font-weight:600;cursor:pointer}"
    "dialog button.secondary{background:var(--glass-soft);color:var(--ink);border:1px solid var(--silver)}"

    # --- Ask Artemis -----------------------------------------------------------
    ".ask-modal{max-width:520px;width:min(520px,92vw)}"
    ".ask-modal h2{margin:0 0 4px;font-size:19px}"
    ".ask-chips{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 16px}"
    ".ask-chips button{background:var(--glass-soft);color:var(--ink);border:1px solid var(--silver);"
    "border-radius:999px;padding:6px 13px;font-size:12.5px;font-weight:500}"
    ".ask-chips button:hover{background:var(--glass)}"
    ".ask-modal textarea{width:100%;min-height:64px;padding:10px;border:1px solid var(--silver);"
    "border-radius:10px;font:inherit;background:rgba(255,255,255,.8);color:var(--ink);resize:vertical}"
    ".ask-answer{margin-top:14px;padding:14px;border-radius:12px;background:rgba(255,255,255,.66);"
    "border:1px solid var(--line);font-size:14px;white-space:pre-wrap}"
    ".ask-answer .ask-links{margin-top:10px;display:flex;gap:14px;flex-wrap:wrap}"

    "@media(max-width:960px){"
    ".topnav{flex-wrap:wrap;gap:14px;padding:16px 20px 4px}"
    ".topnav nav.primary{display:none}"
    ".navtoggle{display:block}"
    ".ask-pill{min-width:0;padding:9px 12px}.ask-pill .ask-label{display:none}"
    ".grid2,.grid3{grid-template-columns:1fr}.wrap{padding:24px 16px 56px}}"
    "@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}"
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

# Header behaviour shared by every page. It rides inside each page's single script (the
# shell itself must stay script-free for the offline tests), so it is guarded against
# double inclusion and uses only function declarations plus the stubs the test harness
# provides (no window, no location, no classList).
CHROME_JS = r"""
function _vesc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function _veloraChrome(){
  if(globalThis.__veloraChrome)return;globalThis.__veloraChrome=true;
  const $=s=>document.querySelector(s);
  try{
    fetch('/api/today').then(r=>r.ok?r.json():null).then(d=>{
      if(!d)return;const c=d.counts||{};
      const n=(c.ready_for_approval|0)+(c.needs_information|0)+(c.handoffs_waiting|0)+(c.employer_responses|0);
      const b=$('#velora-bell-badge');
      if(b&&n>0){b.textContent=String(n);b.hidden=false;}
    }).catch(()=>{});
    fetch('/api/status').then(r=>r.ok?r.json():null).then(d=>{
      if(!d)return;const name=(d.candidate_name||'').trim();
      const av=$('#velora-avatar');
      if(av&&name){const parts=name.split(/\s+/);av.textContent=((parts[0]||' ')[0]+(parts.length>1?parts[parts.length-1][0]:'')).toUpperCase();}
    }).catch(()=>{});
    // The dialog markup is appended after the page script, so the modal's
    // internals do not exist yet when this runs. The open button is in the header
    // and can be wired at once; everything inside the dialog waits for
    // DOMContentLoaded and is looked up lazily, never captured before it exists.
    const openDlg=()=>{
      const dlg=$('#velora-ask');if(!dlg)return;
      if(typeof dlg.showModal==='function'){if(!dlg.open)dlg.showModal();}else dlg.setAttribute('open','');
      const q=$('#velora-ask-q');if(q&&typeof q.focus==='function')q.focus();
    };
    const closeDlg=()=>{
      const dlg=$('#velora-ask');if(!dlg)return;
      if(typeof dlg.close==='function')dlg.close();else dlg.removeAttribute('open');
    };
    const openBtn=$('#velora-ask-open');
    if(openBtn)openBtn.addEventListener('click',openDlg);
    const mobileAsk=$('#mobile-ask-open');if(mobileAsk)mobileAsk.addEventListener('click',openDlg);
    const send=async question=>{
      const out=$('#velora-ask-out');if(!out)return;
      out.hidden=false;
      out.textContent='Thinking over your data...';
      try{
        const r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:question})});
        const text=await r.text();let d={};try{d=JSON.parse(text)}catch(e){d={detail:text}};
        if(!r.ok){out.textContent=(typeof d.detail==='string'?d.detail:text)||'Artemis could not answer that.';return;}
        out.innerHTML=_vesc(d.answer||'')+((d.links&&d.links.length)?'<span class="ask-links">'+d.links.map(l=>`<a href="${_vesc(l.href)}">${_vesc(l.label)}</a>`).join('')+'</span>':'');
      }catch(e){out.textContent='Artemis could not be reached. '+e;}
    };
    const wireAsk=()=>{
      const dlg=$('#velora-ask');if(!dlg||dlg.__wired)return;dlg.__wired=true;
      const closeBtn=$('#velora-ask-close'),goBtn=$('#velora-ask-go'),q=$('#velora-ask-q');
      if(closeBtn)closeBtn.addEventListener('click',closeDlg);
      if(goBtn)goBtn.addEventListener('click',()=>{if(q&&q.value.trim())send(q.value.trim());});
      if(q)q.addEventListener('keydown',e=>{if(e&&e.key==='Enter'&&q.value.trim()){e.preventDefault();send(q.value.trim());}});
      Array.prototype.forEach.call(dlg.querySelectorAll('button[data-q]'),b=>b.addEventListener('click',()=>{
        const text=b.getAttribute('data-q')||'';if(q)q.value=text;send(text);
      }));
    };
    if(document.readyState&&document.readyState!=='loading')wireAsk();
    else if(typeof document.addEventListener==='function')document.addEventListener('DOMContentLoaded',wireAsk);
    if(typeof document.addEventListener==='function')document.addEventListener('click',e=>{
      const menu=$('#velora-usermenu');
      if(menu&&menu.open&&e&&e.target&&typeof e.target.closest==='function'&&!e.target.closest('#velora-usermenu'))menu.open=false;
    });
  }catch(e){}
}
_veloraChrome();
"""

# The page's own confirmation dialog. The Claude desktop app's embedded browser suppresses
# native confirm()/prompt()/alert() (confirm() returns false, so the button silently did
# nothing), so a control that needs an explicit yes uses this `<dialog>` instead. It holds
# no script: each page's script carries `ASK_JS`.
ASK_DIALOG_HTML = (
    '<dialog id="ask-dialog" aria-labelledby="ask-text"><p id="ask-text"></p>'
    '<input id="ask-input" type="text" autocomplete="off" aria-label="Confirmation" hidden>'
    '<div class="ask-actions"><button type="button" class="secondary" id="ask-cancel">Cancel'
    '</button><button type="button" id="ask-ok">Confirm</button></div></dialog>'
)

# `ask(message)` resolves true on Confirm and false on Cancel or Escape.
# `ask(message, {input: true})` also shows a text box and resolves to what was typed, or
# null on Cancel or Escape.
ASK_JS = r"""function ask(message,opts){
  opts=opts||{};
  const dialog=document.querySelector('#ask-dialog'),text=document.querySelector('#ask-text'),
    input=document.querySelector('#ask-input'),ok=document.querySelector('#ask-ok'),
    cancel=document.querySelector('#ask-cancel');
  return new Promise(resolve=>{
    let settled=false;
    const finish=value=>{
      if(settled)return;
      settled=true;
      ok.onclick=null;cancel.onclick=null;dialog.onclose=null;input.onkeydown=null;
      if(dialog.open){if(typeof dialog.close==='function')dialog.close();else dialog.removeAttribute('open');}
      resolve(value);
    };
    text.textContent=message;
    input.hidden=!opts.input;input.value='';
    ok.textContent=opts.confirmLabel||'Confirm';
    ok.onclick=()=>finish(opts.input?input.value:true);
    cancel.onclick=()=>finish(opts.input?null:false);
    // close() queues its event, so a stale one can arrive after the next question has
    // reopened the dialog; only a dialog that is really closed counts as a Cancel.
    dialog.onclose=()=>{if(!dialog.open)finish(opts.input?null:false);};
    input.onkeydown=e=>{if(e&&e.key==='Enter'){if(e.preventDefault)e.preventDefault();ok.onclick();}};
    if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');
    if(opts.input&&input.focus)input.focus();
  });
}
""" + CHROME_JS

ESC_JS = ESC_JS + CHROME_JS

# Keep this inline header mark geometrically identical to brand/logo/artemis-mark.svg.
LOGO_MARK_SVG = (
    '<svg class="mark" viewBox="0 0 64 64" role="img" aria-label="Artemis" '
    'xmlns="http://www.w3.org/2000/svg">'
    '<path d="M8 57 29.5 7H32L12 57Z" fill="#142D29"/>'
    '<path d="M31.5 7H35L57 57H48.5Z" fill="#142D29"/>'
    '</svg>'
)

BELL_SVG = (
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#43555F" '
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/>'
    '<path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>'
)

SEARCH_SVG = (
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#64736C" '
    'stroke-width="2" stroke-linecap="round" aria-hidden="true">'
    '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>'
)

# HTML-only Ask Artemis modal; the page script's CHROME_JS wires it to /api/ask.
ASK_ARTEMIS_HTML = (
    '<dialog class="ask-modal" id="velora-ask" aria-labelledby="velora-ask-title">'
    '<h2 id="velora-ask-title">Ask Artemis</h2>'
    '<p class="muted">Answers come from your real data — nothing is invented.</p>'
    '<div class="ask-chips">'
    '<button type="button" data-q="What should I prioritize today?">What should I prioritize today?</button>'
    '<button type="button" data-q="Find roles matching my experience">Find roles matching my experience</button>'
    '<button type="button" data-q="Why does the top position fit me?">Why does the top position fit me?</button>'
    '<button type="button" data-q="Research the companies on my shortlist">Research my companies</button>'
    '<button type="button" data-q="What changed since the last run?">What changed?</button>'
    '</div>'
    '<textarea id="velora-ask-q" aria-label="Ask Artemis a question" '
    'placeholder="Ask Artemis"></textarea>'
    '<div class="ask-actions"><button type="button" class="secondary" id="velora-ask-close">Close</button>'
    '<button type="button" id="velora-ask-go">Ask</button></div>'
    '<div class="ask-answer" id="velora-ask-out" hidden></div>'
    '</dialog>'
)


def page_shell(title: str, body: str, *, active: str, css: str = "") -> str:
    """Wrap a page body in the shared head and navigation.

    `active` is the nav path to highlight (for example "/opportunities"). `css` is
    page-specific CSS appended after the shared styles.
    """

    body = body.replace('<main ', '<main id="main-content" tabindex="-1" ', 1)
    current = ' class="active" aria-current="page"'
    links = "".join(
        f'<a href="{path}"{current if path == active else ""}>{escape(label)}</a>'
        for label, path in NAV_LINKS
    )
    menu = "".join(
        f'<a href="{path}"{current if path == active else ""}>{escape(label)}</a>'
        for label, path in (*NAV_LINKS, *MENU_LINKS)
    )
    account = "".join(
        f'<a href="{path}"{current if path == active else ""}>{escape(label)}</a>'
        for label, path in MENU_LINKS
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="description" content="Artemis — your intelligent career operating '
        'system. Find the right opportunities. Present your best self. Move with confidence.">'
        '<link rel="icon" type="image/svg+xml" href="/favicon.svg">'
        f"<title>Artemis | {escape(title)}</title>"
        f"<style>{SHELL_CSS}{css}{EDITORIAL_CSS}{REFERENCE_CSS}</style></head>\n"
        '<body><a class="skip-link" href="#main-content">Skip to content</a><header class="topnav">'
        f'<a class="brand" href="/">{LOGO_MARK_SVG}<span class="word">ARTEMIS</span></a>'
        f'<nav class="primary" aria-label="Main">{links}</nav>'
        '<div class="nav-right">'
        f'<button type="button" class="ask-pill" id="velora-ask-open" aria-label="Ask Artemis">{SEARCH_SVG}'
        '<span class="ask-label">Ask Artemis</span></button>'
        f'<a class="bell" href="/#upcoming" aria-label="Notifications">{BELL_SVG}'
        '<span class="badge" id="velora-bell-badge" hidden></span></a>'
        '<details class="usermenu" id="velora-usermenu"><summary class="avatar" '
        'id="velora-avatar" aria-label="Account">·</summary>'
        f'<div class="menu">{account}</div></details>'
        '<details class="navtoggle"><summary aria-label="Menu"><img src="/brand/icons/menu.svg" width="18" height="18" alt=""></summary>'
        f'<div class="mobilemenu">{menu}<button type="button" id="mobile-ask-open">Ask Artemis</button></div></details>'
        '</div></header>\n'
        f"{body}{ASK_DIALOG_HTML}{ASK_ARTEMIS_HTML}</body></html>"
    )


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

HOME_CSS = REFERENCE_HOME_CSS


def home_body() -> str:
    return reference_home_body(ESC_JS)


# ---------------------------------------------------------------------------
# Candidate (Profile)
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
<h1>Profile</h1>
<p>What Artemis knows about you, and how sure it is. <span class="pill VERIFIED">VERIFIED</span> is
something you stated. <span class="pill INFERRED">INFERRED</span> was read from your resume and is
never typed onto a form until you confirm it. <span class="pill UNKNOWN">UNKNOWN</span> is not
known.</p>
<section class="card"><h2>Your details</h2>
<p>Only what you change here, or tick <em>This is right</em> for, is recorded as stated by you
(VERIFIED), and that wins over anything read from a resume. Every other box keeps what it was and
how sure Artemis was of it; the tag beside each box shows that. A blank box changes nothing.</p>
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
function renderUnparsed(list){
  // The master is the version Artemis builds packages from, so its unread lines come first.
  // A version stored before unread lines were recorded has a count but no list.
  const unlisted=r=>r.unparsed_lines==null&&Number(r.unparsed_line_count)>0;
  const rows=(list||[]).filter(r=>(r.unparsed_lines&&r.unparsed_lines.length)||(r.warnings&&r.warnings.length)||unlisted(r));
  if(!rows.length)return '';
  return rows.map(r=>{
    const lines=r.unparsed_lines||[],notes=r.warnings||[];
    return `<div class="stage unparsed"><strong>Version ${esc(r.version)}</strong>`+
      (lines.length?`<p>These lines were not understood:</p><ul>${lines.map(l=>`<li>${esc(l)}</li>`).join('')}</ul>`:'')+
      (unlisted(r)?`<p>${esc(r.unparsed_line_count)} line(s) were not understood, but this version was stored before they were recorded. Upload the resume again to list them.</p>`:'')+
      (notes.length?`<p>Check these:</p><ul>${notes.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:'')+`</div>`;
  }).join('');
}
async function load(){
  const b=await readBody(await fetch('/api/candidate'));
  if(!b.ok){document.querySelector('#profile').textContent=detailOf(b,'The profile could not be loaded.');return;}
  const d=b.d;
  document.querySelector('#profile').innerHTML=renderSections(d.sections);
  document.querySelector('#resumes').innerHTML=renderResumes(d.resumes)+renderUnparsed(d.resumes);
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
  const unread=b.ok&&b.d&&b.d.unparsed_lines?b.d.unparsed_lines.length:0;
  line.textContent=b.ok?'Resume stored as inferred evidence. Confirm your details above to make them yours.'+(unread?' '+unread+' line(s) were not understood; they are listed above.':''):detailOf(b,'Upload failed');
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
<p>A search says what kind of job to look for. The active one is what Run search uses.</p>
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
<div><label>Countries remote work may be in (one per line, e.g. US; leave blank for anywhere)<textarea name="countries" rows="3"></textarea></label></div>
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
<div><label>Most roles from one company on the shortlist<input name="max_per_company" type="number" min="1"></label></div>
</div>
<p><button type="submit" id="save">Save search</button> <button type="button" class="secondary" id="activate">Make this the active search</button></p>
<p class="line" id="line"></p></form></section>
</main>
<script>
__ESC__
const LIST_FIELDS=['target_titles','acceptable_titles','excluded_titles','excluded_keywords','role_family_terms','role_level_terms','industries','excluded_industries'];
const LOC_FIELDS={cities:'cities',states:'states',metro_areas:'metro_areas',countries:'countries'};
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
  set('max_per_company',p.max_per_company==null?'':p.max_per_company);
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
  const mc=num(g('max_per_company'));if(mc!==null)p.max_per_company=mc;
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
  fill({name:'',locations:{allow_remote:true,allow_hybrid:true,allow_onsite:true,countries:['US']},compensation:{currency:'USD'},employment_types:['FULL_TIME'],shortlist_target:10,prepare_target:3,max_per_company:2,max_age_days:90});
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
<p>What Artemis is set to do. The values below are changed outside the browser; the job boards can be changed here.</p>
<section class="card"><div id="settings">Loading...</div></section>
<section class="card">
<h2>Job boards</h2>
<p>The company boards Artemis reads each run. A role on a board Artemis cannot fill itself (anything except Greenhouse and Lever) is opened in your browser for you to complete.</p>
<div id="sources">Loading...</div>
<p class="line" id="sources-line"></p>
</section>
<section class="card">
<h2>Add a board</h2>
<p>Paste a careers URL (Greenhouse, Lever or Ashby). Artemis reads the board once, shows what it found, and adds it only when you press Add.</p>
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


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------

RESEARCH_CSS = (
    ".r-card{padding:20px 24px;margin:16px 0}"
    ".r-card h2{font-size:18px;margin:0}"
    ".r-card .r-sub{color:var(--muted);font-size:13.5px;margin:2px 0 8px}"
    ".r-cols{display:grid;grid-template-columns:1fr 1fr;gap:4px 24px}"
    ".r-cols h3{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--slate);"
    "margin:12px 0 4px}"
    ".r-cols ul{margin:4px 0 8px;padding-left:20px}"
    ".r-src{color:var(--muted);font-size:12.5px}"
    "@media(max-width:700px){.r-cols{grid-template-columns:1fr}}"
)

RESEARCH_BODY = """<main class="wrap">
<h1>Research</h1>
<p>What Artemis has learned about the companies on your shortlist. Verified facts are read from a source; inferences are conclusions, kept apart. Every card names its sources.</p>
<p class="line" id="line"></p>
<div id="list"><section class="card"><p>Loading...</p></section></div>
</main>
<script>
__ESC__
function ul(items){return items&&items.length?'<ul>'+items.map(i=>`<li>${esc(i)}</li>`).join('')+'</ul>':'<p class="muted">None recorded.</p>';}
function render(rows){
  const box=document.querySelector('#list');
  if(!rows||!rows.length){box.innerHTML='<section class="card"><p>No company research yet. Research runs on the shortlist at the end of each run.</p></section>';return;}
  box.innerHTML=rows.map(r=>{
    const web=r.company_website&&/^https?:\\/\\//i.test(r.company_website)?`<a href="${esc(r.company_website)}" target="_blank" rel="noopener noreferrer">${esc(r.company_website)}</a>`:'';
    return `<section class="card r-card"><h2>${esc(r.company)}</h2>
    <p class="r-sub">${esc(r.title||'')} · ${esc(r.industry||'industry not recorded')} · updated ${esc(r.updated_at?new Date(r.updated_at).toLocaleDateString():'recently')}</p>
    ${r.company_description?`<p>${esc(r.company_description)}</p>`:''}
    <div class="r-cols"><div><h3>Verified facts</h3>${ul(r.verified_facts)}</div>
    <div><h3>Inferences</h3>${ul(r.inferences)}</div></div>
    ${(r.recent_developments||[]).length?'<h3>Recent developments</h3>'+ul(r.recent_developments):''}
    <p class="r-src">Sources: ${(r.sources||[]).length?esc(r.sources.join(' · ')):'none recorded'} ${web?' · '+web:''}</p>
    <p><a href="/opportunities/${encodeURIComponent(r.opportunity_id)}">Open the opportunity</a></p></section>`;
  }).join('');
}
async function load(){
  const b=await readBody(await fetch('/api/research'));
  if(!b.ok){document.querySelector('#line').textContent=detailOf(b,'Research could not be loaded.');return;}
  render(b.d.research);
}
load();
</script>
""".replace("__ESC__", ESC_JS)


# ---------------------------------------------------------------------------
# Career Plan
# ---------------------------------------------------------------------------

PLAN_CSS = (
    ".plan-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 24px}"
    ".gap-row{display:flex;justify-content:space-between;gap:12px;padding:9px 0;"
    "border-top:1px solid var(--line)}.gap-row:first-child{border-top:0}"
    ".gap-row .n{background:rgba(166,184,167,.35);border-radius:999px;min-width:26px;height:22px;"
    "line-height:22px;text-align:center;font-size:12px;font-weight:700;padding:0 6px}"
    ".plan-actions{list-style:none;margin:0;padding:0}"
    ".plan-actions li{padding:10px 0;border-top:1px solid var(--line)}"
    ".plan-actions li:first-child{border-top:0}"
    ".plan-meter{height:8px;border-radius:999px;background:var(--mist);overflow:hidden;margin:4px 0 12px}"
    ".plan-meter i{display:block;height:100%;border-radius:999px;"
    "background:linear-gradient(90deg,var(--sky),var(--storm))}"
    "@media(max-width:700px){.plan-grid{grid-template-columns:1fr}}"
)

PLAN_BODY = REFERENCE_PLAN_BODY.replace("__ESC__", ESC_JS)


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


__all__ = [
    "ASK_DIALOG_HTML",
    "ASK_JS",
    "CANDIDATE_BODY",
    "CHROME_JS",
    "ESC_JS",
    "HOME_CSS",
    "LOGO_MARK_SVG",
    "MENU_LINKS",
    "NAV_LINKS",
    "PLAN_BODY",
    "PLAN_CSS",
    "RESEARCH_BODY",
    "RESEARCH_CSS",
    "SEARCHES_BODY",
    "SETTINGS_BODY",
    "SHELL_CSS",
    "home_body",
    "page_shell",
    "searches_body",
]
