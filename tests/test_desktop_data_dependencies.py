from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only used on Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
AKSHARE_REQUIREMENT = "akshare>=1.10.0"


def _project_metadata() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_akshare_is_a_single_base_runtime_dependency() -> None:
    project = _project_metadata()["project"]
    dependencies = project["dependencies"]
    data_dependencies = project["optional-dependencies"]["data"]

    assert dependencies.count(AKSHARE_REQUIREMENT) == 1
    assert AKSHARE_REQUIREMENT not in data_dependencies
    assert (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines().count(
        AKSHARE_REQUIREMENT
    ) == 1


def test_desktop_docs_describe_the_data_preflight_and_explicit_python_policy() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")

    assert "基础运行依赖包含桌面门户和应用内交易日调度所需的 `akshare>=1.10.0`。" in readme
    assert "窗口创建前会用一次不联网的短预检确认 Python >=3.10 且 `pandas`、`akshare` 可以导入。" in readme
    assert "基础安装已包含桌面门户和应用内交易日调度所需的 `akshare>=1.10.0`；" in quickstart
    assert "窗口创建前会执行一次不联网预检，确认 Python >=3.10 且 `pandas`、`akshare` 可导入。" in quickstart

    for document in (readme, quickstart):
        assert "`QTRADE_PYTHON`" in document
        assert "不会静默" in document
        assert "python -m pip install -e ." in document
