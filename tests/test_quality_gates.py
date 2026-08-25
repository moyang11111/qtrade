"""Checks that the declared quality-gate installation remains self-contained."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10 CI
    import tomli as tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUALITY_FILES = "tests/test_service_smoke.py tests/test_quality_gates.py"
STRICT_RUFF_COMMAND = f"ruff check {QUALITY_FILES} --select E4,E7,E9,F"
DOC_PATHS = (PROJECT_ROOT / "README.md", PROJECT_ROOT / "QUICKSTART.md")


def test_test_extra_declares_quality_gate_dependencies():
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = config["project"]["optional-dependencies"]["test"]

    for name in ("pytest", "pytdx", "ruff", "build"):
        assert any(requirement.lower().startswith(name) for requirement in requirements)


def test_project_urls_point_to_the_repository():
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["urls"] == {
        "Homepage": "https://github.com/moyang11111/qtrade",
        "Documentation": "https://qtrade.readthedocs.io",
        "Repository": "https://github.com/moyang11111/qtrade",
        "Issues": "https://github.com/moyang11111/qtrade/issues",
    }


def test_project_uses_spdx_license_without_license_classifier():
    config = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["license"] == "MIT"
    assert all(not classifier.startswith("License ::") for classifier in config["project"]["classifiers"])


def test_strict_ruff_gate_covers_both_new_quality_tests():
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "quality-gates.yml").read_text(encoding="utf-8")
    assert f"run: {STRICT_RUFF_COMMAND}" in workflow
    for path in DOC_PATHS:
        assert STRICT_RUFF_COMMAND in path.read_text(encoding="utf-8")


def test_documented_base_variables_and_daily_update_priority_are_precise():
    required_lines = (
        "`QTRADE_BASE_DIR` 仅供 `qtrade_base_bridge.py` 使用",
        "`deck/` 子目录的 deepseek-harness-quant 根目录",
        "`QTRADE_DECK_DIR` 仅供 `scripts/daily_update_1830.py` 使用",
        "每日更新路径优先级为 CLI `--deck-dir` > `QTRADE_DECK_DIR` > 项目内默认 `third_party/` 路径。",
        "export QTRADE_BASE_DIR=/path/to/deepseek-harness-quant",
        "export QTRADE_DECK_DIR=/path/to/deepseek-harness-quant",
        "python scripts/daily_update_1830.py --deck-dir /path/to/deepseek-harness-quant --dry-run",
        '$env:QTRADE_BASE_DIR = "D:\\path\\to\\deepseek-harness-quant"',
        '$env:QTRADE_DECK_DIR = "D:\\path\\to\\deepseek-harness-quant"',
    )
    for path in DOC_PATHS:
        text = path.read_text(encoding="utf-8")
        for line in required_lines:
            assert line in text, f"{line!r} missing from {path.name}"
