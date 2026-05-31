"""QuantStats integration — generate professional HTML reports."""

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("qtrade.backtest.report")


def generate_quantstats_report(
    equity_curve: pd.Series,
    output_path: str = "results/quantstats_report.html",
    benchmark: Optional[pd.Series] = None,
    title: str = "qtrade Strategy Report",
    rf: float = 0.03,
) -> str:
    """Generate a QuantStats HTML report from equity curve.

    Args:
        equity_curve: Series with DatetimeIndex, values are portfolio values.
        output_path: Path for HTML output.
        benchmark: Optional benchmark equity curve for comparison.
        title: Report title.
        rf: Risk-free rate for Sharpe/Sortino calculations.

    Returns:
        Path to generated HTML report.
    """
    try:
        import quantstats as qs
    except ImportError:
        logger.error("quantstats not installed. Run: pip install quantstats")
        raise

    if equity_curve.empty:
        raise ValueError("Empty equity curve")

    # Convert to returns
    returns = equity_curve.pct_change().dropna()
    if returns.empty:
        raise ValueError("Cannot compute returns from equity curve")

    # Ensure DatetimeIndex
    if not isinstance(returns.index, pd.DatetimeIndex):
        returns.index = pd.to_datetime(returns.index)

    # Benchmark returns
    bench_returns = None
    if benchmark is not None and not benchmark.empty:
        bench_returns = benchmark.pct_change().dropna()
        if not isinstance(bench_returns.index, pd.DatetimeIndex):
            bench_returns.index = pd.to_datetime(bench_returns.index)

    # Generate report
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    qs.reports.html(
        returns,
        benchmark=bench_returns,
        output=str(out),
        title=title,
        rf=rf,
    )

    logger.info("QuantStats report saved: %s", out)
    return str(out)


def get_quantstats_metrics(equity_curve: pd.Series, rf: float = 0.03) -> dict:
    """Extract all QuantStats metrics as a dict."""
    try:
        import quantstats as qs
    except ImportError:
        logger.warning("quantstats not available")
        return {}

    if equity_curve.empty:
        return {}

    returns = equity_curve.pct_change().dropna()
    if returns.empty:
        return {}

    metrics = {}
    try:
        metrics["sharpe"] = float(qs.stats.sharpe(returns, rf=rf))
        metrics["sortino"] = float(qs.stats.sortino(returns, rf=rf))
        metrics["max_drawdown"] = float(qs.stats.max_drawdown(returns))
        metrics["calmar"] = float(qs.stats.calmar(returns))
        metrics["volatility"] = float(qs.stats.volatility(returns))
        metrics["cagr"] = float(qs.stats.cagr(returns, rf=rf))
        metrics["skew"] = float(qs.stats.skew(returns))
        metrics["kurtosis"] = float(qs.stats.kurtosis(returns))
        metrics["win_rate"] = float(qs.stats.win_rate(returns))
        metrics["profit_factor"] = float(qs.stats.profit_factor(returns))
        metrics["payoff_ratio"] = float(qs.stats.payoff_ratio(returns))
        metrics["avg_win"] = float(qs.stats.avg_win(returns))
        metrics["avg_loss"] = float(qs.stats.avg_loss(returns))
        metrics["exposure"] = float(qs.stats.exposure(returns))
        metrics["avg_daily_return"] = float(returns.mean())
        metrics["best_day"] = float(returns.max())
        metrics["worst_day"] = float(returns.min())
    except Exception as e:
        logger.warning("Error computing quantstats metrics: %s", e)

    return {k: round(v, 4) if isinstance(v, float) else v for k, v in metrics.items()}


def get_monthly_returns_table(equity_curve: pd.Series) -> pd.DataFrame:
    """Generate monthly returns heatmap table (year x month)."""
    if equity_curve.empty:
        return pd.DataFrame()

    returns = equity_curve.pct_change().dropna()
    monthly = returns.resample("ME").apply(lambda x: (1 + x).prod() - 1)

    # Build year x month table
    table = {}
    for dt, ret in monthly.items():
        year = dt.year
        month = dt.month
        if year not in table:
            table[year] = {}
        table[year][month] = round(ret * 100, 2)

    df = pd.DataFrame(table).T
    df.columns = [f"Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    df.index.name = "Year"
    return df
