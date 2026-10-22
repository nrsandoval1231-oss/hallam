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

`playwright install chromium` downloads the browser the headed handoff opens. The default test suite does not need it and must not be pointed at a live employer form.

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

`--kind` is `discover`, `rank` (rank and research) or `full` (the default). A run never approves or submits anything. While it works the dashboard stays usable: ranking commits in batches. A run whose process was killed shows as FAILED ("abandoned") as soon as the next request notices the process is gone (or after an hour with no sign of life, when that cannot be checked), so it cannot block the next one. Ctrl-C ends the run as FAILED too. `sabra discover` and `sabra rank` refuse to start while a run is active, and the database itself allows only one active run.

`sabra rank` also researches the shortlist now (company sites are read; a packet less than 7 days old is kept). One site that cannot be read never stops the run. Add `--no-research` to skip it.

**Opportunities** (`/opportunities`). Each card shows the pay (or "not listed"), the confidence, the main reason, the biggest gap and the fit. **Prepare application** is unchanged. **Pass** moves the role to REJECTED (reason `OWNER_PASSED`) and it is never brought back by a later run. A package that was waiting for approval is withdrawn with it. Pass is refused once the application is approved or later.

**Settings** (`/settings`). Read only: whether dry-run is on and the variable that changes it, the language-model provider, the database, the private and artifacts folders, the hand-off timeout, whether Playwright's Chromium is installed, the active search and the version.

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
  - The CLI prompt also times out: after `SABRA_HANDOFF_TIMEOUT_SECONDS` with no answer the application moves to **BLOCKED**, the same as the dashboard.
  - **Hard-killed process** (the dashboard window or `sabra apply` was killed, or the PC lost power) while an application was APPLYING: no `interrupted` event could be written, so a normal report is refused and the dashboard keeps saying a submission is running. First check with the employer or your email whether it was submitted. Then force a report:

    ```powershell
    uv run sabra handoff-outcome APPLICATION_ID submitted --force
    ```

    You are asked to type the application id back; `--yes` does not exist and nothing else substitutes. It is refused while that same process still has a run or window for the application. The dashboard shows a separate **Force a report** control only for a row that has been APPLYING with no new activity for longer than the handoff timeout, and it also asks you to type the id. A forced report is recorded as `forced: true` together with the last event that was in the log, so the ledger shows it was a human override and not something Sabra observed. Use it only when you are sure nothing is still running.

  - `abandoned` and a timeout move the application to **BLOCKED**, not WITHDRAWN. A blocked application can be applied to again: it is re-authorised against the same approval hash first. `failed` moves it to FAILED, which is also retryable. After an unconfirmed click, a report of `failed` or `abandoned` both move it to BLOCKED, because that click may have reached the employer until you say it did not.
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
