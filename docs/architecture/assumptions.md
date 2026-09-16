# Assumptions and decisions register

Requirement 45 says to document assumptions and continue. This is that record, plus
the decisions that were not obvious and the defects that only real data exposed.

## Decisions taken under ambiguity

Each is an implementation refinement, not a product-direction change. Where the PRD
and reality disagreed, reality won and the reason is stated.

| # | Assumption | Why |
| --- | --- | --- |
| A1 | **SQLite is the pilot runtime; PostgreSQL is the documented deployment target.** | The PRD names PostgreSQL, but no database service is available on the owner's machine (Docker Desktop is not installed; only the CLI is on PATH). Rather than block, the schema is dialect-agnostic — portable types only, no `JSONB` or dialect-specific SQL — so the same models and queries run on both. Set `SABRA_DATABASE_URL` to a `postgresql+psycopg://` URL to move. **The PostgreSQL path has not been executed**, so it is NOT TESTED, not working. |
| A2 | **Discovery providers are synchronous.** | The deterministic core stays synchronous and the API will run it in a worker thread. Easier to test, restart and reason about than a web of coroutines; nothing in V1 needs concurrency beyond parallelism across sources, which is handled by running them in sequence within one run. |
| A3 | **Salary comparisons are annual-only.** | `salary_period` was added to the canonical job because comparing a posted `$35/hour` against a `$75,000` annual floor is a real correctness bug. Hourly figures are recorded and never annualised — inventing an annual equivalent would present a guess as a fact about the posting. |
| A4 | **`applications.opportunity_id` is UNIQUE.** | Requirement 9 forbids duplicate applications. A constraint enforces it; a check at one call site would not. |
| A5 | **`opportunities.search_profile_id` uses `""`, never NULL.** | SQL treats NULLs as distinct in a UNIQUE constraint, so a nullable column would have silently permitted two opportunities for the same posting. |
| A6 | **One application per opportunity, one opportunity per (candidate, search profile).** | Fine for the single-user V1. A multi-candidate deployment needs `jobs` to stop carrying a single `opportunity_id`; noted in `models.py`. |
| A7 | **Tailoring reorders and selects; it never paraphrases.** | "Improve wording" (requirement 17) is confined to whitespace, capitalisation and bullet punctuation. A rewriter could introduce a claim the candidate never made, and no test could reliably tell. |
| A8 | **A role is never deleted from a tailored résumé.** | Dropping a job is a misstatement by omission. Irrelevant *bullets* are dropped; every role keeps at least one, verbatim. |
| A9 | **The seed search profile accepts all of Texas, not only DFW.** | Requirement 6 says "Dallas-Fort Worth — remote acceptable". Field-sales territories routinely cover the state, so `TX` is accepted while DFW scores higher (see D3). Narrowing this is one line in `candidate_intake.py`. |
| A10 | **`SABRA_AUTOMATION_DRY_RUN=true` is a floor, not a default.** | A real submission needs both the global switch off *and* a per-call opt-in. An operator's script cannot accidentally submit. |

## Defects that only real data found

Every one of these passed the test suite at the time and was caught by running the
pipeline against live boards.

| # | Defect | Consequence if shipped |
| --- | --- | --- |
| D1 | `normalize_title` used an ASCII-only character class. | Every CJK title collapsed to `""`, so all Japanese postings at one employer deduplicated into **one** opportunity. |
| D2 | Deduplication merged on exact title + location alone. | Two "Clinical Field Specialist" roles in San Diego described *different territories* — two real openings collapsed into one, hiding an opportunity and blocking a second application. Now requires description corroboration. |
| D3 | Geography compared raw tokens. | A posting reading `"Texas"` never matched a profile reading `"TX"`. Also fixed the scoring: every accepted location scored identically, so a Dallas role could not outrank a Houston one. |
| D4 | `extract_candidate_features` built `skill_tokens` and never merged them into `evidence_tokens`. | A candidate whose profile is largely a skills list scored **zero** qualification coverage against postings plainly asking for those skills. |
| D5 | Question n-grams included function words. | `"Director of Sales"` contributed the gram `"director of"`, which matched `"Associate Director of Bioinformatics"` — an unrelated role reached the shortlist. |
| D6 | Multi-word role families were split into tokens. | `"business development"` contributed the bare token `"business"`, admitting `"Director, People Business Partner"` (an HR role). |
| D7 | The captcha-detection selector list was hand-quoted into JavaScript. | `["iframe[src*="recaptcha"]"...]` is a JS syntax error that crashed inspection. Caught by driving a **real** browser, not by a stub. |
| D8 | The submission path moved `APPROVED → APPLIED` directly. | The state machine forbids it. A **successful** submission would have raised *after* the employer received the application, leaving it unrecorded. Now every state move derives its route from the transition graph (`application_path`). |
| D9 | The employment-block date inputs are labelled "Start date month". | The question catalogue classified them as the *"when can you start?"* question, so every application would have been blocked by a question the form never asked. Structural identity now wins over label classification. |

## Deliberate non-features

- **No mass application.** Ten excellent applications beat five hundred irrelevant
  ones, so the hard filter is allowed to be decisive and the shortlist is capped at 10.
- **No CAPTCHA evasion.** The live Greenhouse form is protected. Requirement 21 says
  stop and ask a human; it does not say find a way round, and defeating a control the
  employer installed on purpose is not this product's decision.
- **No fabricated research.** There is no "recent company developments" value unless a
  source said something. An empty field with a stated reason beats a plausible
  sentence.
- **No learned ranking model yet** (requirement 13), but every feature the heuristic
  uses is persisted on each assessment, so a learned ranker can replace it without
  losing history.
