import pytest
from qtrade.config import DEFAULTS, load_config


def test_load_config_merges_defaults():
    cfg = load_config("configs/quick.yaml")
    assert cfg["data"]["symbol"] == "300750"
    assert cfg["backtest"]["initial_capital"] == 100000
    assert cfg["strategy"]["name"] == "dual_ma"


def test_load_config_does_not_mutate_global_defaults():
    before = DEFAULTS["output"]["save_results"]
    cfg = load_config("configs/quick.yaml")
    cfg["output"]["save_results"] = not before
    assert DEFAULTS["output"]["save_results"] == before


def test_load_config_missing_file():
    with pytest.raises(FileNotFoundError):
        load_config("configs/does_not_exist.yaml")


def test_load_config_rejects_bad_capital(tmp_path):
    import yaml
    p = tmp_path / "bad.yaml"
    p.write_text("backtest:\n  initial_capital: 0\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(str(p))
