"""Backward-compatible interface module ¡ª re-exports SignalGenerator as StrategyInterface."""
from qtrade.strategy.base import SignalGenerator as StrategyInterface

__all__ = ["StrategyInterface"]
