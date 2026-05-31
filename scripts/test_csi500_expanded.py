"""
测试中证500成分股 - 扩大样本
覆盖：科技、新能源、医药、制造、网络安全等行业
"""
import sys
import os
from datetime import datetime
import pandas as pd
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / 'src'))

from qtrade.backtest import BacktestEngine
from qtrade.strategy import (
    DualMASignal,
    BollingerSignal,
    BreakoutSignal,
    RegimeFilterSignal,
    RegimeFilterV2Signal,
    EventDrivenSignal,
    EventDrivenV2Signal,
    AdaptiveSignal
)

# 回测配置
BT_CONFIG = {
    "backtest": {
        "initial_capital": 1000000,
        "commission": 0.001,
        "slippage": 0.001,
    }
}

# 中证500成分股 - 15只，多行业覆盖
SYMBOLS = {
    # 科技
    '300033': '同花顺',
    '002049': '紫光国微',
    '688012': '中微公司',

    # 新能源
    '300750': '宁德时代',
    '601012': '隆基绿能',
    '300274': '阳光电源',

    # 医药
    '300760': '迈瑞医疗',
    '300347': '泰格医药',
    '300122': '智飞生物',

    # 制造/半导体
    '002812': '恩捷股份',
    '300394': '天孚通信',
    '002371': '北方华创',

    # 网络安全/软件
    '688561': '奇安信',
    '300454': '深信服',
    '002230': '科大讯飞',
}

# 策略配置
STRATEGIES = {
    'dual_ma': {
        'class': DualMASignal,
        'params': {'fast_period': 10, 'slow_period': 30}
    },
    'bollinger': {
        'class': BollingerSignal,
        'params': {'period': 20, 'std_dev': 2.0}
    },
    'breakout': {
        'class': BreakoutSignal,
        'params': {'period': 20, 'vol_factor': 1.5}
    },
    'regime_filter': {
        'class': RegimeFilterSignal,
        'params': {'fast_ma': 10, 'slow_ma': 50}
    },
    'regime_v2': {
        'class': RegimeFilterV2Signal,
        'params': {'fast_ma': 5, 'slow_ma': 20, 'vol_ma': 20}
    },
    'event_driven': {
        'class': EventDrivenSignal,
        'params': {'vol_window': 20, 'vol_threshold': 2.0, 'gap_threshold': 0.03}
    },
    'event_v2': {
        'class': EventDrivenV2Signal,
        'params': {'vol_window': 20, 'vol_threshold': 1.8, 'min_gap': 0.02}
    },
    'adaptive': {
        'class': AdaptiveSignal,
        'params': {'vol_window': 20, 'vol_threshold_high': 1.5, 'vol_threshold_low': 0.7}
    },
}


def load_data(symbol, start_date='2022-01-01', end_date=None):
    """加载股票数据"""
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')

    cache_file = project_root / 'data' / 'cache' / f'{symbol}.csv'

    if not cache_file.exists():
        print(f'  [WARN] 数据不存在: {cache_file}')
        return None

    try:
        df = pd.read_csv(cache_file, parse_dates=['date'], index_col='date')
        df = df[(df.index >= start_date) & (df.index <= end_date)]

        # 确保列名正确
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in required_cols:
            if col not in df.columns:
                print(f'  [WARN] 缺少列: {col}')
                return None

        return df
    except Exception as e:
        print(f'  [WARN] 加载失败: {e}')
        return None


def run_backtest(df, strategy_class, strategy_params):
    """运行单个回测"""
    try:
        # 创建策略配置
        cfg = {"name": strategy_params.get("name", strategy_class.__name__)}
        cfg.update(strategy_params)

        # 创建策略实例
        strategy = strategy_class(cfg)

        # 生成信号
        df_sig = strategy.generate_signals(df)

        # 运行回测
        engine = BacktestEngine(BT_CONFIG)
        result = engine.run(df_sig)
        return result
    except Exception as e:
        print(f'    [WARN] 回测失败: {e}')
        return None


def main():
    print('='*80)
    print('中证500成分股策略测试 - 扩大样本（15只股票）')
    print('='*80)

    # 时间段划分
    periods = {
        'bear': ('2022-01-01', '2024-09-23'),
        'bull': ('2024-09-24', '2025-12-31'),
        'full': ('2022-01-01', '2025-12-31'),
    }

    all_results = []

    for symbol, name in SYMBOLS.items():
        print(f'\n{"="*80}')
        print(f'测试 {symbol} {name}')
        print(f'{"="*80}')

        # 加载完整数据
        df_full = load_data(symbol, '2022-01-01', '2025-12-31')
        if df_full is None:
            continue

        print(f'  数据范围: {df_full.index[0].date()} ~ {df_full.index[-1].date()}')
        print(f'  数据点数: {len(df_full)}')

        # Buy and Hold
        bh_start = df_full['close'].iloc[0]
        bh_end = df_full['close'].iloc[-1]
        bh_return = (bh_end / bh_start - 1) * 100
        print(f'  Buy & Hold: {bh_return:+.2f}%')

        for period_name, (start_date, end_date) in periods.items():
            print(f'\n  {period_name.upper()} ({start_date} ~ {end_date})')

            df_period = load_data(symbol, start_date, end_date)
            if df_period is None or len(df_period) < 50:
                print(f'    [WARN] 数据不足')
                continue

            # Buy and Hold for period
            bh_start_p = df_period['close'].iloc[0]
            bh_end_p = df_period['close'].iloc[-1]
            bh_return_p = (bh_end_p / bh_start_p - 1) * 100
            print(f'    Buy & Hold: {bh_return_p:+.2f}%')

            for strat_name, strat_config in STRATEGIES.items():
                params = strat_config['params'].copy()
                params['name'] = strat_name

                result = run_backtest(
                    df_period,
                    strat_config['class'],
                    params
                )

                if result is None:
                    continue

                metrics = result.metrics

                print(f'    {strat_name:20s}: {metrics["total_return"]:+7.2f}%  '
                      f'Sharpe: {metrics.get("sharpe_ratio", 0):+.2f}  '
                      f'MaxDD: {metrics["max_drawdown"]:.1f}%  '
                      f'Trades: {metrics["total_trades"]:3d}')

                all_results.append({
                    'symbol': symbol,
                    'name': name,
                    'period': period_name,
                    'strategy': strat_name,
                    'total_return': metrics['total_return'],
                    'sharpe_ratio': metrics.get('sharpe_ratio', 0),
                    'max_drawdown': metrics['max_drawdown'],
                    'win_rate': metrics['win_rate'],
                    'total_trades': metrics['total_trades'],
                    'final_value': metrics['final_value'],
                    'bh_return': bh_return_p,
                })

    # 保存结果
    results_df = pd.DataFrame(all_results)
    output_file = project_root / 'results' / 'csi500_expanded_results.csv'
    output_file.parent.mkdir(exist_ok=True)
    results_df.to_csv(output_file, index=False)
    print(f'\n结果已保存: {output_file}')

    # 分析结果
    print('\n' + '='*80)
    print('结果分析')
    print('='*80)

    for period in ['bear', 'bull', 'full']:
        print(f'\n{period.upper()} 期间:')
        period_df = results_df[results_df['period'] == period]

        if period_df.empty:
            print('  无数据')
            continue

        # 按策略汇总
        print(f'\n  策略表现:')
        strategy_summary = period_df.groupby('strategy').agg({
            'total_return': ['mean', 'median', 'min', 'max', 'count'],
            'sharpe_ratio': 'mean',
            'max_drawdown': 'mean',
            'win_rate': 'mean',
            'total_trades': 'mean',
        }).round(2)

        # 按平均收益排序
        strategy_summary = strategy_summary.sort_values(
            ('total_return', 'mean'), ascending=False
        )

        print(strategy_summary.to_string())

        # 最佳策略
        best_strategy = period_df.groupby('strategy')['total_return'].mean().idxmax()
        best_return = period_df.groupby('strategy')['total_return'].mean().max()
        print(f'\n  [BEST] 最佳策略: {best_strategy} (平均收益: {best_return:+.2f}%)')

        # 最佳交易
        best_trade = period_df.loc[period_df['total_return'].idxmax()]
        print(f'\n  [STAR] 最佳交易: {best_trade["strategy"]} on {best_trade["name"]}')
        print(f'    收益: {best_trade["total_return"]:+.2f}%  '
              f'Sharpe: {best_trade["sharpe_ratio"]:+.2f}  '
              f'交易次数: {best_trade["total_trades"]}')

    print('\n' + '='*80)
    print('测试完成！')
    print('='*80)


if __name__ == '__main__':
    main()
