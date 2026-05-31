"""Backtrader analyzers — Sharpe, DrawDown, Returns, TradeAnalyzer."""

import backtrader as bt


def add_standard_analyzers(cerebro: bt.Cerebro) -> None:
    """Add standard performance analyzers to a Cerebro instance."""
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe",
                        riskfreerate=0.03, annualize=True)
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
