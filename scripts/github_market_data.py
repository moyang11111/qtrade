"""Manage private GitHub market snapshots without committing data to Git."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qtrade_adapters.deepseek_harness import github_market_snapshot, snapshot_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "import", "fetch", "collect"))
    parser.add_argument("--user-data", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--repo", default=os.environ.get("QTRADE_DATA_GITHUB_REPO"))
    parser.add_argument("--target-date", default=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
    args = parser.parse_args()
    user_data = args.user_data
    try:
        if args.action in {"export", "import"} and args.bundle is None:
            parser.error("--bundle is required for export/import")
        if args.action == "export":
            result = github_market_snapshot.export_bundle(user_data, args.bundle)
        elif args.action == "import":
            result = {"result": github_market_snapshot.import_bundle(user_data, args.bundle)}
        elif args.action == "fetch":
            token = os.environ.get("QTRADE_DATA_GITHUB_TOKEN", "")
            if not args.repo or not token:
                raise github_market_snapshot.MarketSnapshotError("github_configuration_missing")
            with tempfile.TemporaryDirectory(prefix="qtrade-github-") as temporary:
                bundle = Path(temporary) / "qtrade-market.zip"
                release = github_market_snapshot.download_latest_bundle(args.repo, token, bundle)
                result = {**release, "result": github_market_snapshot.import_bundle(user_data, bundle, expected_sha256=release["sha256"])}
        else:
            if args.bundle is None:
                parser.error("--bundle is required for collect")
            current = snapshot_pipeline.read_current_pipeline(user_data / "state", user_data_dir=user_data)
            if current is None:
                raise github_market_snapshot.MarketSnapshotError("seed_snapshot_required")
            if current.manifest["target_date"] >= args.target_date:
                result = {"result": "already_current", "target_date": current.manifest["target_date"]}
            else:
                status = user_data / "collect-status.json"
                code = snapshot_pipeline.run_snapshot_pipeline(
                    Path.cwd(), args.target_date,
                    user_data_dir=user_data, state_dir=user_data / "state",
                    status_file=status,
                    plan_inputs_builder=snapshot_pipeline.load_current_or_bootstrap_bound_plan_inputs,
                    calendar_loader=snapshot_pipeline.load_trade_calendar_dates,
                )
                outcome = json.loads(status.read_text(encoding="utf-8"))
                if code != 0:
                    raise github_market_snapshot.MarketSnapshotError(str(outcome.get("reason", "collection_failed")))
                if outcome.get("state") == "skip":
                    result = {"result": "market_closed", "target_date": args.target_date}
                else:
                    result = github_market_snapshot.export_bundle(user_data, args.bundle)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (github_market_snapshot.MarketSnapshotError, OSError, ValueError) as exc:
        print(f"market_snapshot_error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
