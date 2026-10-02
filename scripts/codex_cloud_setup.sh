#!/usr/bin/env bash
# Run inside a disposable Codex Linux environment, from any working directory.
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
python_bin="${QTRADE_CLOUD_PYTHON:-python}"

"$python_bin" -c 'import sys; assert sys.version_info >= (3, 10), "QTrade requires Python 3.10+"; print(sys.version)'
node -e 'if (Number(process.versions.node.split(".")[0]) < 20) throw new Error("Cloud setup requires Node.js 20+"); console.log(process.version)'

"$python_bin" -m pip install --index-url https://pypi.org/simple --upgrade pip
# Includes native LightGBM, other ML models, CSV/Arrow support and CI tools.
# PyTorch and brokerage credentials are not required for the current workflow.
"$python_bin" -m pip install --index-url https://pypi.org/simple -e '.[test,data,ml]'
npm --prefix electron ci --registry=https://registry.npmjs.org/ --no-audit --no-fund

bash scripts/codex_cloud_check.sh smoke
