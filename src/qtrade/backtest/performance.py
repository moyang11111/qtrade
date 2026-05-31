"""Performance metrics calculation — comprehensive metrics with QuantStats integration."""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from qtrade.constants import TRADING_DAYS_PER_YEAR

logger = logging.getLogger("qtrade.backtest.performance")


@dataclass
class BacktestResult:
    """Complete backtest output."""
    metrics: dict = field(default_factory=dict)
    trade_log: list[dict] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    config: dict = field(default_factory=dict)


def calc_metrics(cerebro_result, capital: float, total_days: int) -> dict:
    """Extract comprehensive performance metrics from backtrader results."""
    strat = cerebro_result[0]
    end_value = strat.broker.getvalue()

    total_return = (end_value - capital) / capital * 100
    years = total_days / TRADING_DAYS_PER_YEAR

    if years > 0 and end_value > capital:
        annual_return = ((end_value / capital) ** (1 / years) - 1) * 100
    else:
        annual_return = total_return / years if years > 0 else 0

    sharpe = strat.analyzers.sharpe.get_analysis().get("sharperatio", None)
    dd_analysis = strat.analyzers.drawdown.get_analysis()
    max_dd = dd_analysis.max.drawdown

    # Drawdown duration
    max_dd_len = dd_analysis.get("max", {}).get("len", 0)

    trades_analysis = strat.analyzers.trades.get_analysis()
    total_trades = trades_analysis.get("total", {}).get("total", 0)
    won = trades_analysis.get("won", {}).get("total", 0)
    lost = trades_analysis.get("lost", {}).get("total", 0)
    win_rate = won / total_trades * 100 if total_trades > 0 else 0

    # Profit factor
    gross_profit = trades_analysis.get("won", {}).get("pnl", {}).get("total", 0)
    gross_loss = abs(trades_analysis.get("lost", {}).get("pnl", {}).get("total", 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

    # Average win/loss
    avg_win = trades_analysis.get("won", {}).get("pnl", {}).get("average", 0)
    avg_loss = abs(trades_analysis.get("lost", {}).get("pnl", {}).get("average", 0))
    win_loss_ratio = avg_win / avg_loss if avg_loss > 0 else None

    # Consecutive wins/losses
    consec_won = trades_analysis.get("won", {}).get("consecutive", {})
    consec_lost = trades_analysis.get("lost", {}).get("consecutive", {})
    max_consec_wins = consec_won.get("longest", 0) if consec_won else 0
    max_consec_losses = consec_lost.get("longest", 0) if consec_lost else 0

    return {
        # Returns
        "total_return": round(total_return, 2),
        "annual_return": round(annual_return, 2),
        "final_value": round(end_value, 2),
        "years": round(years, 1),

        # Risk-adjusted
        "sharpe_ratio": round(sharpe, 3) if sharpe else None,
        "max_drawdown": round(max_dd, 2),
        "max_dd_duration": max_dd_len,

        # Trade stats
        "total_trades": total_trades,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor else None,
        "win_loss_ratio": round(win_loss_ratio, 2) if win_loss_ratio else None,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "max_consec_wins": max_consec_wins,
        "max_consec_losses": max_consec_losses,
    }


def calc_extended_metrics(equity_curve: pd.Series, trade_log: list[dict],
                          benchmark: Optional[pd.Series] = None) -> dict:
    """Calculate extended metrics from equity curve and trade log.

    Includes: Sortino, Calmar, Information Ratio, monthly returns, etc.
    """
    if equity_curve.empty:
        return {}

    # Daily returns
    daily_ret = equity_curve.pct_change().dropna()
    if len(daily_ret) < 2:
        return {}

    # Sortino ratio (downside deviation)
    downside = daily_ret[daily_ret < 0]
    downside_std = downside.std() * np.sqrt(TRADING_DAYS_PER_YEAR) if len(downside) > 0 else 0
    ann_return = daily_ret.mean() * TRADING_DAYS_PER_YEAR
    sortino = ann_return / downside_std if downside_std > 0 else None

    # Calmar ratio (annual return / max drawdown)
    cummax = equity_curve.cummax()
    drawdown = (equity_curve - cummax) / cummax
    max_dd = abs(drawdown.min())
    calmar = ann_return / max_dd if max_dd > 0 else None

    # Volatility
    volatility = daily_ret.std() * np.sqrt(TRADING_DAYS_PER_YEAR)

    # Skewness and Kurtosis
    skew = daily_ret.skew()
    kurt = daily_ret.kurtosis()

    # Information ratio (vs benchmark)
    info_ratio = None
    if benchmark is not None and not benchmark.empty:
        bench_ret = benchmark.pct_change().dropna()
        # Align dates
        common = daily_ret.index.intersection(bench_ret.index)
        if len(common) > 0:
            active_ret = daily_ret.loc[common] - bench_ret.loc[common]
            tracking_error = active_ret.std() * np.sqrt(TRADING_DAYS_PER_YEAR)
            if tracking_error > 0:
                info_ratio = active_ret.mean() * TRADING_DAYS_PER_YEAR / tracking_error

    # Monthly returns
    monthly_ret = daily_ret.resample("ME").apply(lambda x: (1 + x).prod() - 1)
    best_month = monthly_ret.max() * 100 if len(monthly_ret) > 0 else None
    worst_month = monthly_ret.min() * 100 if len(monthly_ret) > 0 else None

    # Yearly returns
    yearly_ret = daily_ret.resample("YE").apply(lambda x: (1 + x).prod() - 1)
    yearly_dict = {str(d.year): round(v * 100, 2) for d, v in yearly_ret.items()}

    # Max drawdown duration (in trading days)
    dd_duration = 0
    current_dd_len = 0
    for i in range(len(drawdown)):
        if drawdown.iloc[i] < 0:
            current_dd_len += 1
            dd_duration = max(dd_duration, current_dd_len)
        else:
            current_dd_len = 0

    # Recovery time (days from max DD to new high)
    recovery_days = None
    if max_dd > 0:
        dd_end_idx = drawdown.idxmin()
        peak_value = equity_curve[:dd_end_idx].max()
        post_dd = equity_curve[dd_end_idx:]
        recovery_idx = post_dd[post_dd >= peak_value].index
        if len(recovery_idx) > 0:
            recovery_days = (recovery_idx[0] - dd_end_idx).days

    # Trade-level stats from trade_log
    avg_bars = None
    if trade_log:
        bars = [t.get("bars", 0) for t in trade_log]
        avg_bars = np.mean(bars) if bars else None

    return {
        "sortino_ratio": round(sortino, 3) if sortino else None,
        "calmar_ratio": round(calmar, 3) if calmar else None,
        "volatility": round(volatility * 100, 2),
        "skewness": round(skew, 3),
        "kurtosis": round(kurt, 3),
        "information_ratio": round(info_ratio, 3) if info_ratio else None,
        "best_month": round(best_month, 2) if best_month else None,
        "worst_month": round(worst_month, 2) if worst_month else None,
        "yearly_returns": yearly_dict,
        "max_dd_duration_days": dd_duration,
        "recovery_days": recovery_days,
        "avg_holding_bars": round(avg_bars, 1) if avg_bars else None,
    }


def print_report(result: BacktestResult) -> None:
    """Print formatted backtest report."""
    m = result.metrics
    print(f"\n{'=' * 60}")
    print(f"  Backtest Report")
    print(f"{'=' * 60}")
    print(f"  Period:              {m.get('years', 0):.1f} years")
    print(f"  Final Value:         {m['final_value']:>12,.2f}")
    print(f"  Total Return:        {m['total_return']:>+11.2f}%")
    print(f"  Annual Return:       {m['annual_return']:>+11.2f}%")
    print(f"{'─' * 60}")
    print(f"  Sharpe Ratio:        {str(m.get('sharpe_ratio', 'N/A')):>11}")
    print(f"  Max Drawdown:        {m['max_drawdown']:>11.2f}%")
    print(f"  Max DD Duration:     {m.get('max_dd_duration', 0):>11} bars")
    print(f"{'─' * 60}")
    print(f"  Trades:              {m['total_trades']:>11}")
    print(f"  Win Rate:            {m['win_rate']:>10.1f}%")
    pf = m.get('profit_factor')
    print(f"  Profit Factor:       {str(round(pf, 2) if pf else 'N/A'):>11}")
    wl = m.get('win_loss_ratio')
    print(f"  Win/Loss Ratio:      {str(round(wl, 2) if wl else 'N/A'):>11}")
    print(f"  Avg Win:             {m.get('avg_win', 0):>+11,.2f}")
    print(f"  Avg Loss:            {-m.get('avg_loss', 0):>+11,.2f}")
    print(f"  Consec Wins/Losses:  {m.get('max_consec_wins', 0)}/{m.get('max_consec_losses', 0)}")
    print(f"{'=' * 60}")

    # Extended metrics if available
    ext = result.metrics.get("_extended", {})
    if ext:
        print(f"\n{'=' * 60}")
        print(f"  Extended Metrics")
        print(f"{'=' * 60}")
        sr = ext.get('sortino_ratio')
        print(f"  Sortino Ratio:       {str(round(sr, 3) if sr else 'N/A'):>11}")
        cr = ext.get('calmar_ratio')
        print(f"  Calmar Ratio:        {str(round(cr, 3) if cr else 'N/A'):>11}")
        print(f"  Volatility:          {ext.get('volatility', 0):>10.2f}%")
        ir = ext.get('information_ratio')
        print(f"  Information Ratio:   {str(round(ir, 3) if ir else 'N/A'):>11}")
        print(f"  Skewness:            {ext.get('skewness', 0):>11.3f}")
        print(f"  Kurtosis:            {ext.get('kurtosis', 0):>11.3f}")
        print(f"  Best Month:          {ext.get('best_month', 0):>+10.2f}%")
        print(f"  Worst Month:         {ext.get('worst_month', 0):>+10.2f}%")
        rh = ext.get('recovery_days')
        print(f"  Recovery Days:       {str(rh) if rh else 'N/A':>11}")

        yearly = ext.get("yearly_returns", {})
        if yearly:
            print(f"\n  Yearly Returns:")
            for year, ret in sorted(yearly.items()):
                print(f"    {year}:  {ret:>+7.2f}%")
        print(f"{'=' * 60}")
