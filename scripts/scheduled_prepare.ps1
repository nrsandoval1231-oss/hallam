# Scheduled-task wrapper: discover, rank and prepare up to 3 packages.
# Loads the private environment (database, source registry) before running.
. C:\Users\nrsan\hallam-private\hallam-env.ps1
Set-Location C:\Users\nrsan\Nucleus\02_Projects\Hallam
uv run python scripts\daily_run.py prepare --limit 3
