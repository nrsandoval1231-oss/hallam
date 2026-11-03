"""Measured shell for the owner's selected ARTEMIS interface reference."""

REFERENCE_CSS = r"""
html {
  background:#F7F6F1
}
body {
  background:#F7F6F1;
  width:100%;
  max-width:none;
  margin:0 auto;
  min-height:100vh;
  box-shadow:0 0 72px #0d201d55;
  font-size:12px
}
body::before {
  content:none
}
body>.topnav,body>main {
  background:#F7F6F1
}
.topnav {
  min-height:49px;
  height:49px;
  padding:0 35px;
  gap:0;
  max-width:1008px;
  border-bottom:1px solid #deddd4
}
.brand {
  gap:11px
}
.brand .mark {
  width:30px;
  height:30px
}
.brand .word {
  font-size:11px;
  font-weight:500;
  letter-spacing:.36em;
  padding-left:0
}
.topnav nav.primary {
  display:flex;
  margin-left:50px;
  gap:27px;
  align-self:stretch
}
.topnav nav.primary a {
  font-size:9px;
  color:#5c615c;
  line-height:1;
  font-weight:500;
  border-bottom:0;
  position:relative
}
.topnav nav.primary a.active {
  font-weight:700;
  border-bottom:0
}
.topnav nav.primary a.active::after {
  content:'';
  height:1px;
  position:absolute;
  bottom:8px;
  left:0;
  right:0;
  background:#142D29
}
.nav-right {
  gap:19px
}
.ask-pill {
  border:0;
  background:none;
  box-shadow:none;
  width:24px;
  min-height:28px;
  padding:3px;
  justify-content:center
}
.ask-pill .ask-label {
  display:none
}
.ask-pill svg {
  width:14px;
  height:14px;
  stroke:#26312E
}
.ask-pill:hover {
  background:#E8E5DC
}
.avatar {
  border-radius:50%;
  width:25px;
  min-width:25px;
  height:25px;
  min-height:25px;
  font-size:9px;
  font-weight:500;
  background:#343d34;
  border:0;
  box-shadow:none
}
.bell {
  display:none
}
.navtoggle {
  display:none
}
.usermenu .menu {
  top:36px;
  font-size:12px
}
.usermenu .menu a {
  font-size:12px
}
.wrap {
  max-width:none;
  margin:0;
  min-height:calc(100vh - 49px);
  padding:30px 38px 48px
}
.wrap h1 {
  font-size:32px;
  line-height:1.12;
  margin:0 0 5px;
  font-weight:500
}
.wrap>p {
  font-size:11px;
  line-height:1.55
}
.wrap h2 {
  font-size:20px
}
.wrap h3 {
  font-family:'Artemis Sans',sans-serif;
  font-size:12px
}
.wrap .card {
  padding:19px 20px;
  border-color:#e7e4da;
  background:#faf9f4;
  border-radius:4px;
  box-shadow:none
}
.wrap .btn,.wrap button {
  font-size:11px;
  min-height:33px;
  border-radius:4px;
  padding:8px 15px;
  background:#142D29;
  border-color:#142D29
}
.wrap button.secondary {
  background:transparent;
  color:#26312E;
  border-color:#d5d6cc
}
.wrap input,.wrap select,.wrap textarea {
  font-size:12px;
  min-height:34px
}
.wrap label {
  font-size:11px
}
.pill {
  font-size:9px;
  padding:2px 7px;
  border:0;
  border-radius:4px
}
.line {
  font-size:11px
}
.muted {
  color:#687068
}
.wrap .studio-nav button img {
  vertical-align:middle;
  opacity:.75;
  margin-right:8px;
  flex:none
}
.wrap .studio-nav button {
  background:transparent;
  color:#687068;
  border:0;
  font-size:11px;
  font-weight:500;
  min-height:29px;
  padding:7px 10px
}
.wrap .studio-nav button[aria-current=true] {
  background:#E8E9E1;
  color:#142D29;
  font-weight:600
}
.wrap .studio-nav button:hover {
  background:#E8E9E1;
  color:#142D29
}
.reference-tabs {
  display:flex;
  gap:25px;
  align-items:center;
  border-bottom:1px solid #deddd4;
  margin:16px 0 20px
}
.reference-tabs button {
  position:relative;
  border:0;
  border-radius:0;
  background:none;
  color:#62695f;
  padding:8px 0 10px;
  min-height:30px;
  font-size:10px;
  font-weight:500
}
.reference-tabs button[aria-selected=true] {
  color:#142D29;
  font-weight:700
}
.reference-tabs button[aria-selected=true]::after {
  content:'';
  position:absolute;
  bottom:-1px;
  left:0;
  right:0;
  height:1px;
  background:#142D29
}
.reference-tabs button:hover {
  background:none;
  color:#142D29
}
.career-overview {
  display:grid;
  grid-template-columns:1fr 1.25fr;
  gap:20px
}
.career-overview .card {
  margin:0
}
.target-roles {
  list-style:none;
  padding:0;
  margin:0
}
.target-roles li {
  padding:13px 0;
  border-top:1px solid #e8e6dc;
  display:flex;
  justify-content:space-between;
  gap:10px;
  font-size:11px;
  font-weight:600
}
.target-roles li:first-child {
  border-top:0
}
.career-pref {
  display:flex;
  gap:13px;
  align-items:flex-start;
  margin:18px 0
}
.career-pref img {
  width:17px;
  height:17px;
  flex:none
}
.career-pref strong {
  display:block;
  font-size:11px;
  font-weight:600
}
.career-pref small {
  display:block;
  font-size:9px;
  color:#687068
}
.career-overview .card h2 {
  font-family:'Artemis Sans',sans-serif;
  font-size:12px;
  font-weight:700;
  margin-bottom:6px
}
.career-overview .card h3 {
  font-size:11px
}
.career-facts {
  margin:0;
  padding-left:18px
}
.career-facts li {
  padding:6px 0
}
.career-empty {
  padding:18px 0
}
.career-note {
  font-size:10px;
  color:#687068
}
.career-overview .pill {
  background:#e5ece1;
  font-weight:500
}
.plan-more {
  margin-top:24px
}
.wrap [role=tabpanel][hidden] {
  display:none
}
.reference-tabs:focus-within {
  outline:none
}
@media(max-width:1050px) {
  body {
    max-width:1008px
  }
  .topnav {
    padding-left:24px;
    padding-right:24px
  }
  .topnav nav.primary {
    margin-left:40px;
    gap:22px
  }
}
@media(max-width:760px) {
  body {
    width:100%;
    box-shadow:none;
    background:#F7F6F1
  }
  .topnav {
    height:55px;
    min-height:55px;
    padding:0 20px;
    gap:0
  }
  .brand {
    gap:9px
  }
  .brand .mark {
    width:25px;
    height:30px
  }
  .brand .word {
    font-size:10px;
    letter-spacing:.30em
  }
  .topnav nav.primary {
    display:none
  }
  .nav-right {
    gap:12px
  }
  .ask-pill,.usermenu {
    display:none
  }
  .navtoggle {
    display:block
  }
  .navtoggle summary {
    font-size:0;
    border:0;
    padding:8px 0;
    min-width:22px
  }
  .navtoggle summary img {
    display:block;
    width:18px;
    height:18px
  }
  .navtoggle .mobilemenu {
    right:0;
    top:35px;
    min-width:190px
  }
  .wrap {
    padding:25px 20px 38px;
    min-height:calc(100vh - 55px)
  }
  .wrap h1 {
    font-size:29px
  }
  .reference-tabs {
    gap:21px;
    overflow-x:auto;
    white-space:nowrap;
    margin-top:16px
  }
  .reference-tabs button {
    flex:none
  }
  .career-overview {
    grid-template-columns:1fr;
    gap:14px
  }
  .wrap .card {
    padding:17px
  }
  .career-pref {
    margin:15px 0
  }
  .career-pref strong {
    font-size:12px
  }
  .career-pref small {
    font-size:10px
  }
}
@media(max-width:760px) {
  .topnav {
    height:75px;
    min-height:75px;
    padding:0 29px
  }
  .brand {
    gap:16px
  }
  .brand .mark {
    width:46px;
    height:50px
  }
  .brand .word {
    font-size:16px;
    letter-spacing:.5em
  }
  .navtoggle summary {
    min-width:28px
  }
  .navtoggle summary img {
    width:28px;
    height:28px
  }
  .wrap {
    min-height:calc(100vh - 75px)
  }
}
@media(prefers-reduced-motion:reduce) {
  * {
    scroll-behavior:auto!important
  }
}
.save-icon {
  display:block;
  width:16px;
  height:16px;
  background:url(/brand/icons/bookmark.svg) center/contain no-repeat
}
/* Shared measurements from Reference B; branding follows Reference A. */
:root {
  --surface:#F7F6F1;
  --paper:#FBFAF6;
  --rule:#E5E2D9;
  --soft-ink:#626B63;
  --space-1:4px;
  --space-2:8px;
  --space-3:12px;
  --space-4:20px;
  --space-5:32px;
  --space-6:44px;
  --control-radius:4px;
  --canvas:1800px
}
body {
  max-width:none;
  box-shadow:none;
  background-attachment:scroll
}
.topnav {
  max-width:var(--canvas);
  width:100%;
  margin:0 auto
}
.home.home {
  width:100%;
  max-width:var(--canvas)
}
.topnav nav.primary a {
  font-size:10px
}
.brand .word {
  font-weight:500;
  letter-spacing:.4em
}
.wrap {
  max-width:var(--canvas);
  margin:0 auto;
  padding-top:32px
}
.wrap a.btn {
  border-radius:var(--control-radius)
}
.wrap input,.wrap select,.wrap textarea {
  background:var(--paper)
}
.home-hero {
  min-height:245px;
  padding:35px var(--space-6) 20px
}
.home-hero::before {
  background-position:center 40%;
  background-image:linear-gradient(90deg,#F7F6F1 0%,rgba(247,246,241,.94) 26%,rgba(247,246,241,.32) 51%,transparent 72%),url('/brand/images/artemis-architecture.webp')
}
.home-hero h1 {
  font-weight:500;
  letter-spacing:-.035em;
  font-size:40px;
  line-height:.96
}
.home-greeting {
  font-size:16px
}
.home-summary {
  max-width:340px;
  font-size:12px;
  line-height:1.35
}
.home-hero-actions {
  margin-top:14px;
  gap:17px
}
.home .brief-cta {
  padding:8px 22px;
  font-weight:500;
  box-shadow:0 3px 9px #142d2912
}
.listen-icon {
  box-shadow:0 2px 6px #142d2912
}
.hero-maxim {
  right:43px
}
.reference-metrics {
  gap:11px;
  margin:0 19px 16px;
  padding:5px;
  background:var(--surface);
  border-radius:5px
}
.reference-metric {
  background:var(--paper);
  border-color:var(--rule);
  padding:9px 12px;
  min-height:60px;
  gap:9px
}
.reference-metric-icon {
  width:35px;
  height:35px;
  border:1px solid #EAE7DF;
  border-radius:50%
}
.reference-metric-icon img {
  width:24px;
  height:24px
}
.reference-metric-value {
  font-size:23px
}
.reference-metric-label {
  font-size:10px
}
.home-panels {
  grid-template-columns:1.1fr .92fr 1.05fr;
  gap:10px;
  padding:0 11px
}
.home-panel {
  padding:8px 17px 14px;
  min-height:215px;
  background:var(--paper);
  border:0
}
.home-panel-heading {
  margin-bottom:10px
}
.home-panel-heading h2 {
  font-size:19px;
  white-space:nowrap
}
.home-panel-heading a {
  font-size:9px
}
.brief-date {
  font-size:7px;
  margin-left:4px;
  margin-right:auto
}
.brief-item {
  padding:10px 0;
  grid-template-columns:27px minmax(0,1fr) 15px;
  gap:9px
}
.brief-number {
  font-size:16px;
  border:0;
  background:var(--surface);
  width:25px;
  height:27px
}
.brief-item-title {
  font-size:10px
}
.brief-item-detail {
  font-size:9px
}
.home-opportunity-item {
  position:relative;
  border-top:1px solid var(--rule);
  padding-right:26px
}
.home-opportunity-item:first-child {
  border:0
}
.opportunity-row {
  grid-template-columns:33px minmax(0,1fr) auto;
  gap:10px;
  border:0;
  padding:10px 0
}
.opportunity-initials {
  width:31px;
  height:34px;
  border-radius:5px;
  font-size:12px
}
.opportunity-company {
  display:block;
  font-size:9px;
  line-height:1.4
}
.opportunity-title {
  font-size:10px;
  white-space:normal
}
.opportunity-sub {
  font-size:8px;
  white-space:normal
}
.opportunity-fit {
  font-size:8px;
  gap:5px
}
.opportunity-pay {
  font-size:9px;
  font-weight:650;
  color:var(--graphite);
  white-space:normal;
  text-align:right;
  max-width:110px
}
.opportunity-fit strong {
  font-size:8px
}
.home-opportunity-item>.save-opportunity {
  position:absolute;
  right:-8px;
  top:15px
}
.activity-item {
  border:0;
  padding:8px 0;
  grid-template-columns:20px minmax(0,1fr);
  gap:10px
}
.activity-title {
  font-size:10px
}
.activity-detail,.activity-time {
  font-size:9px
}
.activity-time {
  margin:2px 0 0
}
.home .mark-seen {
  font-size:9px
}
.home-operations {
  margin-top:18px
}
.wrap .save-opportunity,.home .save-opportunity {
  display:inline-grid;
  place-items:center;
  min-height:32px;
  min-width:32px;
  width:32px;
  padding:7px;
  border:0;
  border-radius:4px;
  background:transparent;
  color:var(--evergreen);
  cursor:pointer
}
.save-opportunity:hover {
  background:var(--stone)!important
}
.save-opportunity[aria-pressed=true] {
  background:#DEE8DB!important;
  box-shadow:inset 0 0 0 1px var(--sage)!important
}
.save-opportunity:disabled {
  opacity:.4
}
.opp-hero {
  position:relative;
  padding-right:42px
}
.detail-save {
  position:absolute;
  right:0;
  top:10px
}
.wrap .opp-title {
  font-size:30px!important
}
.opp-meta {
  font-size:11px
}
.company-tile {
  background:var(--evergreen);
  color:var(--porcelain);
  border:0
}
.opportunity-tabs [role=tab] {
  font-size:11px
}
.overview-grid {
  gap:24px
}
.key-insights li {
  padding:7px 0;
  border:0
}
.overview-grid .card {
  padding:0;
  border:0;
  background:transparent
}
.evidence-label {
  font-size:9px
}
.studio-readiness {
  border-radius:3px;
  font-size:11px
}
.studio-subtitle {
  font-size:12px
}
.document-preview {
  box-shadow:0 3px 12px #142d2908
}
.studio-layout {
  gap:20px
}
.career-overview .card h2 {
  font-size:12px
}
.career-overview {
  grid-template-columns:1fr 1.15fr
}
/* The shortlist is an editorial list rather than an operator dashboard. */
.back-overview {float:right;font-size:10px;padding:8px 0 8px 12px}
.opportunities-workspace>h1 {
  margin-bottom:2px
}
.opportunities-workspace>p {
  margin:0 0 16px
}
.opportunities-workspace .queue-summary {
  font-size:10px;
  color:var(--soft-ink);
  margin-bottom:20px
}
.opportunities-workspace .filters {
  display:block;
  margin:0 0 16px;
  padding:0;
  border:0;
  background:transparent
}
.opportunities-workspace .opportunity-search {
  display:flex;
  align-items:center;
  gap:8px;
  background:#EFEEE7;
  border:1px solid var(--rule);
  border-radius:4px;
  margin:0;
  padding:0 12px
}
.opportunities-workspace .opportunity-search input {
  background:transparent;
  border:0;
  padding:10px 0;
  min-height:38px;
  flex:1;
  width:100%;
  outline-offset:4px
}
.filter-chips {
  display:flex;
  gap:7px;
  flex-wrap:wrap;
  margin-top:12px
}
.wrap .filter-chips button {
  background:#EFEEE7;
  border:1px solid transparent;
  color:var(--graphite);
  border-radius:99px;
  padding:5px 13px;
  min-height:30px;
  font-size:10px;
  font-weight:500
}
.wrap .filter-chips button[aria-pressed=true] {
  background:var(--evergreen);
  color:var(--porcelain)
}
#advanced-filters {
  padding:14px 0 4px;
  display:flex;
  gap:8px;
  flex-wrap:wrap
}
#advanced-filters[hidden] {
  display:none
}
#advanced-filters input {
  flex:1 1 140px
}
.opportunities-workspace .queue {
  padding:0;
  border:0;
  background:transparent
}
.opportunities-workspace .queue>h2 {
  font-family:'Artemis Display',serif;
  font-size:19px;
  margin:0;
  padding:5px 0 12px;
  border-bottom:1px solid var(--rule)
}
.opportunities-workspace .row {
  border:0;
  border-bottom:1px solid var(--rule);
  border-radius:0;
  padding:15px 0;
  background:none;
  box-shadow:none
}
.opportunities-workspace .ranked-opportunity {
  display:grid;
  grid-template-columns:38px minmax(0,1fr) auto 32px;
  gap:14px;
  align-items:center
}
.ranked-copy {
  min-width:0
}
.ranked-copy>strong {
  display:block;
  font-size:13px;
  line-height:1.45
}
.ranked-company {
  display:block;
  font-size:10px;
  color:var(--soft-ink)
}
.rank-number {
  font-weight:400;
  color:var(--soft-ink)
}
.ranked-meta {
  display:block;
  font-size:10px;
  color:var(--soft-ink);
  margin-top:3px
}
.ranked-pay {
  display:grid;
  justify-items:end;
  gap:4px;
  max-width:190px;
  font-size:11px
}
.ranked-pay>strong {
  font-weight:550
}
.match-label {
  background:#E5ECE1;
  color:#304D3B;
  font-size:10px;
  border-radius:4px;
  padding:2px 7px
}
.confidence-label {
  font-size:9px;
  color:var(--soft-ink)
}
.opportunity-evidence {
  font-size:11px
}
.opportunity-evidence>summary {
  font-size:9px;
  color:var(--soft-ink);
  font-weight:400;
  margin:6px 0 0
}
.ranked-opportunity>.save-opportunity {
  grid-column:4;
  grid-row:1
}
.ranked-actions {
  grid-column:2/-1;
  display:flex;
  gap:8px
}
.wrap .ranked-actions button {
  font-size:10px;
  min-height:28px;
  padding:5px 10px
}
.ranked-opportunity .opportunity-initials {
  width:36px;
  height:39px;
  display:grid;
  place-items:center;
  color:var(--porcelain);
  background:var(--evergreen);
  border-radius:5px;
  font-size:12px;
  font-weight:600
}
.save-note {
  font-size:10px!important;
  color:var(--soft-ink)
}
@media(min-width:761px) and (max-width:1099px) {
  body {
    width:100%;
    max-width:none
  }
  .topnav {
    padding:0 24px
  }
  .topnav nav.primary {
    margin-left:30px;
    gap:20px
  }
  .topnav nav.primary a {
    font-size:10px
  }
  .home-panels {
    grid-template-columns:1.1fr 1fr
  }
  .home-panel:last-child {
    grid-column:1/-1
  }
  .activity-list {
    display:grid;
    grid-template-columns:repeat(3,minmax(0,1fr));
    gap:10px
  }
  .reference-metric {
    padding:9px 7px;
    gap:4px;
    grid-template-columns:35px minmax(0,1fr)
  }
  .reference-metric-tag {
    display:none
  }
  .reference-metric-label {
    white-space:normal
  }
  .brief-date {
    display:none
  }
}
@media(max-width:760px) {
  body {
    width:100%;
    max-width:none;
    background:var(--surface)
  }
  .topnav {
    height:75px;
    min-height:75px;
    padding:0 29px
  }
  .brand {
    gap:12px
  }
  .brand .mark {
    width:46px;
    height:50px
  }
  .brand .word {
    font-size:14px;
    letter-spacing:.4em
  }
  .navtoggle summary {
    display:grid;
    place-items:center;
    min-width:44px;
    min-height:44px
  }
  .navtoggle summary img {
    width:23px;
    height:23px
  }
  .navtoggle .mobilemenu {
    top:46px
  }
  .mobilemenu a,.mobilemenu button {
    min-height:44px
  }
  .wrap {
    padding:25px 24px 38px
  }
  .home-hero {
    padding:25px 29px 0;
    background:var(--surface)
  }
  .home-greeting {
    font-family:'Artemis Sans',sans-serif;
    font-size:13px;
    font-weight:400;
    margin:0 0 9px
  }
  .home-hero h1 {
    font-family:'Artemis Display',serif;
    font-size:44px;
    font-weight:500;
    line-height:1;
    letter-spacing:-.03em;
    max-width:100%;
    margin-bottom:0
  }
  .home-hero-image {
    height:130px;
    margin:27px 0 0;
    border-radius:0;
    background-position:70% 45%
  }
  .reference-metrics {
    display:grid;
    grid-template-columns:1fr;
    margin:0 29px 13px;
    padding:0;
    gap:0;
    background:none
  }
  .reference-metric {
    min-height:90px;
    grid-template-columns:50px minmax(0,1fr) auto;
    padding:9px 8px;
    gap:13px;
    background:none;
    border:0
  }
  .reference-metric-icon {
    width:34px;
    height:38px;
    border:0;
    border-radius:0
  }
  .reference-metric-icon img {
    width:30px;
    height:30px
  }
  .reference-metric-value {
    font-family:'Artemis Display',serif;
    font-size:29px;
    line-height:1;
    font-weight:500
  }
  .reference-metric-label {
    font-size:13px;
    line-height:1.4;
    white-space:normal
  }
  .reference-metric-tag {
    font-size:9px
  }
  .home .mobile-brief-cta {
    margin:0 29px 24px!important;
    min-height:55px!important;
    font-size:14px!important;
    font-weight:500!important;
    border-radius:5px!important
  }
  .home-panels {
    grid-template-columns:1fr;
    padding:0 20px;
    gap:12px
  }
  .home-panel {
    padding:18px 15px;
    min-height:180px
  }
  .home-panel-heading h2 {
    font-size:22px
  }
  .home-panel-heading a {
    min-height:30px;
    display:flex;
    align-items:center;
    font-size:10px
  }
  .brief-date {
    font-size:7px
  }
  .brief-item-title,.opportunity-title,.activity-title {
    font-size:12px
  }
  .brief-item-detail,.opportunity-sub,.activity-detail,.activity-time {
    font-size:10px
  }
  .brief-item-go {
    min-width:24px;
    min-height:32px;
    text-align:center
  }
  .opportunity-pay {
    font-size:10px;
    max-width:100px
  }
  .opportunity-company {
    font-size:10px
  }
  .opportunity-fit {
    max-width:100px;
    white-space:normal
  }
  .opportunity-fit strong {
    font-size:9px
  }
  .home-opportunity-item>.save-opportunity {
    right:-9px;
    top:13px
  }
  .wrap .save-opportunity,.home .save-opportunity {
    width:44px;
    height:44px;
    min-height:44px;
    min-width:44px;
    padding:13px
  }
  .home-opportunity-item {
    padding-right:34px
  }
  .opportunity-row {
    gap:8px
  }
  .home-operations {
    margin:18px 29px
  }
  .home-operations>summary {
    min-height:44px;
    font-size:11px
  }
  .opp-hero {
    padding-right:0
  }
  .detail-save {
    top:0;
    right:-10px
  }
  .opp-confidence {
    padding-right:38px
  }
  .wrap .opp-title {
    font-size:29px!important
  }
  .opportunity-tabs {
    gap:23px
  }
  .opportunity-tabs [role=tab],.studio-steps [role=tab] {
    min-height:44px
  }
  .studio-titlebar {
    flex-wrap:wrap
  }
  .studio-title {
    font-size:31px!important
  }
  .studio-layout {
    grid-template-columns:1fr
  }
  .studio-nav {
    gap:4px
  }
  .wrap .studio-nav button {
    min-height:40px;
    font-size:10px
  }
  .studio-readiness {
    flex-wrap:wrap;
    padding:7px 10px
  }
  .document-preview {
    padding:16px!important
  }
  .document-preview p,.document-preview li {
    font-size:13px
  }
  .career-overview {
    grid-template-columns:1fr
  }
  .wrap .filter-chips button {
    min-height:40px;
    padding:6px 12px
  }
  .opportunities-workspace .ranked-opportunity {
    grid-template-columns:36px minmax(0,1fr) 44px;
    gap:9px
  }
  .ranked-copy>strong {
    font-size:12px
  }
  .ranked-pay {
    grid-column:2;
    grid-row:2;
    justify-items:start;
    max-width:none;
    display:flex;
    flex-wrap:wrap;
    align-items:center;
    gap:6px
  }
  .ranked-opportunity>.save-opportunity {
    grid-row:1;
    grid-column:3
  }
  .ranked-actions {
    grid-column:2/-1
  }
  .ranked-meta {
    font-size:10px
  }
  .wrap .ranked-actions button {
    min-height:40px
  }
  .opportunities-workspace .row {
    padding:17px 0
  }
  .opportunities-workspace .opportunity-search input {
    min-height:44px
  }
  .opportunities-workspace .queue-summary {
    line-height:1.8
  }
  .reference-tabs button {
    min-height:44px
  }
}
@media(prefers-reduced-motion:reduce) {
  html {
    scroll-behavior:auto
  }
  *,*::before,*::after {
    animation:none!important;
    transition:none!important;
    scroll-behavior:auto!important
  }
}
.reference-metric-value {
  font-family:'Artemis Sans',sans-serif;
  font-size:18px;
  font-weight:500;
  line-height:1.2
}
@media(min-width:1100px){.reference-metric-value{font-size:26px}}
@media(max-width:760px) {
  .brand {
    gap:16px
  }
  .brand .word {
    font-size:16px;
    letter-spacing:.5em
  }
  .home-hero h1 {
    font-size:50px
  }
  .reference-metric {
    min-height:94px
  }
  .reference-metric-value {
    font-family:'Artemis Sans',sans-serif;
    font-size:23px;
    font-weight:500
  }
  .reference-metric-label {
    font-size:14px
  }
  .home .mobile-brief-cta {
    min-height:66px!important;
    border-radius:8px!important
  }
}
/* Latest owner-supplied single-screen target: 1280 × 853. */
.home-destinations {
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:17px 18px 0;
}
.destination-tile {
  position:relative;display:flex;flex-direction:column;justify-content:center;min-width:0;
  min-height:120px;padding:14px 32px;border:1px solid #fff8;border-radius:10px;
  color:var(--graphite);overflow:hidden;background-color:var(--stone);background-size:cover;background-position:center;
  box-shadow:0 4px 14px #5a45200a;text-decoration:none;
}
.destination-tile h2 {font:500 24px/1 'Artemis Display',serif;margin:7px 0 4px;color:inherit}
.destination-tile p {font:400 11px/1.3 'Artemis Sans',sans-serif;margin:0;color:inherit}
.destination-icon {width:27px;height:27px}
.destination-arrow {position:absolute;bottom:15px;right:17px;width:16px;height:16px;transform:rotate(-0deg)}
.destination-opportunities {background-image:url('/brand/images/artemis-destination-opportunities.webp');color:#F7F6F1}
.destination-research {background-image:url('/brand/images/artemis-destination-research.webp')}
.destination-plan {background-image:url('/brand/images/artemis-destination-plan.webp');color:#F7F6F1}
.destination-applications {background-image:url('/brand/images/artemis-destination-applications.webp')}
.destination-opportunities img,.destination-plan img {filter:brightness(0) invert(1)}
.destination-tile:hover {color:inherit;box-shadow:0 5px 16px #142d291a}
.destination-opportunities:hover,.destination-plan:hover {color:#F7F6F1}
.reference-metric-value,.brief-number {font-variant-numeric:lining-nums tabular-nums}
@media(max-width:1099px) {
  .home-destinations {grid-template-columns:repeat(2,minmax(0,1fr));margin:17px 20px 0}
}
@media(max-width:760px) {
  .home-hero-image {background-image:url('/brand/images/artemis-home-architecture.webp')}
  .home-destinations {grid-template-columns:1fr;gap:12px}
  .destination-tile {min-height:120px}
}
@media(min-width:1100px) {
  :root {--home-unit:clamp(.9375px,min(.078125vw,.117233vh),1.40625px)}
  .topnav {height:calc(50 * var(--home-unit));min-height:calc(50 * var(--home-unit));padding:0 calc(43 * var(--home-unit))}
  .brand {gap:calc(10 * var(--home-unit))}
  .brand .mark {width:calc(40 * var(--home-unit));height:calc(40 * var(--home-unit))}
  .brand .word {font-size:calc(19 * var(--home-unit));letter-spacing:.4em;font-weight:500}
  .topnav nav.primary {margin-left:calc(105 * var(--home-unit));gap:calc(33 * var(--home-unit))}
  .topnav nav.primary a {font-family:'Artemis Display',serif;font-size:calc(18 * var(--home-unit));font-weight:600;color:var(--graphite)}
  .topnav nav.primary a.active {font-weight:700}
  .topnav nav.primary a.active::after {bottom:calc(10 * var(--home-unit));height:2px}
  .nav-right {gap:calc(18 * var(--home-unit))}
  .ask-pill,.bell {width:calc(28 * var(--home-unit));min-width:0;min-height:calc(28 * var(--home-unit));height:calc(28 * var(--home-unit));padding:0}
  .ask-pill svg,.bell svg {width:calc(20 * var(--home-unit));height:calc(20 * var(--home-unit))}
  .bell {display:flex;background:none;border:0;box-shadow:none;backdrop-filter:none}
  .bell .badge {top:0;right:2px;min-width:6px;height:6px;padding:0;font-size:0;background:#9C4A38}
  .avatar {width:calc(32 * var(--home-unit));min-width:calc(32 * var(--home-unit));height:calc(32 * var(--home-unit));min-height:calc(32 * var(--home-unit));font-size:calc(11 * var(--home-unit));background:var(--evergreen)}
  .home.home {background:#F0E9E1}
  .home-cover {display:flow-root;background-image:linear-gradient(90deg,rgba(247,238,226,.88),rgba(247,238,226,.68) 28%,rgba(247,238,226,.10) 49%,transparent 65%),url('/brand/images/artemis-home-architecture.webp');background-size:cover;background-position:center}
  .home-hero {min-height:calc(305 * var(--home-unit));padding:calc(31 * var(--home-unit)) calc(78 * var(--home-unit)) calc(20 * var(--home-unit));background:transparent}
  .home-hero::before {content:none}
  .home-hero-copy {width:calc(440 * var(--home-unit))}
  .home-greeting {font:650 calc(11 * var(--home-unit))/1.4 'Artemis Sans',sans-serif;letter-spacing:.32em;text-transform:uppercase;margin:0 0 calc(14 * var(--home-unit))}
  .home-hero h1 {max-width:calc(460 * var(--home-unit));font-size:calc(54 * var(--home-unit));line-height:.91;letter-spacing:-.005em;font-weight:600;margin:0 0 calc(11 * var(--home-unit))}
  .home-summary {max-width:calc(355 * var(--home-unit));min-height:calc(56 * var(--home-unit));font-size:calc(14 * var(--home-unit));line-height:1.32;margin:0}
  .home-hero-actions {margin-top:calc(17 * var(--home-unit));gap:calc(23 * var(--home-unit))}
  .home .brief-cta {min-height:calc(42 * var(--home-unit));padding:calc(10 * var(--home-unit)) calc(30 * var(--home-unit));font:600 calc(14 * var(--home-unit))/1.2 'Artemis Display',serif;border-radius:8px;background:linear-gradient(#173a31,#0d231f);box-shadow:0 4px 8px #142d2912}
  .home .brief-cta img {width:calc(16 * var(--home-unit));height:calc(16 * var(--home-unit))}
  .listen-icon {width:calc(42 * var(--home-unit));height:calc(42 * var(--home-unit));background:#faf6eecc;border-color:#d5d0c6}
  .listen-copy {font-size:calc(11 * var(--home-unit))}
  .listen-copy small {font-size:calc(10 * var(--home-unit))}
  .hero-maxim {top:calc(88 * var(--home-unit));right:calc(52 * var(--home-unit));max-width:calc(92 * var(--home-unit));font-size:calc(10 * var(--home-unit));line-height:2.1;letter-spacing:.32em}
  .hero-maxim::after {width:calc(35 * var(--home-unit));margin-top:calc(18 * var(--home-unit))}
  .reference-metrics {grid-template-columns:1.06fr .96fr 1fr 1fr;gap:calc(12 * var(--home-unit));margin:0 calc(47 * var(--home-unit)) calc(20 * var(--home-unit));padding:0;background:none}
  .reference-metric {min-height:calc(71 * var(--home-unit));padding:calc(11 * var(--home-unit)) calc(18 * var(--home-unit));grid-template-columns:calc(40 * var(--home-unit)) minmax(0,1fr) auto;gap:calc(13 * var(--home-unit));border:1px solid #fff8;border-radius:8px;background:#faf6efdf;box-shadow:0 4px 14px #6f4e2310;backdrop-filter:blur(5px)}
  .reference-metric-icon {width:calc(40 * var(--home-unit));height:calc(40 * var(--home-unit));border-color:#fff5;background:#fff3}
  .reference-metric-icon img {width:calc(27 * var(--home-unit));height:calc(27 * var(--home-unit))}
  .reference-metric-value {font:600 calc(26 * var(--home-unit))/1 'Artemis Display',serif}
  .reference-metric-label {font-size:calc(10 * var(--home-unit));line-height:1.3;white-space:normal;color:#48504a}
  .reference-metric-tag {font-size:calc(9 * var(--home-unit));border-radius:5px;padding:4px 7px}
  .home-panels {width:100%;left:auto;transform:none;position:static;grid-template-columns:1.23fr 1.25fr 1fr;gap:calc(14 * var(--home-unit));padding:0 calc(18 * var(--home-unit));background:transparent}
  .home-panel {min-height:calc(262 * var(--home-unit));padding:calc(12 * var(--home-unit)) calc(20 * var(--home-unit)) calc(16 * var(--home-unit));border:1px solid #fff9;border-radius:10px;background:#fcf8f2e6;box-shadow:0 4px 16px #80623f0c}
  .home-panel-heading {margin-bottom:calc(12 * var(--home-unit));gap:8px}
  .home-panel-heading h2 {font-size:calc(24 * var(--home-unit));font-weight:600;line-height:1;white-space:nowrap}
  .home-panel-heading a {font-size:calc(10 * var(--home-unit));font-weight:650}
  .brief-date {font-size:calc(8 * var(--home-unit));letter-spacing:.2em;margin-left:4px;white-space:nowrap}
  .brief-item {padding:calc(10 * var(--home-unit)) 0;grid-template-columns:calc(34 * var(--home-unit)) minmax(0,1fr) 14px;gap:calc(14 * var(--home-unit));border-top:1px solid #e9e0d6}
  .brief-number {font:600 calc(20 * var(--home-unit))/1 'Artemis Display',serif;width:calc(34 * var(--home-unit));height:calc(34 * var(--home-unit));border:1px solid #f2e5d6;background:#f5ece0}
  .brief-item-title,.opportunity-title,.activity-title {font-size:calc(11 * var(--home-unit));line-height:1.35;font-weight:650}
  .brief-item-detail,.activity-detail,.activity-time {font-size:calc(9 * var(--home-unit));line-height:1.4}
  .home-opportunity-item {padding-right:calc(24 * var(--home-unit));border-color:#e9e0d6}
  .opportunity-row {grid-template-columns:calc(40 * var(--home-unit)) minmax(0,1fr) calc(145 * var(--home-unit));gap:calc(13 * var(--home-unit));padding:calc(11 * var(--home-unit)) 0}
  .opportunity-initials {width:calc(40 * var(--home-unit));height:calc(40 * var(--home-unit));font-size:calc(16 * var(--home-unit));border-radius:7px}
  .opportunity-company {font-size:calc(10 * var(--home-unit));font-weight:650}
  .opportunity-title {font-weight:500}
  .opportunity-sub {font-size:calc(9 * var(--home-unit))}
  .opportunity-fit {display:flex;flex-wrap:wrap;justify-content:flex-start;gap:5px;max-width:none;font-size:calc(8 * var(--home-unit))}
  .opportunity-pay {flex-basis:100%;font-size:calc(11 * var(--home-unit));text-align:left;max-width:none;margin-bottom:2px}
  .opportunity-fit strong,.opportunity-fit>span:not(.opportunity-pay) {font-size:calc(8 * var(--home-unit));padding:3px 5px;background:#e1ead9;border-radius:4px}
  .home-opportunity-item>.save-opportunity {top:calc(18 * var(--home-unit));right:-9px}
  .activity-item {grid-template-columns:calc(34 * var(--home-unit)) minmax(0,1fr);gap:calc(10 * var(--home-unit));padding:calc(5 * var(--home-unit)) 0}
  .activity-icon {width:calc(32 * var(--home-unit));height:calc(32 * var(--home-unit));border:1px solid #f0e4d4;background:#f5ece0;border-radius:50%}
  .activity-icon img {width:calc(17 * var(--home-unit));height:calc(17 * var(--home-unit))}
  .activity-detail,.activity-time {display:inline;margin-right:5px}
  .home-destinations {gap:calc(12 * var(--home-unit));margin:calc(17 * var(--home-unit)) calc(18 * var(--home-unit)) 0}
  .destination-tile {min-height:calc(120 * var(--home-unit));padding:calc(14 * var(--home-unit)) calc(32 * var(--home-unit))}
  .destination-tile h2 {font-size:calc(24 * var(--home-unit))}
  .destination-tile p {font-size:calc(11 * var(--home-unit))}
  .home-operations {margin:calc(15 * var(--home-unit)) calc(18 * var(--home-unit)) 0}
  .home-operations>summary,#status-line {font-size:10px}
  .wrap {padding:40px 56px 64px;font-size:14px}
  .wrap h1 {font-size:40px}
  .wrap>p,.wrap label {font-size:13px}
  .wrap h2 {font-size:25px}
  .wrap h3 {font-size:14px}
  .wrap .btn,.wrap button {min-height:40px;font-size:13px}
  .wrap input,.wrap select,.wrap textarea {min-height:40px;font-size:14px}
  .wrap .pill {font-size:11px}
  .reference-tabs button,.opportunity-tabs [role=tab] {font-size:12px}
  .career-overview .card h2,.career-pref strong,.target-roles li {font-size:14px}
  .career-pref small {font-size:12px}
  .wrap .studio-nav button {font-size:13px;min-height:38px}
  .ranked-copy>strong {font-size:15px}
  .ranked-company,.ranked-meta {font-size:12px}
  .ranked-pay {font-size:13px}
  .wrap .ranked-actions button {font-size:12px;min-height:34px}
  .opportunity-evidence>summary,.confidence-label {font-size:11px}
}
"""
