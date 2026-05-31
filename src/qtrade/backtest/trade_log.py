"""Per-trade log output."""

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("qtrade.backtest.trade_log")


def save_trade_log(trades: list[dict], output_dir: str = "results",
                   filename: str = "trades.csv") -> str | None:
    """Save trade log to CSV."""
    if not trades:
        logger.info("No trades to log")
        return None

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / filename

    df = pd.DataFrame(trades)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("Trade log saved: %s (%d trades)", path, len(df))
    return str(path)


def print_trade_summary(trades: list[dict]) -> None:
    """Print trade summary to console."""
    if not trades:
        print("  No trades executed.")
        return

    df = pd.DataFrame(trades)
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]

    print(f"  Trades: {len(df)}  Wins: {len(wins)}  Losses: {len(losses)}")
    if len(df) > 0:
        print(f"  Avg PnL: {df['pnl'].mean():+,.0f}  "
              f"Max Win: {df['pnl'].max():+,.0f}  "
              f"Max Loss: {df['pnl'].min():+,.0f}")
        print(f"  Avg Bars: {df['bars'].mean():.1f}")
