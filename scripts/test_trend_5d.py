"""测试五日趋势策略"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd
from qtrade.backtest.engine import BacktestEngine
from qtrade.strategy.rule.trend_5d import Trend5DSignal
from qtrade.data.fetcher import DataFetcher

# 测试股票
SYMBOLS = [
    "300750",  # 宁德时代
    "600519",  # 贵州茅台
    "000001",  # 平安银行
    "000858",  # 五粮液
    "300033",  # 同花顺
    "002049",  # 紫光国微
    "300394",  # 天孚通信
    "002709",  # 天赐材料
]

# 策略配置
TREND_5D_PARAMS = {
    "ma_period": 5,
    "slope_period": 3,
    "add_thresh": 0.01,  # 回踩 MA5 阈值 1%
}

# 回测配置
BACKTEST_CONFIG = {
    "backtest": {
        "initial_capital": 1000000,
        "commission": 0.0003,
        "slippage": 0.001,
        "stop_loss_pct": 0.0,      # 策略自带止损（跌破MA5）
        "trail_stop_pct": 0.0,     # 策略自带止盈（跌破MA5）
        "take_profit_pct": 0.0,
        "lot_size": 100,
    },
    "position_sizing": {
        "method": "strength",      # 使用 strength 动态仓位
        "fixed_pct": 0.95,
    }
}


def run_single_backtest(df, config):
    """运行单个回测"""
    strategy = Trend5DSignal(TREND_5D_PARAMS)
    df_signals = strategy.generate_signals(df)
    engine = BacktestEngine(config)
    result = engine.run(df_signals)
    return result


def main():
    print("=" * 100)
    print("五日趋势策略回测")
    print("=" * 100)
    print()
    print("策略参数：")
    print(f"  均线周期: {TREND_5D_PARAMS['ma_period']} 日")
    print(f"  斜率周期: {TREND_5D_PARAMS['slope_period']} 日")
    print(f"  加仓阈值: {TREND_5D_PARAMS['add_thresh']*100:.1f}% (回踩MA5距离)")
    print()
    print("交易逻辑：")
    print("  买入: 收盘价站上MA5 + MA5斜率向上 → 首次买入50%仓位")
    print("  加仓: 价格回踩MA5附近（<1%）且未跌破 → 加仓30%")
    print("  卖出: 收盘价跌破MA5 → 清仓")
    print("=" * 100)
    print()

    # 获取数据
    fetcher = DataFetcher({"data": {"source": "pytdx"}})

    all_results = []

    for symbol in SYMBOLS:
        print(f"\n{'='*80}")
        print(f"测试股票: {symbol}")
        print(f"{'='*80}")

        try:
            df = fetcher.fetch(symbol, "2022-01-01", "2024-12-31")
            if df is None or len(df) == 0:
                print(f"  跳过: 无数据")
                continue

            print(f"  数据量: {len(df)} 条K线")

            # 运行回测
            try:
                result = run_single_backtest(df, BACKTEST_CONFIG)
                m = result.metrics

                all_results.append({
                    "symbol": symbol,
                    "total_return": m.get("total_return", 0),
                    "total_trades": m.get("total_trades", 0),
                    "win_rate": m.get("win_rate", 0),
                    "max_drawdown": m.get("max_drawdown", 0),
                    "sharpe_ratio": m.get("sharpe_ratio", 0),
                })

                print(f"\n  回测结果:")
                print(f"    收益: {m.get('total_return', 0):+.2f}%")
                print(f"    交易: {m.get('total_trades', 0)} 笔")
                print(f"    胜率: {m.get('win_rate', 0):.1f}%")
                print(f"    回撤: {m.get('max_drawdown', 0):.1f}%")
                print(f"    夏普: {m.get('sharpe_ratio', 0):+.2f}")

                # 显示交易详情（前10笔）
                if result.trade_log:
                    print(f"\n  交易详情（前10笔）:")
                    for i, trade in enumerate(result.trade_log[:10]):
                        print(f"    {i+1}. {trade['entry_date']} -> {trade['exit_date']}")
                        print(f"       入场: {trade['entry_price']:.2f}, 出场: {trade['exit_price']:.2f}")
                        print(f"       盈亏: {trade['pnl_pct']:+.2f}% ({trade['pnl']:+.0f})")
                        print(f"       持仓: {trade.get('bars', 'N/A')} 根K线")

            except Exception as e:
                print(f"  ERROR: {str(e)[:100]}")
                import traceback
                traceback.print_exc()

        except Exception as e:
            print(f"  跳过: {str(e)[:100]}")

    # 汇总
    print(f"\n\n{'='*100}")
    print("汇总结果")
    print(f"{'='*100}")

    if all_results:
        df_results = pd.DataFrame(all_results)

        print(f"\n{'股票':<10} {'收益':>10} {'交易次数':>10} {'胜率':>10} {'回撤':>10} {'夏普':>10}")
        print("-" * 60)

        for _, row in df_results.iterrows():
            print(f"{row['symbol']:<10} {row['total_return']:>+9.2f}% {row['total_trades']:>10d} "
                  f"{row['win_rate']:>9.1f}% {row['max_drawdown']:>9.1f}% {row['sharpe_ratio']:>+9.2f}")

        print("-" * 60)
        print(f"{'平均':<10} {df_results['total_return'].mean():>+9.2f}% {df_results['total_trades'].mean():>10.1f} "
              f"{df_results['win_rate'].mean():>9.1f}% {df_results['max_drawdown'].mean():>9.1f}% "
              f"{df_results['sharpe_ratio'].mean():>+9.2f}")

        print(f"\n\n详细数据已保存: results/trend_5d_results.csv")

        # 保存结果
        os.makedirs("results", exist_ok=True)
        df_results.to_csv("results/trend_5d_results.csv", index=False)

    print("\n" + "=" * 100)
    print("测试完成")
    print("=" * 100)


if __name__ == "__main__":
    main()
