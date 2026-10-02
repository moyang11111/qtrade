# QTrade development guidance

## Project and environment

- Python sources live in `src/qtrade/`; the desktop HTTP server is `server.py`.
- Existing UI files live in `static/`, desktop runtime in `electron/`, and the shared paper account implementation in `paper_trading/`.
- Use Python 3.12 and Node.js 20 for the cloud workflow. Install and check with `bash scripts/codex_cloud_setup.sh` in a disposable Linux environment.
- Cloud account settings and usage are documented in `docs/codex-cloud.md`. Committing these scripts does not create or publish a cloud environment.

## Verification

- Run checks relevant to the change. `bash scripts/codex_cloud_check.sh smoke` checks dependencies, tracked Python syntax, the CSV service, next-day probability, paper execution and Electron unit tests.
- Before delivering broad changes, use `bash scripts/codex_cloud_check.sh full` for the full Python suite and package build, plus Electron unit tests.
- If editing Python source, include newly added files in the syntax check; the shared script checks files already tracked by Git.
- The service smoke test creates temporary CSV data and binds a loopback port. Use it for backend startup checks.
- Windows CMD launchers, desktop appearance, NSIS packaging and installed shortcuts need Windows checks. Linux results alone do not establish that the installed Windows application was updated.
- Keep existing validation gates intact. Report failed or skipped checks and the actual reason.

## Data and delivery

- For ordinary code tasks, use generated fixtures and isolated temporary accounts. Fetch live market data or run account operations only when the task requests them.
- Local market-data caches, published snapshots and the installed AppData paper account are not automatically present in a cloud workspace. Do not treat absent or stale data as current data.
- Preserve the user's existing UI and financial account history when modifying related features.
- Keep generated caches, account databases, model artifacts, credentials and local `work/` evidence out of commits.
- Review the source diff and tests before delivery. Use a `codex/` branch for cloud changes, and publish or merge according to the user's request. GitHub changes need local synchronization and installation before they affect the desktop app.
