# ARTEMIS — Deployment Plan (V1)

**Target:** one small always-on Linux VPS serving a single operator.
**Philosophy:** the smallest architecture that satisfies the master prompt —
durable storage, HTTPS, auth, health monitoring, backups, safe rollback — with
no rewrite. The existing FastAPI application deploys as-is.

## 1. Recommended architecture

```
                 ┌────────────────────────────────────────────┐
   operator ───▶ │  Caddy (TLS termination, HTTPS redirect,   │
   (browser)     │  rate limiting, static compression)        │
                 └──────────────┬─────────────────────────────┘
                                │ 127.0.0.1:8787
                 ┌──────────────▼─────────────────────────────┐
                 │  uvicorn (single worker)                   │
                 │  FastAPI app + sabra engine (this repo)    │
                 │  Playwright Chromium co-located (V1)       │
                 └──────────────┬─────────────────────────────┘
                                │
                 ┌──────────────▼─────────────────────────────┐
                 │  SQLite file at /var/lib/artemis/sabra.db  │
                 │  (WAL mode; busy_timeout; FK enforced)     │
                 └──────────────┬─────────────────────────────┘
                                │ nightly (cron + litestream or
                                │ scp/s3 sync of the verified
                                │ `sabra db backup` snapshots)
                 ┌──────────────▼─────────────────────────────┘
                 │  Off-site backup storage                  │
                 └───────────────────────────────────────────┘
```

Why not split the Playwright worker yet: V1 volume is a handful of supervised
submissions per week. Co-location keeps the human-handoff (headed browser,
CAPTCHA) on one machine. When volume or isolation demands it, split behind the
same SQLite file on a shared volume or move to PostgreSQL — the schema is
already portable (SQLAlchemy core, no raw-SQL dependencies outside migrations).

Why not Cloudflare Workers: the runtime is CPython + Playwright + SQLite; a
Workers port would be the prohibited rewrite.

## 2. Required infrastructure

- 1 VPS: 1 vCPU / 1–2 GB RAM / 25 GB SSD (Ubuntu 24.04 LTS or Debian 12).
- DNS A record, e.g. `artemis.example.com`.
- Off-site backup destination (S3-compatible bucket or another host).
- A systemd unit (below). Nothing else.

## 3. Environment variable inventory

| Variable | Required | Purpose |
|----------|----------|---------|
| `SABRA_API_TOKEN` | **yes (hosted)** | Bearer token + browser cookie secret. Generate: `openssl rand -hex 32`. Never commit. |
| `SABRA_DATABASE_URL` | yes | `sqlite+pysqlite:////var/lib/artemis/sabra.db` |
| `SABRA_DATA_DIR` | yes | `/var/lib/artemis` |
| `SABRA_PRIVATE_DIR` | no | Root that redirects `data_dir`/artifacts/evidence outside the checkout |
| `SABRA_AUTOMATION_DRY_RUN` | yes | `true` until the owner intentionally arms real submissions |
| `SABRA_API_HOST` | no | Default `127.0.0.1` — keep it; Caddy fronts it |
| `SABRA_API_PORT` | no | Default `8787` |
| `SABRA_LLM_PROVIDER` / `SABRA_LLM_API_KEY` | no | Only if a model is wired in; every feature degrades without one |
| `SABRA_SOURCE_REGISTRY_PATH` | no | Default `config/sources.yaml` |

## 4. Deployment configuration

```ini
# /etc/systemd/system/artemis.service
[Unit]
Description=Artemis job-search operator
After=network.target

[Service]
User=artemis
WorkingDirectory=/opt/artemis
EnvironmentFile=/etc/artemis/artemis.env
ExecStart=/opt/artemis/.venv/bin/uvicorn sabra_api.main:app --host 127.0.0.1 --port 8787
Restart=on-failure
RestartSec=5
# Hardening
ProtectSystem=strict
ReadWritePaths=/var/lib/artemis
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

Caddyfile:

```
artemis.example.com {
    reverse_proxy 127.0.0.1:8787
    # rate limit bursts at the edge (in-app auth remains the authority)
    @rate limit { zone artemis 10r/m burst 20 }
    handle @rate { respond 429 }
}
```

Install: `uv sync --all-extras --frozen` on the server (or build a wheel and
pip-install), `playwright install chromium`, `sudo systemctl enable --now artemis`.

## 5. Database migration procedure

```bash
sudo -u artemis /opt/artemis/.venv/bin/sabra db status     # what is applied/pending
sudo -u artemis /opt/artemis/.venv/bin/sabra db migrate    # auto-backup, then apply
```

`migrate` snapshots the SQLite file into `<data_dir>/backups/` (verified
`VACUUM INTO`) before touching anything. PostgreSQL, when adopted, is dumped
with `pg_dump` before migration instead (the tool refuses to auto-backup it).

## 6. Backup / recovery procedure

- Nightly: `sabra db backup` via cron + ship `/var/lib/artemis/backups/` off-site.
- Recovery on a fresh host: install, place the latest verified backup at the
  path, `sabra db restore --from <backup>`, start the service.
- Full procedure and failure modes: see `RECOVERY_RUNBOOK.md`.

## 7. Staging smoke tests (run after every deploy)

1. `GET /health` → 200 (unauthenticated).
2. `GET /api/status` without token → 401; with `Authorization: Bearer $TOKEN` → 200.
3. `GET /?token=$TOKEN` → 303 + cookie; follow cookie → 200 Home page.
4. `GET /` in a private window → 401 sign-in page.
5. `sabra db status` → `migrations_needed: false`.
6. `sabra run` (dry-run on) → completes; Home shows the run; no submission occurs.
7. Upload a scratch résumé on `/candidate`; confirm it appears; discard.

## 8. Production cutover checklist

- [ ] VPS provisioned; SSH hardened (key-only); automatic security updates on.
- [ ] DNS A record live; Caddy obtains a real certificate.
- [ ] `/etc/artemis/artemis.env` with a fresh `SABRA_API_TOKEN` (never reused elsewhere).
- [ ] `SABRA_AUTOMATION_DRY_RUN=true` confirmed in Settings page.
- [ ] Owner's pilot database migrated: copy `sabra.db` to the server, run `sabra db migrate` (auto-backup), spot-check Home, Pipeline, Candidate.
- [ ] Nightly backup cron installed and one restore rehearsed onto a scratch dir.
- [ ] Old local dashboard stopped (or kept as a hot spare pointed at a copy, not the live file).

## 9. Rollback procedure

Application rollback: `git checkout <previous-tag>` on the server, `uv sync
--all-extras --frozen`, `systemctl restart artemis`. If a migration accompanied
the deploy, also run `sabra db restore --from <pre-migrate backup>` (stop the
service first) — schema and code must roll back together.

## 10. Estimated recurring cost

| Item | Estimate (USD/mo) |
|------|-------------------|
| VPS (1 vCPU / 2 GB) | $6–12 |
| Backups (S3-compatible, ~1 GB churn) | $1–2 |
| DNS | $0–1 |
| **Total** | **≈ $8–15/mo** |
