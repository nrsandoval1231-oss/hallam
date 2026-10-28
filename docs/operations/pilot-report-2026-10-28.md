# Sabra Job Engine V1 — Pilot Report

**Date:** 2026-10-28
**Database:** `C:\Users\nrsan\hallam-private\var\sabra.db` (candidate: Sabra Sandoval)
**Build:** `main` @ 63379fc (442 tests passing, ruff clean)

## §49 metrics

| Metric | Value |
| --- | --- |
| Jobs discovered | 15,842 postings across 112 enabled sources (Greenhouse, Lever, Ashby) |
| Sources queried | 112 enabled; 111 healthy, 1 failing (`greenhouse-skinlaundry` board retired, HTTP 404) |
| Duplicates removed | 1,212 postings merged into canonical opportunities (14,690 canonical opportunities; 102 multi-source merges) |
| Hard-filtered jobs | 14,567 rejected with deterministic reasons (geography 859, outside search intent 1,205, excluded title 347, closed 68, employment type 60, compensation 31, travel 26) |
| Qualified jobs | 112 survived hard filtering and received a fit assessment |
| Shortlisted jobs | 15 in the reviewed shortlist pool |
| Applications prepared | 72 package revisions built; 9 applications currently COMPLETE and AWAITING_APPROVAL |
| Applications approved | 1 led to a verified live submission (earlier approvals were revoked by package refresh and await re-approval by design) |
| Applications attempted | 7 attempts logged (1 live, 6 dry-run validations) |
| Applications successfully submitted | **1 — confirmed by the employer's own `/confirmation` page** (Affirm, Greenhouse job 7850544003, 2026-10-28 14:53 UTC) |
| Failures | 2 handoffs abandoned by the human during an earlier build (later refreshed and rebuilt); 0 unhandled automation failures; 0 fabricated submissions |
| Unknown candidate fields encountered | 0 — every package completed with no unresolved questions; the truth boundary held (name/email required owner verification before it would type) |
| Outcome events captured | 0 so far; employer responses for the submitted application are pending and tracked manually per §24 |

## Real prepared application packages (§49: at least three)

Nine COMPLETE packages are live in the queue at the time of this report, each carrying the tailored résumé, cover letter, candidate summary, fit explanation, 16 verified application answers, and owner-verified employment/education history:

1. **Affirm** — (submitted, confirmed 2026-10-28; package revision bound to the approval hash is in the ledger)
2. **Prophet Homes** — Transaction Coordinator
3. **Zone 5 Technologies** — Office Manager - DFW
4. **The Jarrell Company** — Administrative Assistant Specialist
5. **Lincoln Property Company** — Administrative Assistant
6. **The Jarrell Company** — Sales Support
7. **Toast** — Bookkeeper (Founding Member)
8. **Holder Construction** — Administrative Assistant Field
9. **Wilson Elser** — Legal Administrative Assistant
10. **Samsara** — Account Executive, Mid Market (Midwest) (prepared by the scheduled pipeline during this pilot)

## Verification evidence

- Live submission end-to-end: approval (hash-bound) → posting-live check → PDF résumé rendered and bound to the approval hash → form filled → submit clicked → **employer confirmation page observed** (`confirmation_detected: true`). No CAPTCHA encountered on this form.
- Human boundary exercised: two CAPTCHA handoffs occurred under an earlier build and were reported `abandoned` by the person; the ledger recorded them as human decisions, not system failures.
- Daily operator loop (`scripts/daily_run.py`) verified live: discovery pass (82s, idempotent), re-rank, and package preparation for the new #1 opportunity.

## Known limits (by design, §42)

- Ashby boards are discover/normalize only; the first application adapter is Greenhouse, Lever adapter implemented, Ashby adapter not yet.
- Only the first employment and first education form blocks are filled; a second required block blocks safely and names the field.
- Outcome tracking is manual (§24); email integration deliberately deferred.
- `greenhouse-skinlaundry` source is stale (board retired); recorded as a source error, not silently dropped.
