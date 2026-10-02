#!/usr/bin/env bash
# Deterministic checks: generated fixtures, isolated test accounts, no live fetch.
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
python_bin="${QTRADE_CLOUD_PYTHON:-python}"
mode="${1:-smoke}"
case "$mode" in
  smoke|full) ;;
  *) echo 'Usage: bash scripts/codex_cloud_check.sh [smoke|full]' >&2; exit 2 ;;
esac

"$python_bin" - <<'PY'
import importlib
import sys

assert sys.version_info >= (3, 10), "QTrade requires Python 3.10+"
for name in (
    "qtrade", "pandas", "numpy", "akshare", "pytdx", "pyarrow",
    "lightgbm", "threadpoolctl", "sklearn", "xgboost", "pytest", "ruff", "build",
):
    importlib.import_module(name)
print("Python, project, model and test dependencies are ready.")
PY
"$python_bin" -m pip check

# Check source files without traversing generated datasets, caches or node_modules.
mapfile -d '' -t python_files < <(git ls-files -z -- '*.py')
if ((${#python_files[@]} == 0)); then
  echo 'No tracked Python source files found; run in the QTrade checkout.' >&2
  exit 1
fi
"$python_bin" -m ruff check "${python_files[@]}"
"$python_bin" -m ruff check tests/test_service_smoke.py tests/test_quality_gates.py --select E4,E7,E9,F

if [[ "$mode" == full ]]; then
  "$python_bin" -m pytest -q
  "$python_bin" -m build --wheel --sdist
else
  "$python_bin" -m pytest -q tests/test_quality_gates.py tests/test_service_smoke.py tests/test_next_day_probability.py tests/test_paper_execution.py
fi
npm --prefix electron run lint
npm --prefix electron test
echo "QTrade cloud ${mode} checks passed. Windows packaging still uses electron-quality-gates."
