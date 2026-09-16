# Source registry findings

`config/sources.yaml` is the discovery source registry (requirement 7). Every entry
was verified live against the source's public API before it was added; each carries
`verified_jobs` (the posting count observed) and `verified_at`.

## How sources were found

Three probe rounds, ~600 candidate board tokens tested against the Greenhouse and
Lever public APIs (`scripts/_probe_boards*.py` — scratch tooling, not product code).
18 boards are live and enabled; 5 more were added in a later round, bringing the
registry to **23 enabled sources**.

Boards are addressed by their public JSON API and need no credentials:

- Greenhouse: `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
- Lever: `https://api.lever.co/v0/postings/{token}?mode=json`

## Live result

`sabra discover` reads all 23 boards: **1,126 postings → 1,098 canonical
opportunities** (28 duplicate postings collapsed). A repeat run is idempotent:
`jobs_new 0`, `jobs_unchanged 1126`.

## Tier 1: direct company career pages — nothing capturable

Requirement 7 lists "directly accessible company career pages" alongside Greenhouse
and Lever. `DirectCareersProvider` is implemented and covered by tests, reading
`schema.org/JobPosting` JSON-LD.

**No seed-vertical employer currently exposes it.** Eight employers' careers pages
were checked: none served a `JobPosting` JSON-LD block — every one routes to a
Greenhouse or Lever board already in this registry. A broader probe (Ashby, Workable,
SmartRecruiters) found pages that are JavaScript shells at fetch time.

The finding is recorded rather than worked around: the registry keeps one
`DIRECT_CAREERS` entry, **disabled**, documenting the negative result. A source that
publishes nothing capturable has not earned health by being routed around. The
provider stays tested so the capability is real if a source appears.

## Operating notes

- **Greenhouse reports its true board size** in `meta.total`. The provider records
  it, so a truncated capture is visible instead of silent.
- **Lever returns the whole board in one response**, so its reported total is simply
  the payload length; it has no `meta.total`.
- Adding a source is a YAML edit plus a verification date. No release required.
- A board that stops publishing shows up as a stale `verified_at` and as
  `sources_failed` on the next run, rather than as an unexplained drop in results.
- Provider health (`HEALTHY`/`DEGRADED`/`FAILING`) and `consecutive_failures` are
  tracked per source in the `providers` table.
