"""qtrade.strategies -- backward-compatible alias for qtrade.strategy."""
from qtrade.strategy.base import SignalGenerator
StrategyBase = SignalGenerator
StrategyInterface = SignalGenerator
from qtrade.strategy.rule.dual_ma import DualMASignal
from qtrade.strategy.rule.bollinger import BollingerSignal
from qtrade.strategy.rule.breakout import BreakoutSignal
from qtrade.strategy.rule.regime_filter import RegimeFilterSignal
from qtrade.strategy.rule.event_driven import EventDrivenSignal
from qtrade.strategy.rule.regime_v2 import RegimeFilterV2Signal
from qtrade.strategy.rule.event_v2 import EventDrivenV2Signal
from qtrade.strategy.rule.adaptive import AdaptiveSignal
from qtrade.strategy.rule.hybrid import AdaptiveHybridSignal
from qtrade.strategy.rule.bb_rsi import BBRsiSignal
from qtrade.strategy.rule.trend_5d import Trend5DSignal
from qtrade.strategy.rule.pullback_bb_mid import PullbackBBMidSignal
from qtrade.strategy.rule.pullback_deep import PullbackDeepSignal
from qtrade.strategy.rule.pullback_vol import PullbackVolSignal
from qtrade.strategy.rule.pullback_20d import Pullback20DSignal
from qtrade.strategy.registry import register, get_signal_generator, list_strategies

DualMAStrategy = DualMASignal
BollingerStrategy = BollingerSignal
BreakoutStrategy = BreakoutSignal
RegimeFilterStrategy = RegimeFilterSignal
EventDrivenStrategy = EventDrivenSignal
RegimeFilterV2Strategy = RegimeFilterV2Signal
EventDrivenV2Strategy = EventDrivenV2Signal
AdaptiveStrategy = AdaptiveSignal
AdaptiveHybridStrategy = AdaptiveHybridSignal
BBRsiStrategy = BBRsiSignal
Trend5DStrategy = Trend5DSignal
PullbackBBMidStrategy = PullbackBBMidSignal
PullbackDeepStrategy = PullbackDeepSignal
PullbackVolStrategy = PullbackVolSignal
Pullback20DStrategy = Pullback20DSignal

__all__ = [
    "SignalGenerator", "StrategyBase", "StrategyInterface",
    "register", "get_signal_generator", "list_strategies",
    "DualMASignal", "BollingerSignal", "BreakoutSignal",
    "RegimeFilterSignal", "EventDrivenSignal", "RegimeFilterV2Signal",
    "EventDrivenV2Signal", "AdaptiveSignal", "AdaptiveHybridSignal",
    "BBRsiSignal", "Trend5DSignal", "PullbackBBMidSignal",
    "PullbackDeepSignal", "PullbackVolSignal", "Pullback20DSignal",
    "DualMAStrategy", "BollingerStrategy", "BreakoutStrategy",
    "RegimeFilterStrategy", "EventDrivenStrategy", "RegimeFilterV2Strategy",
    "EventDrivenV2Strategy", "AdaptiveStrategy", "AdaptiveHybridStrategy",
    "BBRsiStrategy", "Trend5DStrategy", "PullbackBBMidStrategy",
    "PullbackDeepStrategy", "PullbackVolStrategy", "Pullback20DStrategy",
]
