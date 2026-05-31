"""
使用 a-stock-data skill 数据源进行数据驱动回测
1. 使用备选中证500多行业代表股
2. 拉取资金流向筛选强势股
3. 下载K线数据（pytdx）
4. 运行全部8个策略
5. 分熊市/牛市分析
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd
import numpy as np
import requests
import time
from pathlib import Path

from qtrade import DataFetcher, BacktestEngine
from qtrade.strategy import (
    DualMASignal, BollingerSignal, BreakoutSignal,
    RegimeFilterSignal, EventDrivenSignal,
    RegimeFilterV2Signal, EventDrivenV2Signal, AdaptiveSignal,
)

# 中证500多行业代表股（排除白酒消费）
SYMBOLS = {
    # 科技
    "300033": "同花顺",
    "002049": "紫光国微",
    "002371": "北方华创",
    "300394": "天孚通信",
    "603986": "兆易创新",
    # 新能源
    "300750": "宁德时代",
    "601012": "隆基绿能",
    "300274": "阳光电源",
    "002709": "天赐材料",
    "300014": "亿纬锂能",
    # 医药
    "300760": "迈瑞医疗",
    "300347": "泰格医药",
    "300122": "智飞生物",
    "002007": "华兰生物",
    # 制造/半导体
    "002812": "恩捷股份",
    "688036": "传音控股",
    "002463": "沪电股份",
    "603501": "韦尔股份",
    # 金融
    "601688": "华泰证券",
    "600030": "中信证券",
}

# 策略配置
STRATEGIES = {
    "dual_ma": {
        "class": DualMASignal,
        "params": {"fast_period": 5, "slow_period": 20},
    },
    "bollinger": {
        "class": BollingerSignal,
        "params": {"period": 20, "std_mult": 2.0},
    },
    "breakout": {
        "class": BreakoutSignal,
        "params": {"entry_period": 20, "exit_period": 10},
    },
    "regime_filter": {
        "class": RegimeFilterSignal,
        "params": {
            "ma_short": 20, "ma_long": 60, "ma_very_long": 120,
            "bull_boost": 1.2, "bear_reduce": 0.5, "sideways_reduce": 0.7,
        },
    },
    "event_driven": {
        "class": EventDrivenSignal,
        "params": {
            "vol_surge_period": 20, "vol_surge_threshold": 2.0,
            "gap_threshold": 0.03, "fund_flow_confirm": True,
            "vol_ratio_confirm": True, "lookback_days": 5,
        },
    },
    "regime_v2": {
        "class": RegimeFilterV2Signal,
        "params": {"fast_ma": 10, "slow_ma": 30, "bull_threshold": 0.6, "bear_threshold": 0.4},
    },
    "event_v2": {
        "class": EventDrivenV2Signal,
        "params": {"vol_window": 20, "vol_threshold": 1.8, "min_gap": 0.02},
    },
    "adaptive": {
        "class": AdaptiveSignal,
        "params": {"vol_window": 20, "vol_threshold_high": 1.5, "vol_threshold_low": 0.7, "trend_window": 30},
    },
}

# 时间段
BEAR_CUTOFF = pd.Timestamp("2024-09-24")
BEAR_START = "20220101"
BULL_END = "20251231"

BT_CONFIG = {
    "backtest": {
        "initial_capital": 1000000,
        "commission": 0.0003,
        "min_commission": 5.0,
        "slippage": 0.001,
        "lot_size": 100,
        "stop_loss_pct": 0.10,
        "trail_stop_pct": 0.08,
    }
}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def get_fund_flow_120d(code):
    """获取个股120日资金流（东财push2his）"""
    market_code = 1 if code.startswith("6") else 0
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "secid": f"{market_code}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "lmt": "120",
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
        "Origin": "https://quote.eastmoney.com",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        d = r.json()
        klines = d.get("data", {}).get("klines", [])
        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) >= 7:
                rows.append({
                    "date": parts[0],
                    "main_net": float(parts[1]) if parts[1] != "-" else 0,
                    "super_net": float(parts[5]) if parts[5] != "-" else 0,
                })
        return rows
    except Exception as e:
        return []


def get_industry_ranking():
    """获取行业板块排名（东财）"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f13,f14,f104,f105",
    }
    headers = {"User-Agent": UA}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        d = r.json()
        items = d.get("data", {}).get("diff", [])
        rows = []
        for item in items:
            rows.append({
                "name": item.get("f14", ""),
                "change_pct": item.get("f3", 0),
                "up_count": item.get("f104", 0),
                "down_count": item.get("f105", 0),
            })
        return sorted(rows, key=lambda x: x["change_pct"], reverse=True)
    except:
        return []


def run_single(strategy_cls, params, df):
    """运行单个策略回测"""
    cfg = {"name": params.get("name", strategy_cls.__name__)}
    cfg.update(params)
    strategy = strategy_cls(cfg)
    try:
        df_sig = strategy.generate_signals(df)
        engine = BacktestEngine(BT_CONFIG)
        result = engine.run(df_sig)
        return result.metrics
    except Exception as e:
        return {"error": str(e)}


def main():
    print("=" * 80)
    print("A股数据驱动回测（使用 a-stock-data skill）")
    print("=" * 80)

    # 1. 拉取行业排名
    print("\n[1/5] 获取行业板块排名...")
    industries = get_industry_ranking()
    if industries:
        print("  TOP 10 涨幅行业:")
        for ind in industries[:10]:
            print(f"    {ind['name']}: {ind['change_pct']}% 涨{ind['up_count']}跌{ind['down_count']}")

    # 2. 拉取资金流
    print("\n[2/5] 获取120日资金流...")
    fund_data = {}
    for code, name in SYMBOLS.items():
        flow = get_fund_flow_120d(code)
        if flow:
            recent_20 = flow[-20:]
            total_main = sum(d["main_net"] for d in recent_20)
            total_super = sum(d["super_net"] for d in recent_20)
            fund_data[code] = {
                "main_net_20d": total_main,
                "super_net_20d": total_super,
            }
            print(f"  {code} {name}: 主力净流入={total_main/1e8:+.2f}亿")
        else:
            print(f"  {code} {name}: 无数据")
        time.sleep(0.2)

    # 3. 按资金流排名
    if fund_data:
        fund_df = pd.DataFrame(fund_data).T
        fund_df = fund_df.sort_values("main_net_20d", ascending=False)
        print(f"\n  资金流 TOP 10:")
        for code, row in fund_df.head(10).iterrows():
            name = SYMBOLS.get(code, "")
            print(f"    {code} {name}: 主力={row['main_net_20d']/1e8:+.2f}亿")

    # 4. 下载K线并运行回测
    print("\n[3/5] 获取K线数据并运行回测...")
    fetcher = DataFetcher({"data": {}})
    all_results = []

    for code, name in SYMBOLS.items():
        print(f"\n  === {code} {name} ===")
        try:
            df_full = fetcher.fetch(symbol=code, start=BEAR_START, end=BULL_END)
            print(f"  数据: {len(df_full)} bars")
        except Exception as e:
            print(f"  [SKIP] {e}")
            continue

        # Buy & Hold
        bh_bear = (df_full[df_full.index < BEAR_CUTOFF]["close"].iloc[-1] /
                   df_full[df_full.index < BEAR_CUTOFF]["close"].iloc[0] - 1) * 100 if len(df_full[df_full.index < BEAR_CUTOFF]) > 1 else 0
        bh_bull = (df_full[df_full.index >= BEAR_CUTOFF]["close"].iloc[-1] /
                   df_full[df_full.index >= BEAR_CUTOFF]["close"].iloc[0] - 1) * 100 if len(df_full[df_full.index >= BEAR_CUTOFF]) > 1 else 0

        print(f"  Buy&Hold: bear={bh_bear:+.1f}% bull={bh_bull:+.1f}%")

        # 资金流数据
        fund_info = fund_data.get(code, {})
        main_net = fund_info.get("main_net_20d", 0)

        # 分割熊市/牛市
        df_bear = df_full[df_full.index < BEAR_CUTOFF]
        df_bull = df_full[df_full.index >= BEAR_CUTOFF]

        for strat_name, strat_info in STRATEGIES.items():
            params = strat_info["params"].copy()
            params["name"] = strat_name

            for period_name, df_period in [("bear", df_bear), ("bull", df_bull), ("full", df_full)]:
                if len(df_period) < 50:
                    continue

                metrics = run_single(strat_info["class"], params, df_period)

                row = {
                    "symbol": code,
                    "name": name,
                    "strategy": strat_name,
                    "period": period_name,
                    "main_net_20d": main_net,
                }
                row.update(metrics)
                all_results.append(row)

                ret = metrics.get("total_return", "ERR")
                trades = metrics.get("total_trades", 0)
                if isinstance(ret, (int, float)):
                    print(f"    [{period_name:>4}] {strat_name:<16}  ret={ret:+7.2f}%  trades={trades}")
                else:
                    print(f"    [{period_name:>4}] {strat_name:<16}  ERROR")

    # 5. 分析结果
    print(f"\n{'='*80}")
    print("  结果分析")
    print(f"{'='*80}")

    results_df = pd.DataFrame(all_results)
    numeric = results_df.copy()
    for col in ["total_return", "sharpe_ratio", "max_drawdown", "total_trades", "win_rate", "main_net_20d"]:
        numeric[col] = pd.to_numeric(numeric[col], errors="coerce")

    valid = numeric.dropna(subset=["total_return"])
    valid_trades = valid[valid["total_trades"] > 0]

    for period in ["bear", "bull", "full"]:
        print(f"\n{'='*80}")
        print(f"  {period.upper()} 期间")
        print(f"{'='*80}")

        subset = valid_trades[valid_trades["period"] == period]
        if subset.empty:
            print("  无有效数据")
            continue

        # 策略汇总
        summary = subset.groupby("strategy").agg({
            "total_return": ["mean", "median", "std"],
            "sharpe_ratio": "mean",
            "max_drawdown": "mean",
            "win_rate": "mean",
            "total_trades": "mean",
        }).round(2)
        summary = summary.sort_values(("total_return", "mean"), ascending=False)
        print(summary.to_string())

        # 最佳策略
        best_strat = subset.groupby("strategy")["total_return"].mean().idxmax()
        best_ret = subset.groupby("strategy")["total_return"].mean().max()
        print(f"\n  [BEST] 最佳策略: {best_strat} (平均收益: {best_ret:+.2f}%)")

        # 最佳交易
        best_trade = subset.loc[subset["total_return"].idxmax()]
        print(f"\n  [STAR] 最佳交易:")
        print(f"    {best_trade['strategy']} on {best_trade['name']}({best_trade['symbol']})")
        print(f"    收益: {best_trade['total_return']:+.2f}%  Sharpe: {best_trade.get('sharpe_ratio', 'N/A')}")

        # 资金流 vs 收益相关性
        if "main_net_20d" in subset.columns:
            corr = subset["main_net_20d"].corr(subset["total_return"])
            print(f"\n  [CORR] 资金流 vs 收益相关性: {corr:.3f}")
            if abs(corr) > 0.3:
                print(f"    资金流与收益{'正' if corr > 0 else '负'}相关，可用作选股过滤器")
            else:
                print(f"    相关性较弱，资金流不是好的选股指标")

    # 保存结果
    out_dir = Path("results")
    out_dir.mkdir(exist_ok=True)
    results_df.to_csv(out_dir / "fund_flow_driven_results.csv", index=False)
    print(f"\n结果已保存: results/fund_flow_driven_results.csv")


if __name__ == "__main__":
    main()
