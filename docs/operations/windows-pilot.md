# Windows pilot — Nick's machine

Checkout: `C:\Users\nrsan\hallam`
Private data (résumé, answers, database): `C:\Users\nrsan\hallam-private`

Personal files stay in the private directory. Do not copy them into the git checkout.

## 1. Pull this branch

```powershell
cd C:\Users\nrsan\hallam
git fetch origin
git checkout cursor/answer-memory-autofill-approval-9e8f
git pull origin cursor/answer-memory-autofill-approval-9e8f
```

## 2. Install dependencies and a headed browser

[uv](https://docs.astral.sh/uv/getting-started/installation/) is the installer this repo uses. From the checkout:

```powershell
uv sync --all-extras
uv run playwright install chromium
```

`playwright install chromium` downloads the browser the headed handoff opens. The default test suite does not need it and must not be pointed at a live employer form.

## 3. Point the engine at the private directory

In the same PowerShell window, and again in any new one:

```powershell
$env:SABRA_PRIVATE_DIR = "C:\Users\nrsan\hallam-private"
```

To keep that for later windows:

```powershell
setx SABRA_PRIVATE_DIR "C:\Users\nrsan\hallam-private"
```

`setx` does not change the current window. Open a new one, or set `$env:SABRA_PRIVATE_DIR` there too.

With that variable set, and with no other `SABRA_*` path overrides, the engine uses:

| What | Where |
| --- | --- |
| Database | `C:\Users\nrsan\hallam-private\sabra.db` |
| Candidate profiles | `C:\Users\nrsan\hallam-private\candidates` |
| Artifacts, evidence, screenshots | under `C:\Users\nrsan\hallam-private` |

If a pilot database already lives somewhere else, set `SABRA_DATABASE_URL` to that file so this does not start a blank database. Example:

```powershell
$env:SABRA_DATABASE_URL = "sqlite+pysqlite:///C:/Users/nrsan/hallam-private/sabra.db"
```

Dry-run is already the default (`SABRA_AUTOMATION_DRY_RUN` defaults to true). Leave it that way until the last step.

## 4. Load the candidate, then record and verify answers

```powershell
cd C:\Users\nrsan\hallam
uv run sabra init --profile C:\Users\nrsan\hallam-private\candidates\profile.yaml
```

Use the real profile path if the filename differs. Upload the résumé from the dashboard (Overview) if it is not already in that database. The résumé file itself stays outside git.

Sensitive questions stay blank until you record an answer **and** verify it. Recording alone is not enough, and the engine will not invent a value.

```powershell
uv run sabra answers record salary_expectations "Your words here"
uv run sabra answers verify salary_expectations
uv run sabra answers record background_check "Your words here"
uv run sabra answers verify background_check
uv run sabra answers list
```

The same actions are on http://127.0.0.1:8787/answers after `uv run` of the API (step 5). A row is usable on a form only when the list shows it verified.

## 5. Prepare packages and approve a batch

```powershell
uv run uvicorn --app-dir apps/api sabra_api.main:app --host 127.0.0.1 --port 8787
```

Then, in the browser on this PC:

1. http://127.0.0.1:8787/opportunities — prepare a package for each role you want. A package becomes approvable only when it is COMPLETE (verified facts, verified sensitive answers the form needs, no unknown required field).
2. http://127.0.0.1:8787/applications — tick the complete packages and press **Approve selected**.
3. Each approval is stored against that package's content hash. If the package changes later, that approval no longer authorizes submission. Approve again after you have read the new package.

Note each application's id from the package list. `sabra apply` takes that id.

## 6. Dry-run first

Leave `SABRA_AUTOMATION_DRY_RUN` unset or `true`.

```powershell
uv run sabra apply APPLICATION_ID
```

Dry-run fills nothing on a live employer from this command's default path in tests, and on your machine it prepares the approved application without clicking Submit. It does not open a browser. The ledger records the attempt. Read the printed outcome and confirm it is `DRY_RUN`.

## 7. Enable real submission

Both switches are required. `--real` alone is refused while dry-run is still on.

```powershell
$env:SABRA_AUTOMATION_DRY_RUN = "false"
uv run sabra apply APPLICATION_ID --real
```

- No CAPTCHA or bot challenge: the adapter submits, and the ledger records `APPLICATION_SUBMITTED`.
- reCAPTCHA, hCaptcha, Turnstile, a similar challenge, or a probe that fails: nothing is submitted and nothing is solved. A visible Chromium window opens with the pre-filled form. You solve the check and press Submit yourself.
- When the prompt asks, type `submitted`, `abandoned`, or `failed`. That outcome is written to the ledger. A confirmation page can also be detected and recorded as submitted.

Turn dry-run back on when you are done:

```powershell
$env:SABRA_AUTOMATION_DRY_RUN = "true"
```

## What this build will not do

- It will not type an answer you have not verified.
- It will not solve, bypass, or hide a CAPTCHA.
- It will not submit while dry-run is on.
- An unknown required field blocks that one application and names the field.
- Only the first employment block and the first education block are filled. A form that requires a second block stays blocked until that block is handled.
- Login walls and legal attestations still block; they are not handed to the headed browser.
