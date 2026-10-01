# Sabra Job Engine
## V1 Canonical PRD & Autonomous Build Directive

## Agent Execution Header

This file, `SABRA_JOB_ENGINE_V1_BUILD.md`, is the canonical build contract for Sabra Job Engine V1. Place it in the repository root and read it in full before coding. Preserve the 50 numbered requirements and the product doctrine below.

Inspect the repository and existing implementation first, then autonomously execute:

**Inspect → Plan → Implement → Verify → Diagnose → Repair → Re-verify → Continue**

Work through milestones M1–M8 toward the real-data V1 success condition. Diagnose and repair recoverable failures, repeat relevant verification, and continue; scaffolding, a rendered UI, or passing tests alone do not establish completion. Follow the decision hierarchy in requirement 45, document assumptions, and bring product-direction changes to the owner.

Build authority does not authorize external application submission on its own: preserve candidate truth, bind explicit user approval to the specific application package, and stop for human intervention where requirements 19–21 demand it. The human boundary is batch approval of complete packages plus human-only CAPTCHA solving. After that hash-bound approval, a supported form with no challenge may be submitted; a challenge is handed to the person in a headed browser and is never solved by the product. Optimize for interviews and desirable job outcomes, never application volume.

Sabra Job Engine is a standalone product/repository. Athena-style learning and possible Nucleus integration are later capabilities, not V1 dependencies; collect trustworthy outcome evidence from day one.

---

**Status:** BUILD AUTHORIZED  
**Product:** Sabra Job Engine  
**Parent:** SabrAthena  
**Version:** V1  
**Operating principle:** Quality applications at machine speed, with human control at the irreversible boundary.

---

## 1. Mission

Build Sabra Job Engine into an autonomous job-search and application system that can take a candidate from:

**“I need a job.”**

to:

**“Here are the highest-value opportunities I found, why they fit you, and three complete applications ready for your approval.”**

Sabra is not primarily a job board.

Sabra is a **job-search operator**.

Its canonical operating loop is:

**Candidate → Discover → Normalize → Score → Research → Tailor → Prepare → Approve → Apply → Track → Learn**

The system should eventually require very little repetitive work from the candidate while preserving human authority over consequential actions.

---

## 2. V1 Success Condition

V1 is successful when a user can:

1. Create a candidate profile.
2. Upload a master résumé.
3. Define what kind of job they want.
4. Start a search.
5. Leave Sabra running.
6. Return to a ranked opportunity queue.
7. Understand why each opportunity was selected.
8. Open the strongest opportunities.
9. Review tailored application materials.
10. Approve an application.
11. Allow Sabra to execute supported portions of the application.
12. Track what happened afterward.

The primary V1 acceptance test is:

> Starting from a candidate profile and search criteria, Sabra independently discovers at least 50 legitimate current openings, normalizes and deduplicates them, ranks them, identifies approximately 10 high-value opportunities, and prepares complete application packages for at least the top three without inventing candidate information.

This is more important than feature count.

---

## 3. Product Doctrine

Sabra optimizes for:

**Interviews and desirable job outcomes.**

It does NOT optimize for:

**Applications per day.**

Do not turn Sabra into a mass-application spam bot.

Ten excellent applications are more valuable than 500 irrelevant ones.

Every major architectural decision should support this principle.

---

## 4. Candidate Intelligence

Create a canonical `CandidateProfile`.

It should support structured representation of:

- identity/contact information
- employment history
- education
- skills
- certifications
- accomplishments
- industries
- management experience
- technical capabilities
- portfolio/projects
- compensation history where voluntarily supplied
- desired compensation
- preferred titles
- acceptable titles
- excluded titles
- preferred industries
- excluded industries
- geography
- relocation willingness
- remote/hybrid/on-site preference
- travel tolerance
- seniority preference
- employment type
- sponsorship/work authorization where relevant
- hard constraints
- soft preferences

The résumé is evidence for the candidate model.

It is not the candidate model itself.

---

## 5. Candidate Truth Boundary

Sabra must never manufacture qualifications.

Generated application content must distinguish among:

**VERIFIED** — explicitly supported by candidate information.

**INFERRED** — reasonably inferred but not explicitly stated.

**UNKNOWN** — insufficient evidence.

UNKNOWN information must not silently become factual application content.

If an application requires an unknown consequential answer, Sabra should request candidate input or flag it for review.

---

## 6. Search Intent

Create a canonical `SearchProfile`.

Example:

```text
Target roles:
- Regional Sales Manager
- Business Development Manager
- Territory Manager
- Account Executive

Industries:
- Medical aesthetics
- Skincare
- Medical devices
- Beauty technology

Compensation:
- $75,000 minimum target
- prefer $100,000+

Location:
- Dallas-Fort Worth
- remote acceptable

Travel:
- <= 40%

Exclude:
- commission-only
- entry-level
- MLM
- 1099-only
```

Users may maintain multiple SearchProfiles.

---

## 7. Job Discovery

Implement a provider architecture.

Each provider implements a common discovery interface.

Initial priority:

**Tier 1**

- Greenhouse
- Lever
- directly accessible company career pages

Design for later providers without implementing all of them immediately.

Potential future sources include additional ATS systems, job aggregators and company career platforms.

Do not tightly couple the product to any single source.

---

## 8. Canonical Job Object

Normalize discovered openings into a canonical `Job`.

Minimum fields:

```text
id
source
source_job_id
source_url
company
company_domain
title
location
remote_status
employment_type
salary_min
salary_max
salary_currency
description
requirements
preferred_qualifications
responsibilities
travel
department
date_posted
date_discovered
last_verified
status
raw_source_hash
```

Preserve the raw source and provenance.

---

## 9. Deduplication

The same opening may appear through multiple sources.

Create deterministic deduplication using signals such as:

- company
- title
- location
- source IDs
- canonical URL
- description similarity
- posting date

Represent one real-world opening as one canonical opportunity whenever possible.

Do not allow duplicate applications.

---

## 10. Job Lifecycle

Use explicit job states:

```text
DISCOVERED
NORMALIZED
REJECTED
QUALIFIED
SHORTLISTED
RESEARCHED
PREPARED
AWAITING_APPROVAL
APPROVED
APPLYING
APPLIED
FAILED
RESPONSE
SCREEN
INTERVIEW
OFFER
REJECTED_BY_EMPLOYER
WITHDRAWN
EXPIRED
```

Transitions must be auditable.

---

## 11. Hard Filtering

Apply deterministic constraints before expensive AI analysis.

Examples:

- compensation below minimum
- prohibited geography
- incompatible employment type
- excluded title
- excluded company
- mandatory qualification clearly absent
- excessive travel
- role no longer available

Rejected opportunities should remain in history with a rejection reason.

---

## 12. Fit Engine

Jobs surviving deterministic filtering proceed to semantic evaluation.

Produce a structured assessment including:

```text
fit_score
career_score
qualification_score
compensation_score
location_score
industry_score
trajectory_score
confidence
strengths[]
gaps[]
risks[]
why_this_job
```

Scores must be explainable.

Never present an unexplained magic number.

---

## 13. Opportunity Ranking

Ranking should incorporate:

**Candidate fit × job desirability × confidence × probability of meaningful outcome**

V1 can use explicit weighted logic.

Do not build a sophisticated learned ranking model yet.

Preserve the underlying features so outcome learning can replace or augment the heuristic later.

---

## 14. Opportunity Research

For highly ranked jobs, gather additional information when available:

- company description
- company website
- industry
- relevant products/services
- role context
- likely reporting organization
- recent relevant company developments
- hiring context
- useful candidate/company intersections

Separate verified facts from inference.

---

## 15. Application Package

Create an `ApplicationPackage` for selected opportunities.

It may contain:

- tailored résumé
- cover letter
- application answers
- candidate summary
- job-fit explanation
- qualification gaps
- recruiter message
- hiring-manager message
- interview preparation notes

Not every job requires every artifact.

---

## 16. Résumé Architecture

Maintain one canonical master résumé.

Do not repeatedly mutate previous tailored résumés.

Every tailored résumé should be generated from:

**CandidateProfile + MasterResume + Job**

This prevents résumé drift.

Preserve a versioned history.

---

## 17. Résumé Tailoring

Tailoring may:

- reorder truthful experience
- emphasize relevant accomplishments
- change summary language
- prioritize relevant skills
- improve wording
- align terminology with the opportunity
- remove irrelevant material

Tailoring may NOT:

- invent employment
- invent metrics
- invent certifications
- invent responsibilities
- invent software expertise
- inflate titles
- fabricate dates

---

## 18. Application Question Memory

Create reusable structured candidate answers for common questions.

Examples:

- work authorization
- sponsorship
- relocation
- travel
- salary expectations
- start date
- veteran status
- demographic disclosures
- willingness to commute

Sensitive or voluntary information must remain explicitly user-controlled until the owner records an answer and verifies it. A verified answer may be reused on later applications. An unverified or missing answer must not be typed, and it keeps the package from being COMPLETE.

---

## 19. Approval Boundary

The canonical V1 irreversible-action boundary is:

**PREPARED → AWAITING_APPROVAL → APPROVED → APPLY**

Sabra may autonomously:

- search
- ingest
- normalize
- reject
- rank
- research
- generate documents
- prepare applications

Sabra should not submit an external job application without the user's explicit approval.

The approval the user gives is a batch of COMPLETE packages. Each approval binds to that package's content hash. A later edit invalidates the approval. Per-application clicking of Submit is not the boundary when the form has no CAPTCHA or bot challenge: after the hash-bound batch approval, Sabra may submit those forms. Dry-run remains the default until real submission is explicitly enabled. A dry run does not open the employer form, type into it, or upload a résumé.

---

## 20. Application Automation

Use browser automation where appropriate.

Preferred V1 implementation:

**Python + Playwright**

Adapters should understand supported application workflows rather than relying entirely on blind browser agents.

Use deterministic automation where possible.

Use model reasoning for ambiguity.

---

## 21. Application Safety

If the system encounters:

- unexpected legal attestations
- unknown required questions
- CAPTCHA or any bot challenge (reCAPTCHA, hCaptcha, Turnstile, and anything the detector cannot rule out)
- identity verification
- material ambiguity
- unsupported file requirements
- consent outside established policy

stop the automatic submission and request human intervention.

A challenge is not a blocker that abandons the application. Do not solve, bypass, or click past it. Open the pre-filled form in a headed browser so the person solves the check and presses Submit, then record submitted, abandoned, or failed from their confirmation or from the confirmation page. A detection failure is treated as a challenge. An unknown required field blocks that application and names the field. Do not guess.

---

## 22. Application Ledger

Every application must have a durable record.

Store:

```text
job
candidate
application_package
resume_version
answers
submission_time
submission_method
status
external_confirmation
events[]
```

This ledger becomes the foundation of outcome intelligence.

---

## 23. Event Architecture

Important actions should generate append-only events.

Examples:

```text
JOB_DISCOVERED
JOB_REJECTED
JOB_SCORED
JOB_SHORTLISTED
PACKAGE_GENERATED
PACKAGE_APPROVED
APPLICATION_STARTED
APPLICATION_SUBMITTED
APPLICATION_FAILED
EMPLOYER_RESPONSE
SCREEN_REQUESTED
INTERVIEW_REQUESTED
REJECTED
OFFER_RECEIVED
```

Current state may be projected from events.

Preserve history.

---

## 24. Outcome Tracking

Allow outcomes to be captured manually first.

Support:

- employer response
- recruiter contact
- phone screen
- interview
- rejection
- offer
- withdrawal
- no response

Later integrations may automate portions of this process.

Do not make email integration a blocker for V1.

---

## 25. Learning Dataset

Every application eventually creates training evidence:

**Job features + candidate features + strategy + application artifacts → outcome**

Preserve this data from day one.

Do not attempt sophisticated autonomous learning until sufficient trustworthy outcomes exist.

---

## 26. Outcome Intelligence

Future versions should determine patterns such as:

- titles producing interviews
- industries producing responses
- compensation bands producing traction
- résumé variants performing better
- qualifications associated with rejection
- company types producing offers
- application timing effects

V1 should collect the evidence required for this future capability.

---

## 27. User Interface

The application should feel like a command center rather than a traditional job board.

Primary surfaces:

**Today**

What Sabra wants the user to review.

**Opportunities**

Ranked current opportunities.

**Applications**

Prepared, approved and submitted applications.

**Pipeline**

Responses, screens, interviews and offers.

**Candidate**

Candidate intelligence and résumé truth.

**Searches**

Active search profiles.

**Settings**

Automation and policy controls.

---

## 28. Today View

The default page should answer:

> What requires my attention?

Examples:

```text
3 applications ready for approval

1 employer response

2 applications need additional information

17 new jobs analyzed overnight

4 new high-fit opportunities
```

Avoid dashboard clutter.

---

## 29. Opportunity Card

Each opportunity should communicate quickly:

```text
Company
Role
Location
Compensation
Fit
Confidence
Top reasons
Primary gap
Status
```

Primary actions:

**Review**

**Prepare Application**

**Pass**

---

## 30. Opportunity Detail

Show:

- job description
- source
- candidate fit
- strengths
- gaps
- compensation
- company research
- application strategy
- generated materials
- event history

The user should understand why Sabra recommends pursuing the opportunity.

---

## 31. Application Review

Before approval, present a clean review experience containing:

- original opening
- fit analysis
- tailored résumé
- cover letter if applicable
- proposed answers
- unresolved questions
- warnings

One primary action:

**Approve Application**

---

## 32. Architecture

Recommended V1:

**Frontend**

- Next.js
- React
- TypeScript

**Backend**

- Python
- FastAPI

**Database**

- PostgreSQL

**Automation**

- Playwright

**Background execution**

- lightweight job queue / worker architecture

**LLM**

- provider abstraction

Avoid unnecessary infrastructure.

---

## 33. Repository Structure

Recommended:

```text
sabra-job-engine/
  apps/
    web/
    api/

  packages/
    domain/
    providers/
    scoring/
    documents/
    automation/

  workers/
    discovery/
    research/
    applications/

  migrations/

  tests/

  docs/

  scripts/
```

Adjust when implementation evidence justifies it.

---

## 34. Domain Objects

At minimum implement:

```text
Candidate
CandidateProfile
Resume
SearchProfile
Company
Job
Opportunity
FitAssessment
ResearchPacket
ApplicationPackage
Application
ApplicationEvent
Outcome
Provider
```

Keep business logic out of UI components.

---

## 35. Provider Interface

Providers should expose a stable abstraction similar to:

```text
discover(search_profile)
fetch(job_reference)
normalize(raw_job)
verify(job)
```

Provider-specific logic belongs behind the interface.

---

## 36. Automation Adapter Interface

Application adapters should expose behavior similar to:

```text
supports(job)
inspect(job)
prepare(application_package)
validate()
submit(approval)
capture_confirmation()
```

Keep browser implementation details isolated.

---

## 37. AI Boundary

Use models where reasoning creates value.

Good uses:

- semantic fit
- résumé tailoring
- qualification-gap analysis
- research synthesis
- application-answer drafting
- ambiguous page interpretation

Bad uses:

- deterministic filtering
- state transitions
- IDs
- deduplication where deterministic evidence suffices
- authorization
- submission state
- audit history

Code owns truth.

Models assist judgment.

---

## 38. Reliability

Every autonomous operation should be:

- restartable
- idempotent where practical
- observable
- auditable
- failure-safe

A failed discovery source should not corrupt the existing opportunity queue.

A failed application should never be represented as submitted.

---

## 39. Testing

Prioritize tests around consequential behavior:

- normalization
- deduplication
- hard filtering
- scoring contracts
- candidate truth enforcement
- document generation
- approval authorization
- state transitions
- adapter behavior
- submission confirmation

Avoid tests that merely inflate coverage numbers.

---

## 40. Seed Vertical

Build the first complete workflow around:

**Medical aesthetics / skincare / medical device commercial roles in DFW and remote markets.**

Initial example titles:

- Territory Manager
- Regional Sales Manager
- Business Development Manager
- Account Executive
- Area Sales Manager
- Strategic Account Manager

This is the proving ground, not a permanent product limitation.

---

## 41. Initial Search Target

The first real system run should attempt to obtain:

**50+ legitimate current openings**

Then:

**hard-filter → score → rank → shortlist ~10 → prepare top 3**

Record every transition.

---

## 42. Do Not Build Yet

Explicitly defer:

- native mobile application
- massive multi-agent architecture
- dozens of job-board integrations
- autonomous model training
- vector infrastructure without demonstrated need
- elaborate knowledge graphs
- social network automation
- recruiter CRM
- interview coaching platform
- automatic negotiation
- autonomous application submission without approval
- enterprise features

Build the loop first.

---

## 43. V1 Milestones

### M1 — Foundation

Repository, database, domain model, CandidateProfile and SearchProfile.

### M2 — Discovery

Greenhouse + Lever ingestion and canonical Job normalization.

### M3 — Intelligence

Filtering, FitAssessment, ranking and opportunity queue.

### M4 — Application Factory

Master résumé, tailoring and ApplicationPackage generation.

### M5 — Approval

Application review and explicit authorization boundary.

### M6 — Execution

First supported Playwright application adapter.

### M7 — Ledger

Application events, pipeline and outcomes.

### M8 — Pilot

Real candidate + real jobs + real prepared applications.

---

## 44. Builder Operating Mode

Work autonomously through the milestones.

Do not stop after scaffolding.

Do not return merely because:

- one file was completed
- tests pass
- UI renders
- one provider works
- architecture was documented

Continue toward the V1 success condition.

---

## 45. Decision Hierarchy

When implementation ambiguity occurs, use this order:

1. This PRD.
2. Existing repository architecture that does not contradict the PRD.
3. Tests and executable contracts.
4. Simplest implementation satisfying the objective.
5. Document assumptions.
6. Continue.

Escalate only when a decision is irreversible, consequential, or genuinely requires owner preference.

---

## 46. Self-Correction Loop

Use:

**Inspect → Plan → Implement → Verify → Diagnose → Repair → Re-verify → Continue**

If verification fails:

Do not simply report failure.

Determine the cause.

Repair it.

Run verification again.

Continue until the milestone is actually satisfied or a genuine owner decision is required.

---

## 47. PRD Improvement Rule

Implementation will reveal weaknesses in this document.

The builder may propose improvements to the PRD when code or real-world evidence proves an assumption wrong.

Do not silently rewrite product intent.

Separate:

**implementation refinement**

from:

**product-direction change**

Product-direction changes require owner review.

---

## 48. Definition of Done

V1 is NOT done because:

```text
npm run build
```

passes.

V1 is done when the complete loop works with real data:

**Candidate**

↓

**Search criteria**

↓

**50+ real jobs discovered**

↓

**normalized + deduplicated**

↓

**hard filtered**

↓

**semantically evaluated**

↓

**ranked opportunities**

↓

**~10 useful shortlist candidates**

↓

**top applications prepared**

↓

**candidate truth verified**

↓

**user approval available**

↓

**supported application workflow executable**

↓

**submission recorded accurately**

↓

**outcome trackable**

---

## 49. Final Pilot Evidence

Before declaring V1 complete, produce a pilot report containing:

```text
Jobs discovered
Sources queried
Duplicates removed
Hard-filtered jobs
Qualified jobs
Shortlisted jobs
Applications prepared
Applications approved
Applications attempted
Applications successfully submitted
Failures
Unknown candidate fields encountered
Outcome events captured
```

Include at least three real prepared application packages.

---

## 50. Final Directive

Build the smallest system that completely closes the loop.

Do not confuse architectural sophistication with product progress.

Do not build infrastructure because it might someday be useful.

Do not optimize for application volume.

Do not fabricate candidate information.

