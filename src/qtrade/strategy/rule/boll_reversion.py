"""Bollinger Reversion Signal — 布林带均值回归策略。

来源：布林带(20, 2σ) 下轨超卖回归经典玩法，参考 FMZ/TradingView
高人气均值回归脚本（Kevin Davey 版）与 vnpy boll_channel 思路的对偶。

规则（多头 + 趋势过滤，防"沿带下行"）：
  入场：收盘跌破下轨 且 长期趋势走多（收盘 > MA60）
  离场：收盘回归中轨（20 日均线）
  趋势过滤确保只在多头市场的超卖中低吸。
"""

import numpy as np
import pandas as pd

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("boll_reversion")
class BollReversionSignal(SignalGenerator):
    """布林带(20, 2) 下轨买入、中轨卖出，MA60 趋势过滤。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.period = config.get("period", 20)
        self.num_std = config.get("num_std", 2.0)
        self.trend_ma = config.get("trend_ma", 60)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        close = result["close"]
        n = len(result)

        mid = close.rolling(self.period).mean()
        std = close.rolling(self.period).std()
        lower = mid - self.num_std * std
        ma_trend = close.rolling(self.trend_ma).mean()

        buy = (close < lower) & (close > ma_trend)
        reach_mid = close >= mid

        action = np.zeros(n, dtype=int)
        holding = False
        for i in range(n):
            if not np.isnan(lower.iloc[i]) and not np.isnan(ma_trend.iloc[i]):
                if not holding and buy.iloc[i]:
                    action[i] = 1
                    holding = True
                elif holding and reach_mid.iloc[i]:
                    action[i] = -1
                    holding = False

        # 超卖深度：低于下轨的 σ 数
        depth = (mid - close) / std.replace(0, np.nan)

        result[SIGNAL_ACTION] = action
        result[SIGNAL_STRENGTH] = np.where(action == 1, np.clip(depth / 2.0, 0.3, 1.0), 0.0)
        result[SIGNAL_SCORE] = np.where(action == 1, np.clip(depth / 2.0, 0, 1), 0.0)

        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0).astype(int)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0.0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0.0)
        return result
