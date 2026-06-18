"""YAML configuration loader with deep-merge defaults and validation."""

import os
from pathlib import Path
from typing import Any

import yaml


DEFAULTS: dict[str, Any] = {
    "data": {
        "source": "pytdx",
        "fallback": ["akshare"],
        "symbol": "300750",
        "start_date": "20220101",
        "end_date": None,
        "cache": {"enabled": True, "type": "csv", "dir": "data/cache", "auto_refresh": False},
        "bar_type": "daily",
    },
    "backtest": {
        "initial_capital": 100000.0,
        "commission": 0.0003,
        "min_commission": 5.0,
        "commission_type": "percentage",
        "slippage": 0.001,
        "slippage_type": "percentage",
        "stamp_duty": 0.001,
        "stamp_duty_side": "sell",
        "lot_size": 100,
        "t_plus_n": 1,
        "stop_loss_pct": 0.15,
        "trail_stop_pct": 0.10,
        "max_position_pct": 0.95,
    },
    "position_sizing": {
        "method": "strength",
        "fixed_pct": 0.95,
        "min_strength": 0.3,
        "scale_with_strength": True,
        "risk_per_trade": 0.02,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
    },
    "strategy": {"name": "dual_ma", "type": "rule", "params": {}},
    "logging": {"level": "INFO", "file": None, "log_trades": True},
    "output": {"save_results": True, "results_dir": "results", "plot": False,
               "quantstats_report": False},
}


def load_config(path: str | Path) -> dict:
    """Load YAML config, deep-merge with defaults, validate."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        user_cfg = yaml.safe_load(f) or {}

    config = _deep_merge(DEFAULTS, user_cfg)
    _validate(config)
    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base."""
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def _validate(cfg: dict) -> None:
    """Validate critical config values."""
    strat_type = cfg.get("strategy", {}).get("type", "rule")

    if strat_type == "ml":
        ml = cfg.get("ml", {})
        train_end = ml.get("train_end", "")
        bt_start = cfg.get("data", {}).get("start_date", "")
        if train_end and bt_start:
            if train_end >= bt_start:
                raise ValueError(
                    f"ml.train_end ({train_end}) must be before "
                    f"data.start_date ({bt_start}) to prevent lookahead."
                )

    capital = cfg.get("backtest", {}).get("initial_capital", 0)
    if capital <= 0:
        raise ValueError(f"backtest.initial_capital must be positive, got {capital}")


class Config(dict):
    """Dictionary subclass with a from_yaml factory for backward compatibility.

    Usage:
        config = Config.from_yaml("configs/quick.yaml")
        # Behaves exactly like the dict returned by load_config()
    """

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        """Load a YAML config file, returning a Config (dict subclass)."""
        return cls(load_config(path))

    def __repr__(self):
        return f"Config({super().__repr__()})"