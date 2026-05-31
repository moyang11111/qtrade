import pandas as pd
import sys
sys.stdout.reconfigure(encoding='utf-8')

# 读取结果
results = pd.read_csv('results/csi500_expanded_results.csv')

print('='*80)
print('中证500扩大样本测试结果分析')
print('='*80)

print(f'\n测试股票数: {results["symbol"].nunique()}')
print(f'测试策略数: {results["strategy"].nunique()}')
print(f'总测试数: {len(results)}')

# 按时期分析
for period in ['bear', 'bull', 'full']:
    print(f'\n{"="*80}')
    print(f'{period.upper()} 期间分析')
    print(f'{"="*80}')

    period_data = results[results['period'] == period]

    # 按策略汇总
    strategy_summary = period_data.groupby('strategy').agg({
        'total_return': ['mean', 'median', 'std', 'min', 'max'],
        'sharpe_ratio': 'mean',
        'max_drawdown': 'mean',
        'win_rate': 'mean',
        'total_trades': 'mean',
        'symbol': 'count'
    }).round(2)

    strategy_summary.columns = ['avg_return', 'median_return', 'std_return', 'min_return', 'max_return',
                                'avg_sharpe', 'avg_maxdd', 'avg_winrate', 'avg_trades', 'test_count']
    strategy_summary = strategy_summary.sort_values('avg_return', ascending=False)

    print('\n策略表现排名:')
    print(strategy_summary.to_string())

    # 最佳策略
    best_strategy = strategy_summary.index[0]
    best_return = strategy_summary.loc[best_strategy, 'avg_return']
    print(f'\n[BEST] 最佳策略: {best_strategy} (平均收益: {best_return:+.2f}%)')

    # 最佳单笔交易
    best_trade = period_data.loc[period_data['total_return'].idxmax()]
    print(f'\n[STAR] 最佳交易:')
    print(f'  股票: {best_trade["symbol"]} {best_trade["name"]}')
    print(f'  策略: {best_trade["strategy"]}')
    print(f'  收益: {best_trade["total_return"]:+.2f}%')
    print(f'  Sharpe: {best_trade["sharpe_ratio"]:+.2f}')
    print(f'  交易次数: {best_trade["total_trades"]}')

    # 对比 Buy & Hold
    avg_bh = period_data['bh_return'].mean()
    avg_strategy = period_data['total_return'].mean()
    alpha = avg_strategy - avg_bh
    print(f'\n[ALPHA] 策略 vs Buy&Hold:')
    print(f'  Buy&Hold 平均: {avg_bh:+.2f}%')
    print(f'  策略平均: {avg_strategy:+.2f}%')
    print(f'  Alpha: {alpha:+.2f}%')

print('\n' + '='*80)
print('分析完成')
print('='*80)
