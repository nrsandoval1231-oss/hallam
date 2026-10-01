# Hallam

Hallam is the repository for **Sabra Job Engine V1** — an outcome-driven job-search
operator with human control at the external application submission boundary.

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

`apps/api` is a FastAPI app with an HTML dashboard. It serves `/`, `/opportunities`,
`/applications` (batch approval), and `/answers` (record and verify), plus JSON
routes for health, status, the candidate profile, résumé upload, the shortlist,
and package preparation. `make run-api` listens on http://127.0.0.1:8787.
`apps/web` is still an empty scaffold.

### Honest gaps

- **M8 needs your real résumé.** The pipeline runs end to end today, but package
  generation correctly refuses to invent candidate facts: with no résumé loaded it
  produces `INCOMPLETE` packages with every open question named. That is requirement
  5 working, and it is the one thing only you can supply.
- Sensitive answers (salary, background check, and the same class of question)
  fill only after the owner records and verifies them. Until then the package
  stays incomplete.

## Quick start

```bash
make sync        # create the venv and install (required after adding a package)
make init        # create the database, load the source registry, seed a search profile
make discover    # read every enabled board
make rank        # hard-filter, assess fit, rank, shortlist
make test        # default suite; live employer checks stay opted out
make run-api     # local dashboard at http://127.0.0.1:8787
```

Reasoning-layer features are **off by default** (`SABRA_LLM_PROVIDER=none`) and every
one of them has a deterministic fallback, so the loop runs with no model configured.

## Layout

```text
apps/            FastAPI app and HTML dashboard under api/; web/ is an empty scaffold
packages/
  domain/        models, schema, state machines, repositories, event log
  llm/           provider abstraction (null provider by default)
  providers/     Greenhouse, Lever, direct careers; normalization; deduplication
  scoring/       features, hard filter, fit engine, ranking
  documents/     master résumé, tailoring, answers, package assembly
  automation/    Playwright adapter contract, Greenhouse and Lever adapters
  engine/        services: discovery, intelligence, research, documents,
                 approval, execution, ledger, CLI
workers/         empty scaffold (discovery, research, and application workers)
config/          source registry and candidate profiles
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
  and not solved: the pre-filled form is handed to a person in a headed browser.
  Unknown required fields block that application and name the field. Dry-run is
  the default: it never opens an employer form, never types or uploads, and never submits.
- **Code owns truth.** IDs, deduplication, state transitions and authorization are
  deterministic; models assist judgement only.

## Documentation

- [`docs/operations/source-registry-findings.md`](docs/operations/source-registry-findings.md)
- [`docs/operations/m6-greenhouse-form-findings.md`](docs/operations/m6-greenhouse-form-findings.md)
- [`docs/operations/windows-pilot.md`](docs/operations/windows-pilot.md)
- [`docs/architecture/assumptions.md`](docs/architecture/assumptions.md)
