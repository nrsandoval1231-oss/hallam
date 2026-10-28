# Scheduled-task wrapper: submit up to 3 owner-approved applications.
# `--real` on the command line is the explicit submission switch; dry-run is
# otherwise the standing default. Approvals remain the human gate.
. C:\Users\nrsan\hallam-private\hallam-env.ps1
Set-Location C:\Users\nrsan\Nucleus\02_Projects\Hallam
uv run python scripts\daily_run.py submit --limit 3 --real
