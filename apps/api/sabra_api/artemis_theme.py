"""Shared ARTEMIS Quiet Strategist visual layer.

Append ``EDITORIAL_CSS`` after the existing shell and page styles. Keeping the
overrides in one final stylesheet lets the browser-first page markup migrate
without changing its structure or behavior.
"""

EDITORIAL_CSS = r"""
@font-face{font-family:'Artemis Display';src:url('/brand/fonts/CormorantGaramond-Variable.ttf') format('truetype');font-style:normal;font-weight:300 700;font-display:swap}
@font-face{font-family:'Artemis Sans';src:url('/brand/fonts/Manrope-Variable.ttf') format('truetype');font-style:normal;font-weight:200 800;font-display:swap}
.skip-link{position:absolute;left:16px;top:-100px;padding:12px;background:#142D29;color:#F7F6F1;z-index:100}.skip-link:focus{top:10px;color:#F7F6F1}
.brief-copy{max-width:36rem;margin:28px 0 20px}.brief-copy p{margin:7px 0}.brief-changes{display:flex;gap:18px;flex-wrap:wrap;list-style:none;padding:0;font-size:12px}.brief-changes strong{font-size:17px}.brief-changes span{display:block;color:var(--muted)}
.brief-action{border-left:2px solid #29483E;padding:0 0 0 18px;margin:24px 0}.brief-action .eyebrow{display:block;margin-bottom:9px}.brief-action p{max-width:35rem;font-size:13px}.brand-note{margin-top:48px;padding-top:22px;border-top:1px solid #D8D6CC;font-size:12px;letter-spacing:.03em;color:#52615A}

:root {
  color-scheme: light;
  --evergreen: #142D29;
  --forest: #29483E;
  --sage: #A6B8A7;
  --stone: #E8E5DC;
  --sand: #D6C6A8;
  --porcelain: #F7F6F1;
  --graphite: #26312E;
  --cloud: var(--porcelain);
  --mist: var(--stone);
  --silver: var(--sand);
  --sky: var(--sage);
  --slate: #52665C;
  --storm: var(--forest);
  --ink: var(--graphite);
  --muted: #52615A;
  --line: #D8D6CC;
  --glass: var(--porcelain);
  --glass-soft: #F2F0E9;
  --glass-high: #FFFFFF;
  --glass-line: #D8D6CC;
  --shadow: 0 8px 24px rgba(20,45,41,.055);
  --shadow-soft: 0 3px 12px rgba(20,45,41,.045);
  --shadow-high: 0 12px 32px rgba(20,45,41,.10);
  --radius: 8px;
  --radius-sm: 5px;
  --warn: #8B382D;
}

*,*::before,*::after { box-sizing: border-box; }
html { background: var(--porcelain); scroll-behavior: smooth; }
body {
  min-height: 100vh;
  margin: 0;
  color: var(--graphite);
  background: var(--porcelain);
  font: 15px/1.6 'Artemis Sans', Inter, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
body::before { content: none; }
h1,h2,h3,.display { color: var(--evergreen); font-family: 'Artemis Display', Georgia, serif; font-weight: 500; }
h1 { letter-spacing: -.025em; }
h2 { letter-spacing: -.012em; }
p { margin-top: .55em; }
a { color: var(--forest); text-decoration-thickness: 1px; text-underline-offset: 3px; }
a:hover { color: var(--evergreen); }
::selection { color: var(--porcelain); background: var(--forest); }
:focus-visible { outline: 3px solid #527564; outline-offset: 3px; border-radius: 3px; }

/* A quiet horizontal masthead replaces the floating/glass navigation. */
.topnav {
  max-width: none;
  min-height: 74px;
  padding: 13px max(28px, calc((100vw - 1320px) / 2)) 12px;
  gap: 24px;
  border-bottom: 1px solid var(--line);
  background: var(--porcelain);
}
.brand { gap: 10px; flex: none; }
.brand .mark { width: 30px; height: 30px; }
.brand .word {
  padding-left: .28em;
  color: var(--evergreen);
  font: 600 13px/1 'Artemis Sans', Inter, sans-serif;
  letter-spacing: .28em;
}
.topnav nav.primary { gap: 22px; margin-left: 22px; align-self: stretch; align-items: center; }
.topnav nav.primary a {
  display: inline-flex;
  align-items: center;
  height: 100%;
  padding: 2px 0 0;
  border-bottom: 1px solid transparent;
  color: #46564E;
  font-size: 13px;
  font-weight: 500;
  white-space: nowrap;
}
.topnav nav.primary a:hover { color: var(--evergreen); }
.topnav nav.primary a.active { color: var(--evergreen); border-bottom: 2px solid var(--forest); }
.nav-right { gap: 9px; }
.ask-pill,.bell,.avatar {
  min-height: 36px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: transparent;
  box-shadow: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.ask-pill { min-width: 0; padding: 7px 11px; color: var(--muted); font-size: 12px; }
.ask-pill:hover,.bell:hover { color: var(--evergreen); background: var(--stone); }
.ask-pill svg { color: var(--forest); }
.bell { width: 36px; height: 36px; }
.bell svg { stroke: var(--forest); }
.bell .badge { top: -5px; right: -5px; min-width: 17px; height: 17px; background: var(--forest); }
.avatar { width: 36px; height: 36px; color: var(--porcelain); background: var(--evergreen); border-color: var(--evergreen); font-size: 11px; }
.usermenu .menu,.navtoggle .mobilemenu {
  padding: 6px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--porcelain);
  box-shadow: var(--shadow-high);
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.usermenu .menu a,.navtoggle .mobilemenu a { padding: 9px 11px; border-radius: 3px; color: var(--graphite); font-size: 13px; }
.usermenu .menu a:hover,.navtoggle .mobilemenu a:hover,.navtoggle .mobilemenu a.active { background: var(--stone); }
.navtoggle summary { border: 1px solid var(--line); border-radius: 5px; }

/* Surfaces use paper, hairline rules and restrained depth. */
.glass,.card {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #FBFAF6;
  box-shadow: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.card { padding: 21px 23px; margin: 15px 0; }
.glass.soft { background: var(--stone); box-shadow: none; }
.glass.elevated { overflow: visible; background: #FBFAF6; box-shadow: var(--shadow); }
.glass.elevated::before { content: none; }
.glass.interactive { transition: border-color .16s ease, background-color .16s ease; }
.glass.interactive:hover { transform: none; border-color: #9BA99E; background: #FFFFFF; box-shadow: var(--shadow); }
.wrap { max-width: 1120px; padding: 40px 28px 76px; }
.wrap h1 { margin: 7px 0 8px; font-size: clamp(32px,4vw,42px); line-height: 1.12; }
.wrap h2 { margin: 0 0 10px; font-size: 20px; line-height: 1.25; }
.wrap h3 { margin: 17px 0 5px; font-size: 17px; line-height: 1.3; }
.muted,.wrap p { color: var(--muted); }
.eyebrow { color: var(--forest); font-size: 10px; font-weight: 700; letter-spacing: .19em; }
.line { min-height: 1.4em; color: var(--muted); }
.err,.error { color: var(--warn); }
.pill { padding: 3px 9px; border: 1px solid #D5D8CF; border-radius: 3px; background: #ECEDE7; color: #34473D; font-size: 11px; font-weight: 650; }
.pill.VERIFIED,.pill.passed,.pill.ok { border-color: #B8CCBA; background: #E5EDE4; color: #254A37; }
.pill.INFERRED,.pill.flag { border-color: #D6C6A8; background: #F1EBDD; color: #51452D; }
.pill.UNKNOWN { border-color: #D8D6CC; background: #ECEBE5; color: #46514B; }
.pill.rejected,.pill.bad { border-color: #D8BDB4; background: #F3E5E0; color: #74372D; }

.stat,.filters,.queue,.row,.wrap form,.wrap li{backdrop-filter:none;-webkit-backdrop-filter:none}
.stat,.filters,.queue{background:#FBFAF6;border:1px solid var(--line);box-shadow:none;border-radius:5px}
.stat strong,.queue h2{font-family:'Artemis Sans',system-ui,sans-serif}
.filters input{flex:1 1 140px;min-width:0}
.row{min-width:0}.row>div{min-width:0;overflow-wrap:anywhere}.row code{overflow-wrap:anywhere}
/* Clear, consistent actions and controls. */
.btn,.wrap button,dialog button,.home button {
  min-height: 40px;
  padding: 9px 16px;
  border: 1px solid var(--forest);
  border-radius: 4px;
  color: var(--porcelain);
  background: var(--forest);
  box-shadow: none;
  font: 600 13px/1.3 'Artemis Sans', Inter, system-ui, sans-serif;
  cursor: pointer;
}
.btn { display: inline-flex; align-items: center; text-decoration: none; }
.btn:hover,.wrap button:hover,dialog button:hover,.home button:hover { color: #fff; background: var(--evergreen); border-color: var(--evergreen); filter: none; }
.wrap button:disabled,.home button:disabled { color: #F7F6F1; background: #718078; border-color: #718078; cursor: not-allowed; }
.wrap button.secondary,.btn.ghost,.ghost,.home button.ghost,dialog button.secondary,.ask-chips button {
  color: var(--evergreen);
  background: transparent;
  border: 1px solid #9EAAA1;
  box-shadow: none;
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
.wrap button.secondary:hover,.btn.ghost:hover,.ghost:hover,.home button.ghost:hover,dialog button.secondary:hover,.ask-chips button:hover { color: var(--evergreen); background: var(--stone); }
.wrap button.small { min-height: 32px; padding: 5px 10px; }
.wrap input,.wrap select,.wrap textarea,dialog input,.ask-modal textarea {
  min-height: 40px;
  padding: 9px 11px;
  border: 1px solid #AEB6AE;
  border-radius: 4px;
  color: var(--graphite);
  background: #FFFFFF;
  box-shadow: none;
  font: 14px/1.45 'Artemis Sans', Inter, system-ui, sans-serif;
}
.wrap textarea,.ask-modal textarea { min-height: 90px; }
.wrap input:focus,.wrap select:focus,.wrap textarea:focus,dialog input:focus,.ask-modal textarea:focus { border-color: var(--forest); outline: 2px solid rgba(41,72,62,.18); outline-offset: 1px; }
.wrap input[type=checkbox],.wrap input[type=radio] { width: 17px; min-height: 17px; accent-color: var(--forest); }
.wrap label { margin: 11px 0 5px; color: var(--graphite); font-size: 13px; font-weight: 550; }
.wrap label.check { gap: 8px; color: var(--muted); font-weight: 400; }
.grid2,.grid3 { gap: 0 22px; }
.actions,.hero-actions { gap: 10px; }

/* Content-heavy views remain readable at narrow widths. */
.facts { gap: 6px 20px; }
.facts dt { color: var(--muted); }
.facts dd,.text,code { overflow-wrap: anywhere; }
table.plain { border-collapse: collapse; }
table.plain th,table.plain td { padding: 10px 9px; border-top: 1px solid var(--line); }
table.plain th { color: #46564E; font-size: 11px; letter-spacing: .06em; text-transform: uppercase; }
.history li,.app { border-color: var(--line); }
.decision { border: 1px solid var(--forest); background: #F1F3ED; }
.cols { gap: 18px; }
dialog {
  max-width: min(540px, calc(100vw - 28px));
  padding: 25px;
  border: 1px solid var(--line);
  border-radius: 7px;
  color: var(--graphite);
  background: var(--porcelain);
  box-shadow: 0 22px 60px rgba(20,45,41,.2);
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
}
dialog::backdrop { background: rgba(20,45,41,.48); }
dialog p { color: var(--graphite); }
dialog button { min-height: 38px; }
.ask-modal { width: min(540px, calc(100vw - 28px)); }
.ask-modal h2 { margin: 0 0 4px; font-size: 25px; }
.ask-chips { gap: 7px; }
.ask-chips button { min-height: 34px; padding: 6px 10px; border-radius: 3px; font-size: 12px; }
.ask-answer { padding: 13px; border: 1px solid var(--line); border-radius: 4px; background: #EFEEE8; }

/* Home brief: a single evergreen anchor with restrained paper detail. */
.home { max-width: 1360px; padding: 38px 40px 80px; }
.hero { padding-top: 8px; }
.hero h1 { max-width: 10em; margin: 13px 0 16px; color: var(--evergreen); font-size: clamp(40px,5vw,58px); font-weight: 400; line-height: 1.02; }
.hero .lede { max-width: 34rem; color: var(--muted); font-size: 16px; }
.metrics { gap: 12px; grid-template-columns:repeat(2,minmax(0,1fr)); }
.metric { min-width: 0; padding: 17px 16px; border-radius: 5px; }
.metric .m-icon { width: 34px; height: 34px; border: 1px solid #CFD7CE; border-radius: 4px; color: var(--forest); background: #E9EDE6; }
.metric strong { margin-top: 14px; color: var(--evergreen); font: 500 29px/1.1 'Artemis Display', Georgia, serif; }
.metric .m-label { color: var(--muted); font-size: 12px; }
.metric .m-arrow { color: var(--forest); }
.hero-row{grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:54px;align-items:start}.hero .brief-copy{margin:18px 0 14px}.hero .brief-action{margin:18px 0}.hero h1{max-width:12em}.hero-actions{margin-top:18px}
.document-preview{max-width:760px;margin:12px auto;padding:22px 28px;background:#fff;border:1px solid var(--line);overflow-wrap:anywhere}.document-preview h3{font-family:'Artemis Sans',sans-serif;font-size:22px}.document-preview h4{font-family:'Artemis Sans',sans-serif;font-size:12px;letter-spacing:.12em;text-transform:uppercase;border-bottom:1px solid var(--line);padding-bottom:8px;margin:24px 0 12px}.document-preview p,.document-preview li{color:var(--graphite);font-size:14px}.document-preview p{margin:7px 0}
.home-grid { gap: 22px; margin-top: 32px; }
.col-main,.col-side { gap: 20px; }
.featured { position: relative; overflow: hidden; padding: 30px 31px; color: var(--porcelain); background: var(--evergreen) !important; border-color: var(--evergreen); }
.featured h2,.featured h3,.featured p,.featured .f-company,.featured .f-meta,.featured .f-why,.featured .empty-note { color: #F7F6F1; }
.featured h2 { font-size: clamp(28px,3vw,36px); }
.featured .eyebrow { color: #C9D6C9; }
.featured .muted { color: #D2DDD2; }
.featured .f-art { display: none; }
.featured .f-why { max-width: 100%; }
.featured .btn { color: var(--evergreen); border-color: var(--porcelain); background: var(--porcelain); }
.featured .btn:hover { background: var(--stone); }
.featured .ghost,.featured button.ghost { color: var(--porcelain); border-color: #A6B8A7; background: transparent; }
.featured .ghost:hover,.featured button.ghost:hover { color: var(--evergreen); background: var(--sage); }
.featured .match-pill { color: #19372E; border-radius: 3px; background: #DCE6DB; }
.featured .match-pill::before { background: var(--forest); }
.board { padding: 20px 22px; }
.board h2 { font: 500 21px/1.25 'Artemis Display', Georgia, serif; }
.board h2 a { color: var(--forest); font: 600 12px/1.4 'Artemis Sans', Inter, sans-serif; }
.opp-row { gap: 12px; padding: 12px 0; border-radius: 0; }
.opp-row .co-mark { width: 36px; height: 36px; border-radius: 4px; color: var(--porcelain); background: var(--forest); font-family: 'Artemis Sans', Inter, sans-serif; }
.opp-row .o-title { color: var(--graphite); font-size: 13px; }
.opp-row .o-sub { font-size: 12px; }
.opp-row .o-fit { border-radius: 3px; color: var(--evergreen); background: #E5EDE4; }
.opp-row:hover { background: #F0EFE9; border-radius: 3px; }
.match-pill { border-radius: 3px; }
.bar .col { max-width: 54px; border-radius: 2px 2px 0 0; background: var(--sage); box-shadow: none; }
.todo li,.gap-row,.plan-actions li { border-color: var(--line); }
.todo .dot { border-radius: 1px; background: var(--forest); }
.todo .n,.gap-row .n { border-radius: 3px; color: var(--evergreen); background: #E5EDE4; }
.plan-meter { border-radius: 2px; background: var(--stone); }
.plan-meter i { border-radius: 2px; background: var(--forest); }
.r-card { padding: 21px 23px; }
.r-card h2 { font: 500 24px/1.25 'Artemis Display', Georgia, serif; }
.r-cols h3 { color: var(--forest); }

@media (max-width: 1080px) {
  .topnav { padding-right: 20px; padding-left: 20px; }
  .topnav nav.primary { gap: 15px; margin-left: 8px; }
  .home { padding-right: 26px; padding-left: 26px; }
  .metrics { grid-template-columns: repeat(2,minmax(0,1fr)); }
}
@media (max-width: 960px) {
  .topnav { min-height: 64px; flex-wrap: nowrap; gap: 10px; padding: 11px 16px; }
  .topnav nav.primary { display: none; }
  .navtoggle { display: block; }
  .nav-right { gap: 7px; }
  .ask-pill { min-width: 36px; width: 36px; justify-content: center; padding: 7px; }
  .ask-pill .ask-label { display: none; }
  .home { padding: 24px 18px 58px; }
  .hero-row,.home-grid { grid-template-columns: 1fr; gap:24px; }
  .document-preview{padding:16px 13px;}
  .hero { padding-top: 8px; }
  .hero h1 { max-width: 12em; font-size: clamp(40px,8vw,54px); }
  .featured h2 { font-size: 30px; }
  .featured .f-art { display: none; }
}
@media (max-width: 600px) {
  .topnav { padding-right: 12px; padding-left: 12px; }
  .brand { gap: 7px; }
  .brand .mark { width: 30px; height: 30px; }
  .brand .word { font-size: 11px; letter-spacing: .2em; }
  .nav-right { gap: 5px; }
  .ask-pill,.bell,.avatar { width: 32px; min-width: 32px; height: 32px; min-height: 32px; }
  .navtoggle summary { padding: 5px; }
  .wrap { padding: 28px 16px 54px; }
  .wrap h1 { font-size: 34px; }
  .wrap h2 { font-size: 19px; }
  .card { padding: 17px 16px; }
  .home { padding: 24px 15px 50px; }
  .hero h1 { font-size: 41px; }
  .metrics { gap: 8px; }
  .metric { padding: 14px 12px; }
  .metric strong { font-size: 25px; }
  .featured { padding: 23px 19px; }
  .featured .f-why { max-width: none; }
  .board { padding: 17px 15px; }
  .f-head { align-items: flex-start !important; flex-wrap: wrap; }
  .actions { align-items: stretch; }
  .actions .btn,.actions button { max-width: 100%; }
  .facts { grid-template-columns: 1fr; gap: 1px; }
  .facts dt { margin-top: 8px; font-size: 12px; }
  .facts dd { margin-bottom: 3px; }
  .cols,.grid2,.grid3,.r-cols,.plan-grid { grid-template-columns: 1fr; }
  .outcome { align-items: stretch; }
  .wrap .outcome select,.wrap .outcome input,.wrap .outcome input.oc-note,.wrap .outcome input.oc-contact { width: 100%; flex: 1 1 100%; }
  table.plain { display: block; max-width: 100%; overflow-x: auto; }
  table.plain th,table.plain td { padding: 8px 7px; }
  .history li { overflow-wrap: anywhere; }
}
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *,*::before,*::after { scroll-behavior: auto !important; transition: none !important; animation: none !important; }
}
"""

__all__ = ["EDITORIAL_CSS"]
