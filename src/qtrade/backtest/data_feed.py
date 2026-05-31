"""Backtrader data feed with signal columns."""

import backtrader as bt


class SignalPandasData(bt.feeds.PandasData):
    """Extended PandasData that carries signal_action/strength/score."""

    lines = ("signal_action", "signal_strength", "signal_score")
    params = (
        ("signal_action", -1),
        ("signal_strength", -1),
        ("signal_score", -1),
    )
