# Hallam — VELORA

Hallam is the repository for **Sabra Job Engine V1** — an outcome-driven job-search
operator with human control at the external application submission boundary.
Its web interface is **VELORA**, a Cloudglass Architectural design system
(documented in [`BRAND.md`](BRAND.md), assets in [`brand/`](brand/)).

The canonical build contract is [`SABRA_JOB_ENGINE_V1_BUILD.md`](SABRA_JOB_ENGINE_V1_BUILD.md).
Read it before coding. [`AGENTS.md`](AGENTS.md) carries the agent rules.

Sabra optimises for **interviews and desirable job outcomes**, never for applications
per day.

## Where it actually stands

Verified against real data, not scaffolding:

| Milestone | State | Evidence |
| --- | --- | --- |
| M1 Foundation | Done | Domain model, schema, state machines, event log |
| M2 Discovery | **Verified live** | 23 real ATS boards → **1,126 postings → 1,098 opportunities**; repeat run fully idempotent |
| M3 Intelligence | **Verified live** | 1,092 screened → 25 qualified → **10 shortlisted**, all genuine commercial roles |
| M4 Application factory | Done | Truth-bound tailoring, packages, answer memory |
| M5 Approval | Done | Approval bound to a content hash; refuses on mismatch |
| M6 Execution | Adapter built against a **real** captured form | Live Greenhouse forms are CAPTCHA-protected, so the adapter stops by design |
| M7 Ledger | Done | Append-only events, ledger projection, outcomes, learning records |
| M8 Pilot | **Blocked on owner input** | See below |

`apps/api` is a FastAPI app serving the VELORA interface, which covers the whole loop
from the browser. Every page shares one navigation bar: `/` (**Home**: "A clearer path
forward", real metrics, the featured opportunity, recent opportunities, your pipeline
progress, upcoming items, and **Run now**), `/opportunities` (cards with
pay, confidence and the biggest gap; Prepare and Pass), `/opportunities/{id}` (the
posting, why it scored as it did, the hard-filter decision, verified facts apart from
inferences, the application and the event history), `/applications` (batch approval; each
row links to its review page), `/packages/{id}` (the application review: the opening, fit,
résumé, cover letter, answers with their truth levels, unresolved questions, warnings and
how each artifact was written, then one **Approve application** bound to the content hash),
`/research` (the company research packets behind the shortlist) and `/career-plan` (your
active search as objectives, the recurring fit gaps, and the actions derived from them).
The account menu holds `/pipeline` (every application past approval by stage, with a
**Record outcome** control and its history), `/candidate` (your details, résumé upload,
the profile with its truth levels), `/searches` (define and switch search profiles),
`/answers` (record and verify) and `/settings` (the configuration, read only, plus
**Job boards**: enable or disable a board, or paste a careers URL to check it and add
it). The **Ask Velora** pill answers questions like "what requires my attention" from
real engine data. JSON routes sit beside them: health, status, home, today, candidate,
searches, runs, settings, sources, the shortlist, opportunity detail, package review and reject,
package preparation, the pipeline, the application ledger, outcomes, and the learning
dataset (`/api/learning-records`, JSON lines). On the command line, `sabra outcome`,
`sabra pipeline` and `sabra ledger export` cover the same ground. `make run-api` listens
on http://127.0.0.1:8787.
`apps/web` is still an empty scaffold.

### Honest gaps

- **M8 needs your real résumé.** The pipeline runs end to end today, but package
  generation correctly refuses to invent candidate facts: with no résumé loaded it
  produces `INCOMPLETE` packages with every open question named. That is requirement
  5 working, and it is the one thing only you can supply.
- Sensitive answers (salary, background check, and the same class of question)
  fill only after the owner records and verifies them. Until then the package
  stays incomplete.
- Every verified answer in answer memory (not only the default seven questions) is
  carried on each package and matched to form questions by key or by exact wording.
  A drop-down is set only by an exactly equal option; "Yes" never selects "Yes -
  I will require sponsorship". An unmatched required question blocks and is named.
- The uploaded résumé (and cover letter) is a PDF rendered from the approved
  Markdown at submit time (`reportlab`, in the `automation` extra), not Markdown.
- A BLOCKED or FAILED application that never reached the employer can be rebuilt
  with **Refresh package** (a revocation, so it must be approved again). One with a
  recorded submission or an unreported unconfirmed click cannot.
- Before a real submission the provider's `verify` is called; a closed posting expires
  the job and blocks the application without opening a browser. A failed check never
  expires a job.

## Quick start

```bash
make sync        # create the venv and install (required after adding a package)
uv run playwright install chromium  # local synthetic browser acceptance in the default suite
make init        # create the database, load the source registry, seed a search profile
make discover    # read every enabled board
make rank        # hard-filter, assess fit, rank, shortlist, research the shortlist
make test        # default suite; live employer checks stay opted out
make run-api     # local dashboard at http://127.0.0.1:8787
```

Your own search, instead of the built-in seed profile (copy and edit
[`config/search_profile.example.yaml`](config/search_profile.example.yaml); every field is
explained in it):

```bash
uv run sabra init --search-profile my_search.yaml   # load it and make it active
uv run sabra searches list                          # the active one is starred
uv run sabra searches load other.yaml               # add or replace another
uv run sabra searches activate SEARCH_ID
uv run sabra rank --rescore                         # re-filter and re-rank stored roles after editing a search
```

Beyond titles, pay and places, a search sets `locations.countries` (the countries a remote role may
be open to; default `US`) and `max_per_company` (the most roles from one company on the shortlist;
default 2). `sabra rank --rescore` applies a changed search to roles already stored; roles you
passed on, and roles with a prepared or approved package, are left alone.

The whole loop in one command, or from the browser's **Home** page (**Run now**):

```bash
uv run sabra run                  # discover, rank, research the shortlist
uv run sabra run --prepare        # ...and prepare packages for the top roles
```

A run never approves or submits anything. `sabra run --only-new` (and
`sabra discover --only-new`) skips boards that were read successfully in the last 20
hours (`--only-new-hours N` to change it), so a second run in a day costs a request per
stale board. `sabra status` shows each board's last success. To run it every morning on
Windows, see
[`scripts/schedule-windows.ps1`](scripts/schedule-windows.ps1) and the "Run it every
morning" section of [`docs/operations/windows-pilot.md`](docs/operations/windows-pilot.md).

### Job boards (providers)

Discovery reads public job-board APIs. The providers are **Greenhouse**, **Lever**,
**Ashby** and **direct careers pages** (schema.org JobPosting JSON-LD). Boards are listed
in [`config/sources.yaml`](config/sources.yaml) (set `SABRA_SOURCE_REGISTRY_PATH` to keep
your own copy); an entry may carry `max_items` to cap the postings read from that board.
Greenhouse and Lever roles are filled by the engine's adapters. A role on an Ashby board or
a company careers page cannot be: once you approve it, **Open in browser for you to fill**
opens the apply page, saves your tailored résumé and cover letter as PDFs and shows where,
types nothing, and waits for you to report the result.

```bash
uv run sabra sources list                       # boards: enabled, health, postings found
uv run sabra sources probe https://jobs.ashbyhq.com/siepe
uv run sabra sources probe siepe --kind ASHBY   # a bare token needs --kind
uv run sabra sources add https://jobs.ashbyhq.com/siepe --industry Fintech
uv run sabra sources disable ashby-siepe        # and: enable
```

`probe` reads the board once through the board's public API (never any other host) and
prints whether it is live, how many postings it has, how many mention a city from your
active search or "remote", and up to five titles that fit your search. `add` probes, refuses
a dead or duplicate board, and appends an entry to the configured registry file without
touching its comments. One slow or broken board is recorded on its row (health, last error)
and never fails the run; each board has its own time budget
(`SABRA_DISCOVERY_SOURCE_BUDGET_SECONDS`, default twice `SABRA_DISCOVERY_TIMEOUT_SECONDS`).

Reasoning-layer features are **off by default** (`SABRA_LLM_PROVIDER=none`) and every
one of them has a deterministic fallback, so the loop runs with no model configured.

## Layout

```text
apps/            FastAPI app and HTML dashboard under api/; web/ is an empty scaffold
packages/
  domain/        models, schema, state machines, repositories, event log
  llm/           provider abstraction (null provider by default)
  providers/     Greenhouse, Lever, Ashby, direct careers; normalization; deduplication
  scoring/       features, hard filter, fit engine, ranking
  documents/     master résumé, tailoring, answers, package assembly
  automation/    Playwright adapter contract, Greenhouse and Lever adapters
  engine/        services: discovery, intelligence, research, documents,
                 approval, execution, ledger, CLI
workers/         empty scaffold (discovery, research, and application workers)
config/          source registry, candidate profiles, example search profile
docs/            architecture, decisions, operations
migrations/      empty scaffold; the schema is created from the SQLAlchemy models
tests/           test suite and committed fixtures
```

## Non-negotiable invariants

- **Candidate truth.** Generated content may only assert what candidate data
  supports. `verify_tailored_truth` re-checks every generated line and is tested to
  fail on a fabricated bullet.
- **Approval before submission.** An approval carries the content hash of the package
  a human read. The execution path recomputes it and refuses on any difference.
- **Stop, don't guess.** A CAPTCHA or a failed challenge probe is not submitted
  and not solved: the pre-filled form is handed to a person in a headed browser, and
  the application waits (APPLYING) until the person reports an outcome or the page
  confirms. A closed or timed-out handoff stays APPLYING and requires a report;
  it cannot be retried automatically. An explicit abandoned report is BLOCKED and
  retryable, unless the opportunity has expired. Unknown required fields block that
  application and name the field.
  A proposed answer that cannot be written safely also blocks an optional control;
  an employer's preselected value cannot silently replace that answer.
  A submission is confirmed only by the employer's confirmation page, never by
  words in the posting. Dry-run is the default: it never opens an employer form,
  never types or uploads, and never submits.
- **Code owns truth.** IDs, deduplication, state transitions and authorization are
  deterministic; models assist judgement only.

## Documentation

- [`BRAND.md`](BRAND.md) — the VELORA Cloudglass brand system (canonical)
- [`docs/operations/source-registry-findings.md`](docs/operations/source-registry-findings.md)
- [`docs/operations/m6-greenhouse-form-findings.md`](docs/operations/m6-greenhouse-form-findings.md)
- [`docs/operations/windows-pilot.md`](docs/operations/windows-pilot.md)
- [`docs/architecture/assumptions.md`](docs/architecture/assumptions.md)
