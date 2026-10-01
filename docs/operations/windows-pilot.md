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

Use the real profile path if the filename differs. Upload the résumé from the dashboard (Overview) if it is not already in that database. The résumé file itself stays outside git.

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

The same actions are on http://127.0.0.1:8787/answers after the API is running (step 5). A row is usable on a form only when the list shows it verified.

## 5. Prepare packages, read them, and approve a batch

```powershell
uv run uvicorn --app-dir apps/api sabra_api.main:app --host 127.0.0.1 --port 8787
```

Then, in the browser on this PC:

1. http://127.0.0.1:8787/ — the overview lists the shortlisted queue.
2. http://127.0.0.1:8787/opportunities — prepare a package for each role. Preparing an application that is already approved returns "already approved" and leaves the approved package in place.
3. http://127.0.0.1:8787/applications — the list is the latest revision of each application. Open **Read package** before you approve. Copy the application id with **Copy**. Tick the complete packages and press **Approve selected**.
4. An approved row says **Approved** and names the next step. **Apply** stays disabled while dry-run is on.

Each approval is stored against that package's content hash. If the package changes later, that approval no longer authorizes submission.

## 6. Refresh packages after you verify new answers

An approved package keeps the contact fields it had when it was built. Verified answers recorded later (phone, street address, city, state, postal code, country) are not copied into that frozen package, and Prepare on an approved application stays refused.

Profile identity wins when both the profile and a verified answer have a value. A verified answer fills a field only when the profile value is blank. An unverified answer is never used. Street address comes only from a verified `street_address` answer.

Refresh revokes the approval, writes the old content hash to the ledger, builds a new revision, and returns the application to awaiting approval. You then approve the new package. It refuses an application that has already been submitted.

On http://127.0.0.1:8787/applications, press **Refresh package** on one approved row, or **Refresh all unsubmitted** and confirm the dialog. Then tick the new complete packages and press **Approve selected**.

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

- No CAPTCHA or bot challenge: the adapter submits, and the ledger records `APPLICATION_SUBMITTED`.
- reCAPTCHA, hCaptcha, Turnstile, a similar challenge, or a probe that fails: nothing is submitted and nothing is solved. A visible Chromium window opens with the pre-filled form. You solve the check and press Submit yourself.
- When the prompt asks, type `submitted`, `abandoned`, or `failed`. That outcome is written to the ledger. A confirmation page can also be detected and recorded as submitted.

Turn dry-run back on when you are done:

```powershell
$env:SABRA_AUTOMATION_DRY_RUN = "true"
```

## What this build will not do

- It will not type an answer you have not verified.
- It will not open, type into, or upload to an employer form during dry-run.
- It will not solve, bypass, or hide a CAPTCHA.
- It will not submit while dry-run is on.
- It will not prepare a new package for an application that is already approved.
- An unknown required field blocks that one application and names the field.
- Only the first employment block and the first education block are filled. A form that requires a second block stays blocked until that block is handled.
- Login walls and legal attestations still block; they are not handed to the headed browser.
