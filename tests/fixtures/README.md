# Test fixtures

Everything in this directory is test data. Two kinds live here, and the
distinction matters:

## Captured from a live source

These are byte-real payloads captured from public APIs and committed so provider
tests are reproducible offline and assert against real shapes rather than invented
ones. They were captured by `scripts/_capture_fixtures.py`.

- `greenhouse_revance_board.json` — 15 postings from the Revance Therapeutic
  Greenhouse board, plus the board's reported total (`meta.total`).
- `greenhouse_revance_job_detail.json` — one posting's detail payload, including the
  board's application questions.
- `lever_elfbeauty_board.json` — 15 postings from the e.l.f. Beauty Lever board.
  Lever returns the whole board in one response; the fixture is bounded by taking
  the first 15 *postings* rather than by slicing serialized bytes, because a byte
  slice of a JSON document is not JSON.

## Synthetic

- `master_resume_sample.txt` — **a fictional candidate. This is not a real person
  and describes nobody.** It exists so the résumé parser and the truth-boundary
  checks have a realistic document to work against: multi-line contact block,
  three roles with dated headers, bullets with figures, skills, certifications and
  a project. It is deliberately *not* prefixed with a disclaimer banner, because a
  banner would be parsed as the candidate's name and would make the parser look
  broken when it is not.
- `direct_careers_jsonld_synthetic.json` — a hand-built `schema.org/JobPosting`
  page fragment. No live source in the seed vertical publishes capturable JSON-LD
  (their careers pages route to a Greenhouse or Lever board), so this exercises the
  parser against the documented shape. It is not evidence that such a source exists.
