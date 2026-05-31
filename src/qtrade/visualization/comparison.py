"""Multi-strategy comparison visualization."""

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger("qtrade.visualization.comparison")


def plot_strategy_comparison(
    results: dict[str, "BacktestResult"],
    title: str = "Strategy Comparison",
    output_path: Optional[str] = None,
) -> None:
    """Plot side-by-side comparison of multiple strategy results."""
    import matplotlib.pyplot as plt

    if not results:
        logger.warning("No results to compare")
        return

    fig, axes = plt.subplots(2, 2, figsize=(16, 10))

    # 1. Equity curves
    ax = axes[0, 0]
    colors = plt.cm.Set2.colors
    for i, (name, result) in enumerate(results.items()):
        if result.equity_curve.empty:
            continue
        ec = result.equity_curve / result.equity_curve.iloc[0]
        ax.plot(ec.index, ec.values, label=name,
                color=colors[i % len(colors)], linewidth=1.5)
    ax.set_title("Equity Curves (Normalized)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # 2. Drawdown comparison
    ax = axes[0, 1]
    for i, (name, result) in enumerate(results.items()):
        if result.equity_curve.empty:
            continue
        ec = result.equity_curve
        cummax = ec.cummax()
        dd = (ec - cummax) / cummax * 100
        ax.plot(dd.index, dd.values, label=name,
                color=colors[i % len(colors)], linewidth=1, alpha=0.8)
    ax.set_title("Drawdown Comparison")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # 3. Metrics bar chart
    ax = axes[1, 0]
    names = list(results.keys())
    returns = [results[n].metrics.get("total_return", 0) for n in names]
    sharpes = [results[n].metrics.get("sharpe_ratio") or 0 for n in names]
    max_dds = [results[n].metrics.get("max_drawdown", 0) for n in names]

    x = range(len(names))
    width = 0.25
    ax.bar([i - width for i in x], returns, width, label="Return %",
           color="#4CAF50", alpha=0.8)
    ax.bar(x, [s * 10 for s in sharpes], width, label="Sharpe x10",
           color="#2196F3", alpha=0.8)
    ax.bar([i + width for i in x], [-d for d in max_dds], width,
           label="-MaxDD %", color="#F44336", alpha=0.8)
    ax.set_xticks(list(x))
    ax.set_xticklabels(names, fontsize=8, rotation=30, ha="right")
    ax.set_title("Metrics Comparison")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3, axis="y")

    # 4. Summary table
    ax = axes[1, 1]
    ax.axis("off")
    table_data = [["Strategy", "Return", "Sharpe", "MaxDD", "Trades", "WinRate"]]
    for name in names:
        m = results[name].metrics
        table_data.append([
            name[:12],
            f"{m.get('total_return', 0):+.1f}%",
            f"{m.get('sharpe_ratio', 'N/A')}",
            f"{m.get('max_drawdown', 0):.1f}%",
            str(m.get("total_trades", 0)),
            f"{m.get('win_rate', 0):.0f}%",
        ])

    table = ax.table(cellText=table_data[1:], colLabels=table_data[0],
                     loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.5)
    ax.set_title("Summary")

    fig.suptitle(title, fontsize=16, fontweight="bold")
    plt.tight_layout()

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out), dpi=150, bbox_inches="tight")
        logger.info("Comparison chart saved: %s", out)
    else:
        plt.show()

    plt.close(fig)


def comparison_table(results: dict[str, "BacktestResult"]) -> pd.DataFrame:
    """Generate comparison table as DataFrame."""
    rows = []
    for name, result in results.items():
        m = result.metrics
        row = {
            "Strategy": name,
            "Total Return %": m.get("total_return", 0),
            "Annual Return %": m.get("annual_return", 0),
            "Sharpe": m.get("sharpe_ratio"),
            "Max DD %": m.get("max_drawdown", 0),
            "Trades": m.get("total_trades", 0),
            "Win Rate %": m.get("win_rate", 0),
            "Profit Factor": m.get("profit_factor"),
            "Final Value": m.get("final_value", 0),
        }
        rows.append(row)
    return pd.DataFrame(rows).set_index("Strategy")
