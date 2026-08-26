"""Contracts for packaging the app's daily update scheduler script."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEDULER_SOURCE = PROJECT_ROOT / "scripts" / "daily_update_1830.py"
PACKAGE_JSON = PROJECT_ROOT / "electron" / "package.json"


def _package_config() -> dict:
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))


def test_daily_scheduler_is_a_single_filtered_extra_resource() -> None:
    resources = _package_config()["build"]["extraResources"]
    scheduler_entries = [
        entry for entry in resources if entry.get("to") == "qtrade/scripts/daily_update_1830.py"
    ]

    assert scheduler_entries == [
        {
            "from": "../scripts/daily_update_1830.py",
            "to": "qtrade/scripts/daily_update_1830.py",
            "filter": [
                "**/*.py",
                "!**/__pycache__/**",
                "!**/*.pyc",
            ],
        }
    ]
    assert not any(
        entry.get("from") == "../scripts" for entry in resources
    )


def test_packaged_scheduler_dry_run_uses_isolated_deck_without_updates(tmp_path: Path) -> None:
    packaged_root = tmp_path / "resources" / "qtrade"
    packaged_script = packaged_root / "scripts" / SCHEDULER_SOURCE.name
    packaged_script.parent.mkdir(parents=True)
    shutil.copyfile(SCHEDULER_SOURCE, packaged_script)
    deck = tmp_path / "fake-deck"
    deck.mkdir()
    status_file = tmp_path / "status.json"
    command = [
        sys.executable,
        str(packaged_script),
        "--dry-run",
        "--force",
        "--date",
        "2026-08-26",
        "--deck-dir",
        str(deck),
        "--status-file",
        str(status_file),
    ]
    environment = os.environ.copy()
    environment.update(
        {
            "QTRADE_DECK_DIR": str(tmp_path / "ignored-deck"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )

    result = subprocess.run(
        command,
        cwd=packaged_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "DRY" in result.stdout
    assert status_file.exists()
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["state"] == "skip"
    assert status["reason"] == "dry_run"
    assert status["outputs"] == {
        "portal": False,
        "decision": False,
        "factors": False,
        "sync": False,
    }
    assert not (deck / "logs").exists()
