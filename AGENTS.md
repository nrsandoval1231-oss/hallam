# ARTEMIS Agent Rules

ARTEMIS is the sole approved customer-facing brand. Treat [BRAND.md](BRAND.md) as canonical. Do not restore earlier naming, design tokens or logos. Keep internal `sabra` package names, database identifiers and CLI commands for compatibility. Do not change application safeguards for cosmetic work.

Read `SABRA_JOB_ENGINE_V1_BUILD.md` before coding. Treat it as the canonical V1 build contract.

Inspect the repository before making changes. Work autonomously through:

**Inspect → Plan → Implement → Verify → Diagnose → Repair → Re-verify → Continue**

Preserve candidate truth, explicit human approval before external submission, auditable state transitions, and the 50 numbered requirements in the build contract. Do not stop at scaffolding or report recoverable failures without repairing and re-verifying them.

The human boundary is batch approval of COMPLETE packages, each approval bound to that package's content hash, plus human-only solving of a CAPTCHA or bot challenge. After a hash-bound approval, a form with no challenge may be submitted. A challenge is never solved, bypassed, or clicked through: the pre-filled form opens in a headed browser and the person submits it. Dry-run is the default. It never opens an employer form, never types or uploads, and never submits. Unverified candidate facts, including sensitive answers that have been recorded but not verified, are not typed onto a form.
