# Windows pilot — Nick's machine

Checkout: `C:\Users\nrsan\hallam`
Private data (résumé, answers, database): `C:\Users\nrsan\hallam-private`

Personal files stay in the private directory. Do not copy them into the git checkout.

Use `main`. The follow-up fixes land there after this pull request merges. Do not check out the old feature branch.

## 1. Pull main

```powershell
cd C:\Users\nrsan\hallam
git fetch origin
git checkout main
git pull origin main
```

## 2. Install dependencies and a headed browser

[uv](https://docs.astral.sh/uv/getting-started/installation/) is the installer this repo uses. From the checkout:

```powershell
uv sync --all-extras
uv run playwright install chromium
```

`playwright install chromium` downloads the browser used by the headed handoff and the default suite's synthetic local form acceptance tests. Those tests use loopback fixtures and must never be pointed at a live employer form.

`--all-extras` also installs `reportlab`, which renders the résumé PDF that is uploaded (see section 8). Without it a real submission blocks at the upload rather than sending a Markdown file.

## 3. Point the engine at the private directory

If `C:\Users\nrsan\hallam-private\hallam-env.ps1` already sets `SABRA_PRIVATE_DIR`, dot-source it and skip `setx`:

```powershell
. C:\Users\nrsan\hallam-private\hallam-env.ps1
```

`sabra.env` in that private directory is the same idea: it should export `SABRA_PRIVATE_DIR` (and `SABRA_DATABASE_URL` only when you want a file other than the default below). The engine reads `SABRA_*` from the process environment. A `.env` file is loaded from the checkout working directory, not from the private directory, so keep secrets in `hallam-env.ps1` or `sabra.env` and load those yourself.

Otherwise, in this PowerShell window:

```powershell
$env:SABRA_PRIVATE_DIR = "C:\Users\nrsan\hallam-private"
```

To keep that for later windows:

```powershell
setx SABRA_PRIVATE_DIR "C:\Users\nrsan\hallam-private"
```

`setx` does not change the current window. Open a new one, or set `$env:SABRA_PRIVATE_DIR` there too.

With `SABRA_PRIVATE_DIR` set, and with no other `SABRA_*` path overrides, the engine uses:

| What | Where |
| --- | --- |
| Database | `C:\Users\nrsan\hallam-private\var\sabra.db` |
| Candidate profiles | `C:\Users\nrsan\hallam-private\candidates` |
| Artifacts, evidence, screenshots | `C:\Users\nrsan\hallam-private\var\` |

`SABRA_DATABASE_URL` overrides that database path. Set it only when the file you want is not `<private-dir>\var\sabra.db`:

```powershell
$env:SABRA_DATABASE_URL = "sqlite+pysqlite:///C:/Users/nrsan/hallam-private/var/sabra.db"
```

If a command says no candidate is stored, the message names the database file it opened. That file is the one to point `SABRA_DATABASE_URL` at, or load the profile into it with `sabra init --profile`.

Dry-run is already the default (`SABRA_AUTOMATION_DRY_RUN` defaults to true). Leave it that way until the last step. Dry-run does not open an employer form, does not type, and does not upload a résumé.

## 4. Load the candidate, then record and verify answers

```powershell
cd C:\Users\nrsan\hallam
uv run sabra init --profile C:\Users\nrsan\hallam-private\candidates\profile.yaml
```

Use the real profile path if the filename differs. Upload the résumé on the **Candidate** page (http://127.0.0.1:8787/candidate) if it is not already in that database: `.pdf`, `.docx` (text in headers and footers is read too), `.txt` and `.md` are accepted; anything else (`.rtf`, `.odt`, ...) is refused rather than guessed at. The résumé file itself stays outside git.

Sensitive questions stay blank until you record an answer and verify it. Recording alone is not enough, and the engine will not invent a value.

The values below are examples. Replace them with the candidate's real answers before you verify.

```powershell
uv run sabra answers record salary_expectations "140000 base plus commission"
uv run sabra answers verify salary_expectations
uv run sabra answers record background_check "Yes, I consent to a background check"
uv run sabra answers verify background_check
uv run sabra answers list
```

The answers page lists every key the Greenhouse and Lever adapters know, including `us_citizen`, `spanish`, `resume_headline`, `phone`, `country`, `city`, `state_residence`, `postal_code`, `street_address`, `age_eligibility`, and `non_compete`. Choose **Custom key** for a question that is not in the list.

Every **verified** answer in the list rides on each package you prepare, not only the seven default questions, so the board-specific questions (citizenship, language, prior employment, address, and the rest) are answered from it. A form question is matched to an answer by its question key and, where the key is unknown, by its wording: record the **Question wording** exactly as the form shows it (case, spacing and a trailing `*`, `?` or `:` do not matter; symbols do, so "C++", "C#" and "C" are three different questions and "18+" is not "18") and a custom key will match. An answer that is only recorded, not verified, is never typed and never put on a package; it shows up as an unanswered question when a form asks for it, and that blocks the application.

A verified answer is typed into a free-text field only when the form's wording is the wording recorded with it. A key match alone (the phrase catalogue recognised the question) is accepted only for a yes/no answer in a drop-down or checkbox, because a phrase also appears in questions it does not answer ("If you are not a U.S. citizen, what is your visa type?"). Anything else is reported as unresolved and blocks, naming the field: record the answer again with the exact question wording and verify it. This holds for the seven default questions too: if you record `sponsorship` with the board's wording ("Do you now or will you in the future require sponsorship?"), that wording rides on the package next to the default one and fills the board's field. Values copied from your profile (city, state, postal code, current employer, LinkedIn) follow the same rule: they are typed into a field the form itself marks as that input (a `city` or `candidate-location` field, a `zip` field, or an `autocomplete` of address-level2, address-level1, postal-code or organization), or when you recorded the question's wording. "What city would you like to work in?" does not get your home city, and a board's own "What city do you currently live in?" needs your `city` answer recorded with that wording. The ethnicity question ("Are you Hispanic/Latino?") has its own key, `hispanic_ethnicity`, separate from `race`.

Matching is exact. A drop-down is set only when one option equals your answer (case and spacing aside): "Yes" is not "Yes - I will require sponsorship", so that field blocks and is named instead of guessed. Record the answer using the option's own words. Fields that belong to the employment and education blocks are filled from your verified history, never from an answer, so a start-date answer cannot land in a job's start month.

On the answers page, **Verify** sends the value shown beside it. If the stored value changed since you loaded the page, nothing is verified; reload and verify what you now see.

The same actions are on http://127.0.0.1:8787/answers after the API is running (step 5). A row is usable on a form only when the list shows it verified.

## 4a. Define the search, run it, and read the results from the browser

Start the dashboard (step 5 has the command), then everything below can be done at http://127.0.0.1:8787 without the command line.

**Candidate** (`/candidate`). Fill in **Your details** (name, email, phone, street address, city, state, postal code, country, LinkedIn, preferred headline) and press **Save my details**. The boxes start with what is stored, including values read from the résumé, and the tag beside each shows how sure Sabra is of it. Only a box you change, or one you tick **This is right** for, is stored as stated by you (VERIFIED) in a new profile version; every other value keeps its level. A confirmed value records what it was confirmed from. What you state wins over anything read from a résumé, whether the résumé was uploaded before or after. A blank box changes nothing. Packages read these values: the first and last name, email, phone, address and LinkedIn are what the form fillers type. Each row of the profile on that page shows its truth level: VERIFIED (you said it), INFERRED (read from the résumé, never typed onto a form until you confirm it), or UNKNOWN. The same page lists the master résumé versions and takes the upload.

**Searches** (`/searches`). A search says what kind of job to look for. Edit the active search in place, or press **New search**. Titles, words, cities and the like are one per line. **Save search** writes it and records a `SEARCH_PROFILE_UPDATED` event; **Make this the active search** is what **Run now**, `sabra discover` and `sabra rank` use. Saving never wipes a field the form does not show (excluded companies, seniority levels and so on): they are sent back as they were.

The same thing from a file: copy `config/search_profile.example.yaml` (every field is explained in it; the example is a generic Dallas-Fort Worth office/administrative search with a $70,000 target), edit it, and load it:

```powershell
uv run sabra searches load C:\Users\nrsan\hallam-private\my_search.yaml
uv run sabra init --search-profile C:\Users\nrsan\hallam-private\my_search.yaml
uv run sabra searches list
uv run sabra searches show
uv run sabra searches activate SEARCH_ID
```

Loading the same file again is harmless. If a file's id (or the one derived from its name) matches a stored search that is different, the load is refused; add `--replace` to replace it (`Replaced existing search <id> (was '<name>')` is printed). Leave `id:` out of your own files. A stored search is never overwritten or re-seeded by `sabra init`; the built-in medical-aesthetics profile is created only when the candidate has no search at all.

**Today** (`/`). The first page. It lists what needs you: packages ready for approval, applications that need more information (and how many open questions), submissions waiting for your report, new high-fit roles since the last run began, and employer responses since you last pressed **Mark responses as seen**. **Run now** starts the whole loop in the background (discover, rank, research the shortlist; tick the box to also prepare packages for the top roles) and shows live progress, source by source. Only one run can be active at a time. The same run from PowerShell, with a progress table:

```powershell
uv run sabra run
uv run sabra run --prepare
uv run sabra run --kind rank
```

`--kind` is `discover`, `rank` (rank and research) or `full` (the default). A run never approves or submits anything. `--only-new` skips boards read successfully in the last 20 hours (see section 4b). While it works the dashboard stays usable: ranking commits in batches. A run whose process was killed shows as FAILED ("abandoned") as soon as the next request notices the process is gone (or after an hour with no sign of life, when that cannot be checked), so it cannot block the next one. Ctrl-C ends the run as FAILED too. `sabra discover` and `sabra rank` refuse to start while a run is active, and the database itself allows only one active run.

`sabra rank` also researches the shortlist now (company sites are read; a packet less than 7 days old is kept). One site that cannot be read never stops the run. Add `--no-research` to skip it.

**Opportunities** (`/opportunities`). Each card shows the pay (or "not listed"), the confidence, the main reason, the biggest gap and the fit. **Prepare application** is unchanged. **Pass** moves the role to REJECTED (reason `OWNER_PASSED`) and it is never brought back by a later run. A package that was waiting for approval is withdrawn with it. Pass is refused once the application is approved or later.

**Settings** (`/settings`). The configuration is read only: whether dry-run is on and the variable that changes it, the language-model provider, the database, the private and artifacts folders, the hand-off timeout, whether Playwright's Chromium is installed, the active search and the version.

## 4b. Add a job board

Boards live in the registry file (`SABRA_SOURCE_REGISTRY_PATH`, otherwise `config/sources.yaml`). You do not edit it by hand.

**From the browser.** Open **Settings** (http://127.0.0.1:8787/settings). **Job boards** lists every board with its service (Greenhouse, Lever, Ashby), whether it is on, how many postings it had when added, its health and its last error, with **Disable** / **Enable** buttons. Under **Add a board**, paste a careers URL (for example `https://jobs.ashbyhq.com/siepe`, `https://job-boards.greenhouse.io/revance` or `https://jobs.lever.co/elfbeauty`) and press **Check this board**. Sabra reads the board once and shows whether it is live, how many postings it has, how many mention one of your search's cities or "remote", and titles that fit your search. **Add this board** appears only for a live board that is not already listed. Company name and industry are optional; the name defaults to the board's own name where the service gives one (Greenhouse), otherwise to its token.

**From PowerShell.** The same thing:

```powershell
uv run sabra sources list
uv run sabra sources probe https://jobs.ashbyhq.com/siepe
uv run sabra sources add https://jobs.ashbyhq.com/siepe --display-name "Siepe" --industry "Fintech"
uv run sabra sources probe siepe --kind ASHBY       # a bare name needs --kind
uv run sabra sources disable ashby-siepe            # and: enable
```

Adding appends one entry at the end of the registry file and keeps every comment already in it; it refuses a board that does not answer and one already listed. The new board is read on the next run. Disabling a board keeps its history and stops reading it.

**What a run costs.** One slow or failing board never stops the run: it gets its own time budget, its failure is recorded on its row (health and last error, visible on Settings, `sabra sources list` and `sabra status`), and the other boards carry on. Re-run during the day with `uv run sabra run --only-new` (or `sabra discover --only-new`) to skip boards read successfully in the last 20 hours; `--only-new-hours 6` changes the window. A registry entry can also set `max_items: 200` to cap the postings read from one very large board.

**A role on a board the engine cannot fill.** Greenhouse and Lever forms are filled by Sabra's adapters. Ashby and company careers pages are not, so an approved role there is not blocked. With dry-run on, **Apply** is replaced by **Open in browser for you to fill** (disabled) and `sabra apply APPLICATION_ID` reports `DRY_RUN` with a plan that says "manual handoff; no adapter". With real submission on (section 8), pressing it:

1. checks the posting is still up (a closed posting blocks the application, as for any other),
2. saves your tailored résumé and cover letter as PDFs in `<artifacts>\applications\<opportunity id>\` (`<First>-<Last>-Resume.pdf`, `<First>-<Last>-Cover-Letter.pdf`) and shows their paths on the row (the CLI prints them),
3. opens the posting's apply page in a visible browser and **types nothing**,
4. waits for you to fill the form, attach those files, submit it yourself, and press **I submitted it** (or **I didn't submit**).

It follows the same rules as a CAPTCHA handoff: the row stays **APPLYING** while the window is open, a closed window or timeout waits for your report, **I didn't submit** returns it to **BLOCKED** (retryable), a report from `sabra handoff-outcome` or the forced report works as in section 8, and a submission is recorded with method `HEADED_HUMAN` and adapter `manual` with a note that nothing was typed. Sabra never recognises a confirmation page on these sites, so the report is always yours.

## 5. Prepare packages, read them, and approve a batch

```powershell
uv run uvicorn --app-dir apps/api sabra_api.main:app --host 127.0.0.1 --port 8787
```

Then, in the browser on this PC:

1. http://127.0.0.1:8787/ — **Today**: what needs you, the run controls and the top of the queue.
2. http://127.0.0.1:8787/opportunities — prepare a package for each role. Preparing an application that is already approved returns "already approved" and leaves the approved package in place.
3. http://127.0.0.1:8787/applications — the list is the latest revision of each application. Open **Read package** before you approve. Copy the application id with **Copy**. Tick the complete packages and press **Approve selected**.
4. An approved row says **Approved** and names the next step. **Apply** stays disabled while dry-run is on. A blocked or failed row can be applied to again once its cause is fixed, or rebuilt with **Refresh package** if the cause is a missing or wrong answer (section 6).
5. Batch approval takes COMPLETE packages only. The dashboard and the CLI never approve a package with unresolved questions.

Each approval is stored against that package's content hash. If the package changes later, that approval no longer authorizes submission.

### Read one role, then review one application

- Click a role's title on **Opportunities** to open its detail page (`/opportunities/ID`). It shows the posting, why Sabra recommends it, every fit component with its reason, strengths, gaps and risks, whether it passed the hard filter and why, what is verified about the company apart from what is only inferred, where the application stands, and the event history. **Prepare application** and **Pass** are there too, and are disabled once an approval exists. An id that does not exist shows a 404 page.
- **Read package** on the Applications page (or **Review application** on a role) opens the review page (`/packages/ID`). Read it top to bottom: the original opening (the description is collapsed), the fit analysis, the tailored résumé, the cover letter, each proposed answer with its truth level and whether it is verified or sensitive, the unresolved questions, and the warnings. Warnings include how each artifact was written: which were model-written and which came from the template, what the truth gate rejected, regenerated or replaced with the template, and the model token counts. The recruiter and hiring-manager messages and the interview preparation are in a collapsed section.
- The **Decision** box shows the content hash the approval will be bound to. **Approve application** approves exactly this package at exactly that hash; it is disabled, with the reason, when the package is INCOMPLETE, not truth-clean, superseded by a newer revision, or already approved. **Refresh package** rebuilds it (revoking an earlier approval first when there is one) and opens the new revision. **Reject** withdraws the application and passes on the role; it is not offered for an approved application (refresh it first) or a submitted one.
- On **Today**, each count opens the right page: ready for approval, needs more information and waiting-for-your-report open Applications filtered to just those rows (press **Show all** to clear the filter); new high-fit opens Opportunities; employer responses opens Pipeline.

### Record what employers do

Open http://127.0.0.1:8787/pipeline. Every application past approval is listed under its stage: approved, applying / handoff, applied, response, screen, interview, offer, rejected by employer, withdrawn, expired. Each shows the company, role, when and how it was submitted and its last event. **History** shows its outcomes and events; **Review** opens the package.

Use **Record outcome** to say what happened: employer response, recruiter contact, phone screen, interview, offer, rejection, withdrawal or no response. A note, the contact's name and the date it happened are optional (the date defaults to now; a future date is refused). Each outcome writes an event and moves the opportunity (and its postings) along the job lifecycle: applied, response, screen, interview, offer, rejected by employer, withdrawn. A withdrawal also withdraws the application. The application row itself stays APPLIED through response, screen, interview, offer and rejection; the opportunity carries those stages. An outcome that does not fit where the lifecycle is (a phone screen after an interview, anything after a rejection) is recorded without moving it, and the page says so. No new lifecycle edges were added. Employer outcomes can be recorded only for a submitted application. The one exception is a withdrawal, which is refused while a submission is in progress or waiting for your report (report how it ended first), and for a blocked or failed application that may have reached the employer. A withdrawal moves the application and the role together or not at all. An outcome cannot be dated before the application was submitted.

From PowerShell:

```powershell
uv run sabra pipeline                     # the same list, by stage
uv run sabra outcome APPLICATION_ID interview --note "Panel on Friday" --at 2026-10-24
uv run sabra outcome APPLICATION_ID rejection --contact "Pat Lee"
```

`--at` takes an ISO date or datetime; a value with no time zone is read as local time. Kinds can be typed in any case (`phone-screen` works).

### Export the learning dataset

Every application becomes one line of evidence: job features, candidate features, strategy, artifact ids and the latest outcome.

```powershell
uv run sabra ledger export learning.jsonl
```

The command refuses to replace an existing file unless you add `--force`. The same data downloads from http://127.0.0.1:8787/api/learning-records. Names, email, phone, address, the candidate id, outcome notes and contacts are never in the export; any string that still looks like an email address is replaced. Outcome notes and contacts stay in the ledger only.

## 6. Refresh packages after you verify new answers

An approved package keeps the contact fields it had when it was built. Verified answers recorded later (phone, street address, city, state, postal code, country) are not copied into that frozen package, and Prepare on an approved application stays refused.

Profile identity wins when both the profile and a verified answer have a value. A verified answer fills a field only when the profile value is blank. An unverified answer is never used. Street address comes only from a verified `street_address` answer.

Refresh revokes the approval, writes the old content hash to the ledger, builds a new revision, and returns the application to awaiting approval. You then approve the new package.

It works on an **approved**, **blocked** or **failed** application. A blocked or failed one is the usual case after a required question had no verified answer: record and verify the answer, press **Refresh package**, approve the new revision, then apply again. It is refused when anything suggests the employer may have the application: a recorded submission, or a submit click that nothing confirmed and that you have not yet reported on. It is also refused once the posting has been found closed. Giving up on a blocked or failed application (reject) is allowed under the same conditions and withdraws it.

On http://127.0.0.1:8787/applications, press **Refresh package** on one approved, blocked or failed row, or **Refresh all unsubmitted** and confirm the dialog. Then tick the new complete packages and press **Approve selected**.

Or from PowerShell, using the application id from **Copy**:

```powershell
uv run sabra packages refresh APPLICATION_ID
uv run sabra packages refresh --all-unsubmitted
```

`--all-unsubmitted` asks you to confirm. Add `--yes` only when you intend to revoke every unsubmitted approval without that prompt.

## 7. Dry-run first

Leave `SABRA_AUTOMATION_DRY_RUN` unset or `true`. Restart the API if you change it.

```powershell
uv run sabra apply APPLICATION_ID
```

The outcome is `DRY_RUN`. The command does not open a browser, does not navigate to the employer, and does not type or upload. The ledger records the attempt with `navigated: false`. The dashboard **Apply** button is disabled and says why.

## 8. Enable real submission

Both switches are required. `--real` alone is refused while dry-run is still on. Restart the API after changing the variable so the dashboard sees it.

```powershell
$env:SABRA_AUTOMATION_DRY_RUN = "false"
uv run sabra apply APPLICATION_ID --real
```

Or, on http://127.0.0.1:8787/applications, press **Apply** and confirm the dialog. The dialog does not solve a CAPTCHA.

- Before anything is claimed or opened, Sabra asks the posting's provider whether the posting is still up. If it is gone (HTTP 404 or 410, or "closed"), the job is moved to EXPIRED, the application is BLOCKED with reason `posting no longer available`, and no browser opens. If the check itself fails (the network is down, a timeout, a server error), the job is **not** expired: a warning event is written and the attempt goes on. The check makes one request with the discovery timeout and no retries, and it runs after the approval is re-verified: if the application changed meanwhile (for example you refreshed it), nothing is blocked or claimed and the attempt is refused.
- The résumé is uploaded as a PDF, `<First>-<Last>-Resume.pdf`, rendered at that moment from the approved package's Markdown, so the file is bound to the approval hash. The cover letter is rendered the same way (`<First>-<Last>-Cover-Letter.pdf`). The Markdown copies (`tailored_resume.md`, `cover_letter.md`) stay beside them in `var/artifacts/applications/<opportunity id>/` for you. If a PDF cannot be produced, the application blocks before the employer's page is opened and nothing is uploaded in its place. Letters the standard fonts lack are mapped to a plain form where one exists (ł to l, đ to d, and accents are dropped only when the font cannot draw them); a character with no plain form in the name or contact line stops the upload rather than printing a `?` in your name. Elsewhere in the résumé a `?` is tolerated and the count is recorded. The attempt record in the ledger holds the sha256 of each uploaded PDF and the renderer version.
- The first and last name typed on the form come from the profile's structured `first_name` / `last_name` when you set them; otherwise the full name is split and a middle initial ("JORDAN A. RIVERA") is dropped, not made part of the surname.
- No CAPTCHA or bot challenge: the adapter fills and submits the form in a single browser session. It counts the submission as confirmed only when the page has left the form and shows the employer's confirmation marker (Greenhouse: a `/confirmation` page or confirmation element; Lever: the `/thanks` page). Words such as "thank you" in the posting never count. If the button was clicked and nothing confirms it, the application stays **APPLYING** with an `APPLICATION_UNCONFIRMED` event; check with the employer or your email, then report the outcome (below).
- reCAPTCHA, hCaptcha, Turnstile, a similar challenge, or a probe that fails: nothing is submitted and nothing is solved. A visible Chromium window opens with the pre-filled form. You solve the check and press Submit yourself. The form is filled only in that visible window.
- The application stays **APPLYING** while the window is open. The window is not closed for you, and a timeout (`SABRA_HANDOFF_TIMEOUT_SECONDS`, default 2700) is the only thing that ends the wait on its own.
  - From the CLI, type `submitted`, `abandoned`, or `failed` when asked (`q` means abandoned). Anything else is asked again.
  - From the dashboard, **Apply** returns at once with `HANDED_TO_HUMAN`; the row then shows **I submitted it** and **I didn't submit**. **Apply** is disabled while a request is in flight and while a handoff is active.
  - A report is accepted only when nothing is still running for that application. While the dashboard's window is open, or while a run is in progress, `sabra handoff-outcome` is refused: answer in the dashboard instead, or close the window first. If you close the browser, or the dashboard was stopped or restarted, the application stays APPLYING and the report is accepted. From PowerShell:

    ```powershell
    uv run sabra handoff-outcome APPLICATION_ID submitted
    ```

  - **Ctrl+C during `sabra apply --real`**, or any other interruption of a run, leaves the application APPLYING with an `interrupted` event. That event is what lets you report the outcome afterwards with `sabra handoff-outcome`. Nothing is guessed about whether the employer got it.
  - The CLI prompt also times out: after `SABRA_HANDOFF_TIMEOUT_SECONDS` with no answer the application stays **APPLYING**, the same as the dashboard. Report its outcome before retrying: the person may have submitted between observation polls.
  - **Hard-killed process** (the dashboard window or `sabra apply` was killed, or the PC lost power) while an application was APPLYING: no `interrupted` event could be written, so a normal report is refused and the dashboard keeps saying a submission is running. First check with the employer or your email whether it was submitted. Then force a report:

    ```powershell
    uv run sabra handoff-outcome APPLICATION_ID submitted --force
    ```

    You are asked to type the application id back; `--yes` does not exist and nothing else substitutes. It is refused while that same process still has a run or window for the application. The dashboard shows a separate **Force a report** control only for a row that has been APPLYING with no new activity for longer than the handoff timeout, and it also asks you to type the id. A forced report is recorded as `forced: true` together with the last event that was in the log, so the ledger shows it was a human override and not something Sabra observed. Use it only when you are sure nothing is still running.

  - An explicit `abandoned` report moves the application to **BLOCKED**, not WITHDRAWN. A timeout requires a report and stays APPLYING. A blocked application can be applied to again if its opportunity has not expired: it is re-authorised against the same approval hash first. `failed` moves it to FAILED, which is also retryable for a live opportunity. After an unconfirmed click, a report of `failed` or `abandoned` both move it to BLOCKED, because that click may have reached the employer until you say it did not.
  - A confirmation page is detected from the page itself (same rule as above) and recorded as submitted without a report.

Turn dry-run back on when you are done:

```powershell
$env:SABRA_AUTOMATION_DRY_RUN = "true"
```

## 9. Run it every morning

To have the loop run by itself, register a daily Windows Task Scheduler task. Nothing is registered until you run this yourself, from the checkout:

```powershell
cd C:\Users\nrsan\hallam
.\scripts\schedule-windows.ps1 -At 06:30            # discover, rank, research
.\scripts\schedule-windows.ps1 -At 06:30 -Prepare   # ...and prepare packages for the top roles
.\scripts\schedule-windows.ps1 -Unregister          # remove it
```

The task is called "Sabra daily run". It runs as you, only while you are logged on (if the PC was off at 06:30 it runs when you next log on), and never starts a second run on top of one that is still going. Each run loads `C:\Users\nrsan\hallam-private\hallam-env.ps1` (pass `-PrivateDir` for another folder), then runs

```powershell
uv run sabra run --json >> <data_dir>\logs\run-<date>.log
```

`<data_dir>` is `C:\Users\nrsan\hallam-private\var` unless `hallam-env.ps1` sets `SABRA_DATA_DIR`. The task stops with an error, and runs nothing, if `hallam-env.ps1` is missing. The log is UTF-8 and holds the run's JSON record, per-source outcomes included; the dashboard's **Today** page shows the last run's summary. The scheduled run only discovers, ranks, researches and (with `-Prepare`) prepares. It does not approve, and it does not submit: dry-run stays the default, and a package still waits for you on the Applications page. The task runs `scripts\run-sabra-daily.ps1`; do not move it.

## 10. Model-assisted drafting (optional)

Add these lines to `C:\Users\nrsan\hallam-private\sabra.env` and restart the dashboard
(or dot-source `hallam-env.ps1` again in the window that runs the CLI). Never put the key
anywhere inside the git checkout.

```text
SABRA_LLM_PROVIDER=anthropic
SABRA_LLM_API_KEY=<your Anthropic key>
SABRA_LLM_MODEL=claude-opus-5-5
```

With a provider set, the engine uses the model for three things only: choosing and
ordering the résumé's bullets, skills, headline and summary; writing the cover-letter
body; and writing the fit narrative (why this job, gap analysis). Bullets are never
written by the model: it returns bullet ids and the engine copies the candidate's own
text. Scores, ranking, approval and submission stay deterministic. With no provider
set, or if the model fails, the package is built exactly as before and carries a
"model unavailable" warning when a call failed. `SABRA_LLM_PROVIDER=openai_compatible`
with `SABRA_LLM_BASE_URL` works the same way against any OpenAI-style endpoint.

**Truth gate.** Every model-written text is checked before it can enter a package.
Numbers, durations, names, titles, dates, certifications and skills must appear in
the candidate's profile or master résumé (the job posting and company research are
never evidence about the candidate, in any grammatical person), and a second model
call must quote supporting evidence for each claim. A text that fails is regenerated
once with the problems fed back, then replaced by the deterministic template. Each
package records, per artifact, whether it was `model:<provider>/<model>` or `template`,
plus the gate result and per-call token counts, in its truth ledger under
`generation`; the review page shows this under Warnings. Approval still binds to the
content hash of the final texts. Model calls are logged in the `model_calls` table.

## What this build will not do

- It will not type an answer you have not verified.
- It will not open, type into, or upload to an employer form during dry-run.
- It will not solve, bypass, or hide a CAPTCHA.
- It will not submit while dry-run is on.
- It will not prepare a new package for an application that is already approved, blocked or failed; use **Refresh package**, which revokes the old approval first.
- An unknown required field blocks that one application and names the field.
- Only the first employment block and the first education block are filled. A form that requires a second block stays blocked until that block is handled.
- Login walls and legal attestations still block; they are not handed to the headed browser.
- Name, e-mail, phone and country are typed only from the verified package or verified answers. They are never read out of the résumé text; a required identity field with no verified value blocks and names the field.
- Only one submission can run per application. A second Apply while one is in progress is refused.
