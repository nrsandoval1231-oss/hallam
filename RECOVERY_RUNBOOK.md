# ARTEMIS — Recovery Runbook

Operator procedures for the failure modes that matter. Every command assumes
the service user and venv of DEPLOYMENT_PLAN.md (locally: just `uv run sabra`).

## R1 — Nightly backup (prevention)

```bash
sabra db backup
```

Writes a verified, timestamped snapshot to `<data_dir>/backups/`
(`VACUUM INTO`, then the copy is opened and table-counted before being
accepted). Ship the directory off-site with your normal tooling.

## R2 — Restore from backup

```bash
systemctl stop artemis          # locally: close the dashboard window
sabra db restore --from /path/to/sabra-YYYYMMDD-HHMMSS.db
systemctl start artemis
```

- The backup is open-verified before anything is touched; garbage files are
  refused.
- Orphaned `-wal`/`-shm` siblings are removed first; if one is locked, the
  command names the process holding it instead of corrupting the file.
- Sanity check afterwards: `sabra db status` (expect `migrations_needed:
  false`), open Home, check Pipeline counts against expectations.

## R3 — Schema migration (upgrade)

```bash
sabra db status     # read: current / latest / pending / unknown
sabra db migrate    # verified backup first, then pending migrations in order
```

Failure behavior: the failing migration rolls back; earlier ones stay applied;
the tool exits non-zero with the migration name and error. Downgrade protection:
if the database carries a version name this build does not recognize,
`migrate` refuses with a clear message instead of guessing.

## R4 — "The database is locked"

Another Artemis process holds the SQLite write lock (a CLI run while the
dashboard is open, or two dashboards). The API already answers 503 with this
explanation. Wait for the holder to finish; if it is hung, stop it. The engine
uses `BEGIN IMMEDIATE` + a 30 s busy timeout, so this is a queue, not corruption.

## R5 — A submission is stuck "Applying"

1. Open `/applications` — the row polls its handoff status every 3 s.
2. If a browser window is open: finish the human step (solve the check,
   submit or abandon) and press the matching button.
3. If nothing is open (crashed process): the row offers **Force a report**
   after the handoff timeout, fail-closed — you must type the application id,
   and it is recorded as forced. Nothing is ever marked submitted unseen.

## R6 — Server stopped mid-handoff

On shutdown the app records `HANDED_TO_HUMAN / server_stopped` for every
in-flight application, so the row is explainable afterwards and R5 applies.
If the machine died hard (power loss), the same forced-report path covers it.

## R7 — Wrong or unverified answer typed nowhere

Unverified answers are never typed onto forms (truth gate). To fix a bad
recorded answer: `/answers` → correct the value → **Verify** it → on the
application, *Refresh package* → approve the new revision (the old approval
died with the package hash).

## R8 — Full disaster recovery (new machine)

1. Provision per DEPLOYMENT_PLAN §4.
2. Copy the latest verified backup to the new host.
3. `sabra db restore --from <backup>`; `sabra db status`.
4. Start the service; run staging smoke tests §7.
5. Re-create `SABRA_API_TOKEN` (it is a secret, not data — rotate on incident).

## What is NOT recoverable by design

- Approvals: an approval binds one package hash; restoring an old database
  reinstates old approvals, and refreshing any package revokes them. This is
  the safety property working, not data loss.
- Ledger events and outcomes are append-only; no procedure here deletes them.
