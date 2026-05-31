"""龙虎榜 + 回调布林中轨策略回测。

流程：
  1. 从龙虎榜获取近期"热度高涨幅高"的候选股票池
  2. 批量预下载历史日线数据到本地缓存（可并行）
  3. 使用 PullbackBBMidSignal 策略（强势回调布林中轨买入）
  4. SignalFollower 配置 10% 止盈 / 5% 止损
  5. 汇总输出表格和 CSV 报告
"""

import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# 确保 src/ 在路径中
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from qtrade.data.fetcher import DataFetcher
from qtrade.data.lhb import get_lhb_hot_stocks
from qtrade.strategy.rule.pullback_bb_mid import PullbackBBMidSignal
from qtrade.backtest.engine import BacktestEngine

# ─── 日志 ───
logging.basicConfig(
    level=logging.WARNING,  # 安静模式
    format="%(levelname)-8s [%(name)s] %(message)s",
)
logger = logging.getLogger("lhb_backtest")
logger.setLevel(logging.INFO)

# ─── 配置 ───
DATA_START = "2024-01-01"
DATA_END = datetime.now().strftime("%Y-%m-%d")

# 策略参数
PULLBACK_CONFIG = {
    "bb_period": 20,
    "bb_std": 2.0,
    "ma_short": 5,
    "ma_long": 20,
    "ma60_period": 60,
    "ma60_premium": 0.05,
    "uptrend_lookback": 20,
    "uptrend_min_return": 0.08,    # 20日涨幅 ≥ 8%
    "pullback_peak_lookback": 10,
    "pullback_min_drop": 0.02,     # 从高点回落 ≥ 2%
    "above_mid_lookback": 5,
    "bb_mid_touch_threshold": 0.008,  # 中轨 ±0.8%
    "bb_mid_break_bars": 3,
}

# 回测参数 — 止盈 10% / 止损 5%
BACKTEST_CONFIG = {
    "backtest": {
        "initial_capital": 1_000_000,
        "commission": 0.0003,
        "slippage": 0.001,
        "stop_loss_pct": 0.05,        # ✅ 5% 硬止损
        "trail_stop_pct": 0.0,        # 不启用移动止损
        "take_profit_pct": 0.10,      # ✅ 10% 止盈
        "lot_size": 100,
    },
    "position_sizing": {
        "method": "strength",
        "fixed_pct": 0.95,
        "min_strength": 0.3,
    },
}

# 龙虎榜筛选参数
LHB_DAYS = 30        # 回溯天数
LHB_MIN_RISE = 5     # 上榜日最低涨幅（%）
LHB_MAX_STOCKS = 30   # 最多回测多少只


def backtest_single(symbol: str) -> dict | None:
    """对单只股票执行完整回测。"""
    try:
        # 1. 获取数据
        fetcher = DataFetcher({"data": {
            "source": "akshare",
            "fallback": ["pytdx"],
            "cache": {"type": "csv", "dir": "data/cache", "enabled": True},
        }})
        df = fetcher.fetch(symbol, DATA_START.replace("-", ""), DATA_END.replace("-", ""))

        if df is None or df.empty or len(df) < 120:
            logger.warning("%s: 数据不足 (%d 条)，跳过", symbol, len(df) if df is not None else 0)
            return None

        # 2. 生成信号
        strategy = PullbackBBMidSignal(PULLBACK_CONFIG)
        df_signal = strategy.generate_signals(df)

        buy_count = (df_signal["signal_action"] == 1).sum()
        if buy_count == 0:
            logger.info("%s: 无买入信号，跳过", symbol)
            return None

        # 3. 运行回测
        engine = BacktestEngine(BACKTEST_CONFIG)
        result = engine.run(df_signal)

        m = result.metrics
        return {
            "symbol": symbol,
            "rows": len(df),
            "signals": int(buy_count),
            "trades": m.get("total_trades", 0),
            "win_rate": round(m.get("win_rate", 0), 1),
            "total_return": round(m.get("total_return", 0), 2),
            "annual_return": round(m.get("annual_return", 0), 2),
            "max_drawdown": round(m.get("max_drawdown", 0), 2),
            "sharpe": round(m.get("sharpe_ratio", 0), 2),
            "final_value": round(m.get("final_capital", 0), 0),
        }
    except Exception as e:
        logger.error("%s: 回测异常 — %s", symbol, e)
        return None


def main():
    results_dir = Path(__file__).parent.parent / "results"
    results_dir.mkdir(exist_ok=True)

    # ── Step 1: 获取龙虎榜强股池 ──
    print("=" * 70)
    print("  龙虎榜 + 回调布林中轨策略 回测")
    print("=" * 70)
    print(f"\n📊 获取龙虎榜热度股票...")
    print(f"   参数: {LHB_DAYS} 日内上榜, 涨幅 ≥ {LHB_MIN_RISE}%")
    stocks = get_lhb_hot_stocks(
        days=LHB_DAYS,
        min_rise=LHB_MIN_RISE,
        max_stocks=LHB_MAX_STOCKS,
    )

    if not stocks:
        print("❌ 未获取到龙虎榜股票！")
        return

    print(f"   获取到 {len(stocks)} 只候选股票")
    print(f"   前 10 只: {stocks[:10]}")

    # ── Step 2: 批量预下载到缓存（并行加速） ──
    print(f"\n📥 预下载数据到缓存 ({DATA_START} ~ {DATA_END})...")
    print(f"   并行下载 {len(stocks)} 只股票...")

    fetcher_cfg = {"data": {
        "source": "akshare",            # akshare 比 pytdx 更稳定
        "fallback": ["pytdx"],
        "cache": {"type": "csv", "dir": "data/cache", "enabled": True},
    }}

    def download_one(symbol):
        try:
            f = DataFetcher(fetcher_cfg)
            df = f.fetch(symbol, DATA_START.replace("-", ""), DATA_END.replace("-", ""))
            return symbol, df is not None and len(df) > 0
        except Exception as e:
            return symbol, False

    t0 = time.time()
    ok = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(download_one, s): s for s in stocks}
        for fut in as_completed(futures):
            symbol, success = fut.result()
            if success:
                ok += 1
    elapsed = time.time() - t0
    print(f"   完成: {ok}/{len(stocks)} ({elapsed:.0f}s)\n")

    # ── Step 3: 回测 ──
    print(f"🔄 开始逐只回测...")
    print(f"   策略: PullbackBBMidSignal — 强势回调布林中轨买入")
    print(f"   出场: 止盈 10% / 止损 5%")
    print()

    results = []
    for i, symbol in enumerate(stocks, 1):
        print(f"  [{i:2d}/{len(stocks)}] {symbol} ...", end=" ", flush=True)
        r = backtest_single(symbol)
        if r:
            results.append(r)
            print(f"✔ 收益率={r['total_return']:+.1f}%  Sharpe={r['sharpe']:.2f}  "
                  f"胜率={r['win_rate']:.0f}%  DD={r['max_drawdown']:.1f}%  "
                  f"交易={r['trades']}次")
        else:
            print("✗ 跳过")

    if not results:
        print("\n❌ 所有股票均无有效数据！")
        return

    # ── Step 3: 汇总排序 ──
    results.sort(key=lambda x: x["total_return"], reverse=True)

    print("\n" + "=" * 70)
    print("  📋 回测结果汇总（按收益率降序）")
    print("=" * 70)
    print(f"  {'股票':<10} {'收益率':>8} {'年化':>7} {'Sharpe':>7} {'胜率':>6} "
          f"{'最大回撤':>8} {'交易':>5} {'信号':>5}")
    print(f"  {'─'*10} {'─'*8} {'─'*7} {'─'*7} {'─'*6} {'─'*8} {'─'*5} {'─'*5}")

    winners = 0
    for r in results:
        tag = "⭐" if r["total_return"] > 15 else "  "
        if r["total_return"] > 0:
            winners += 1
        print(f"{tag}{r['symbol']:<10} {r['total_return']:>+7.1f}% {r['annual_return']:>+6.1f}% "
              f"{r['sharpe']:>7.2f} {r['win_rate']:>5.0f}% "
              f"{r['max_drawdown']:>+7.1f}% {r['trades']:>5} {r['signals']:>5}")

    # ── 统计 ──
    n = len(results)
    avg_ret = sum(r["total_return"] for r in results) / n
    avg_sharpe = sum(r["sharpe"] for r in results) / n
    avg_dd = sum(r["max_drawdown"] for r in results) / n

    print(f"\n  📊 统计: {n} 只有效股票 | 盈利 {winners} 只 ({winners/n*100:.0f}%)")
    print(f"  平均收益: {avg_ret:+.1f}% | 平均 Sharpe: {avg_sharpe:.2f} | 平均回撤: {avg_dd:.1f}%")

    # ── Save ──
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    csv_path = results_dir / f"lhb_pullback_{timestamp}.csv"
    json_path = results_dir / f"lhb_pullback_{timestamp}.json"

    import pandas as pd
    pd.DataFrame(results).to_csv(csv_path, index=False)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "config": {
                "lhb_days": LHB_DAYS,
                "lhb_min_rise": LHB_MIN_RISE,
                "data_range": f"{DATA_START} ~ {DATA_END}",
                "strategy": "PullbackBBMidSignal",
                "take_profit": "10%",
                "stop_loss": "5%",
            },
            "summary": {
                "total": n,
                "winners": winners,
                "win_pct": round(winners / n * 100, 1),
                "avg_return": round(avg_ret, 2),
                "avg_sharpe": round(avg_sharpe, 2),
                "avg_max_dd": round(avg_dd, 2),
            },
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n  📁 CSV 报告: {csv_path}")
    print(f"  📁 JSON 报告: {json_path}")
    print("\n✅ 回测完成！")


if __name__ == "__main__":
    main()
