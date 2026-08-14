#!/usr/bin/env python
"""
全市场多因子回测研究
=====================
对样本股票池运行 16 种策略/因子，统一模拟规则（10万本金、0.03%手续费、
止损5%、止盈15%），汇总胜率、交易次数、收益率，找出有用因子。

用法: python research_backtest.py [--sample 500] [--output results.csv]
"""
import io
import sys
import glob
import random
import argparse
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)

import pandas as pd
import numpy as np

# ============================================================================
# 指标
# ============================================================================

def sma(close, p): return close.rolling(p).mean()
def ema(close, s): return close.ewm(span=s, adjust=False).mean()

def macd(close, fast=12, slow=26, signal=9):
    m = ema(close, fast) - ema(close, slow)
    return m, m.ewm(span=signal, adjust=False).mean()

def rsi(close, period=14):
    d = close.diff()
    g = d.clip(lower=0)
    l = (-d).clip(lower=0)
    ag = g.ewm(alpha=1/period, adjust=False).mean()
    al = l.ewm(alpha=1/period, adjust=False).mean()
    return 100 - 100 / (1 + ag / al.replace(0, np.nan))

def boll(close, period=20, std=2):
    ma = close.rolling(period).mean()
    s = close.rolling(period).std()
    return ma + std*s, ma, ma - std*s

# ============================================================================
# 策略信号生成器：输入 df，输出 signals(-1/0/1)
# ============================================================================

def sig_dual_ma(df):
    c = df['close']; s = pd.Series(0, index=df.index)
    m5, m20 = sma(c,5), sma(c,20)
    s[m5 > m20] = 1; s[m5 < m20] = -1
    return s

def sig_trend_5d(df):
    c = df['close']; s = pd.Series(0, index=df.index)
    m5, m10 = sma(c,5), sma(c,10)
    s[(m5 > m10) & (c > m5)] = 1
    s[(m5 < m10) | (c < m5)] = -1
    return s

def sig_ma60_trend(df):
    c = df['close']; s = pd.Series(0, index=df.index)
    m60 = sma(c, 60)
    s[c > m60] = 1; s[c < m60] = -1
    return s

def sig_bollinger(df):
    c = df['close']; s = pd.Series(0, index=df.index)
    u, m, l = boll(c)
    s[c < l] = 1; s[c > u] = -1
    return s

def sig_rsi_reversion(df):
    c = df['close']; s = pd.Series(0, index=df.index)
    r = rsi(c)
    s[r < 30] = 1; s[r > 70] = -1
    return s

def sig_rsi_extreme(df):
    c = df['close']; s = pd.Series(0, index=df.index)
    r = rsi(c)
    s[r < 20] = 1; s[r > 60] = -1
    return s

def sig_pullback_20d(df):
    """缩量回调：距60日高回落15-40% + 缩量 + 站上MA60，持有20日。"""
    c, h, v = df['close'], df['high'], df['volume']
    s = pd.Series(0, index=df.index)
    h60 = h.rolling(60).max()
    pb = c / h60 - 1
    v5, v20 = v.rolling(5).mean(), v.rolling(20).mean()
    m60 = sma(c, 60)
    cond = (pb < -0.15) & (pb > -0.40) & (v5 / v20 < 0.7) & (c > m60)
    s[cond] = 1
    for idx in s[s == 1].index:
        pos = df.index.get_loc(idx)
        exit_pos = min(pos + 20, len(df) - 1)
        if exit_pos > pos:
            s.iloc[pos+1:exit_pos] = 1
            s.iloc[exit_pos] = -1
    return s

def sig_pullback_deep(df):
    """深度回调：距60日高回落25-50%买入，反弹至-10%以内卖出。"""
    c, h = df['close'], df['high']
    s = pd.Series(0, index=df.index)
    h60 = h.rolling(60).max()
    pb = (c / h60 - 1) * 100
    s[(pb < -25) & (pb > -50)] = 1
    for i in range(1, len(s)):
        prev = s.iloc[i-1]
        if prev == 1 and pb.iloc[i] > -10:
            s.iloc[i] = -1
        elif prev == 1:
            s.iloc[i] = 1
    return s

def sig_pullback_ma60(df):
    """回踩 MA60：收盘曾高于MA60且现回踩至MA60附近(±3%)买入。"""
    c = df['close']; s = pd.Series(0, index=df.index)
    m60 = sma(c, 60)
    above = c > m60
    near = (c >= m60 * 0.97) & (c <= m60 * 1.03)
    touched = above.shift(1).fillna(False)
    s[near & touched] = 1
    for i in range(1, len(s)):
        if s.iloc[i-1] == 1:
            s.iloc[i] = -1 if c.iloc[i] < m60.iloc[i] * 0.95 else 1
    return s

def sig_breakout_20d(df):
    c, h = df['close'], df['high']
    s = pd.Series(0, index=df.index)
    h20 = h.rolling(20).max()
    s[c > h20.shift(1)] = 1
    for i in range(1, len(s)):
        if s.iloc[i-1] == 1:
            s.iloc[i] = -1 if c.iloc[i] < c.iloc[i-1] * 0.95 else 1
    return s

def sig_breakout_vol(df):
    """放量突破：突破20日高 + 量>5日均量1.5倍。"""
    c, h, v = df['close'], df['high'], df['volume']
    s = pd.Series(0, index=df.index)
    h20 = h.rolling(20).max()
    v5 = v.rolling(5).mean()
    s[(c > h20.shift(1)) & (v > v5 * 1.5)] = 1
    for i in range(1, len(s)):
        if s.iloc[i-1] == 1:
            s.iloc[i] = -1 if c.iloc[i] < c.iloc[i-1] * 0.95 else 1
    return s

def sig_volume_surge(df):
    """量比突增：5日均量/20日均量>2 且 收盘涨。"""
    c, v = df['close'], df['volume']
    s = pd.Series(0, index=df.index)
    v5, v20 = v.rolling(5).mean(), v.rolling(20).mean()
    s[(v5 / v20 > 2) & (c.diff() > 0)] = 1
    for i in range(1, len(s)):
        if s.iloc[i-1] == 1:
            s.iloc[i] = -1 if c.iloc[i] < c.iloc[i-1] * 0.95 else 1
    return s

def sig_momentum(df):
    """动量：20日涨幅>15% 且 创20日新高。"""
    c = df['close']; s = pd.Series(0, index=df.index)
    ret20 = c / c.shift(20) - 1
    h20 = c.rolling(20).max()
    s[(ret20 > 0.15) & (c >= h20.shift(1))] = 1
    for i in range(1, len(s)):
        if s.iloc[i-1] == 1:
            s.iloc[i] = -1 if c.iloc[i] < c.iloc[i-1] * 0.95 else 1
    return s

def sig_macd_cross(df):
    c = df['close']; s = pd.Series(0, index=df.index)
    m, sg = macd(c)
    golden = (m > sg) & (m.shift(1) <= sg.shift(1))
    dead = (m < sg) & (m.shift(1) >= sg.shift(1))
    s[golden] = 1; s[dead] = -1
    return s

def sig_bb_rsi(df):
    c = df['close']; s = pd.Series(0, index=df.index)
    u, m, l = boll(c)
    r = rsi(c)
    s[(c < l) & (r < 35)] = 1
    s[(c > m) & (r > 60)] = -1
    return s

def sig_trend_pullback(df):
    """趋势+回调：站上MA60 且 距20日高回落5-15% 买入。"""
    c, h = df['close'], df['high']
    s = pd.Series(0, index=df.index)
    m60 = sma(c, 60)
    h20 = h.rolling(20).max()
    pb = c / h20 - 1
    s[(c > m60) & (pb < -0.05) & (pb > -0.15)] = 1
    for i in range(1, len(s)):
        if s.iloc[i-1] == 1:
            s.iloc[i] = -1 if c.iloc[i] < m60.iloc[i] * 0.95 else 1
    return s

def sig_rsi_macd(df):
    """组合：RSI<40 金叉 MACD。"""
    c = df['close']; s = pd.Series(0, index=df.index)
    r = rsi(c)
    m, sg = macd(c)
    golden = (m > sg) & (m.shift(1) <= sg.shift(1))
    s[golden & (r < 40)] = 1
    for i in range(1, len(s)):
        if s.iloc[i-1] == 1:
            s.iloc[i] = -1 if c.iloc[i] < c.iloc[i-1] * 0.95 else 1
    return s

def sig_breakout_ma60(df):
    """站上MA60的突破：突破20日高 且 收盘>MA60。"""
    c, h = df['close'], df['high']
    s = pd.Series(0, index=df.index)
    h20 = h.rolling(20).max()
    m60 = sma(c, 60)
    s[(c > h20.shift(1)) & (c > m60)] = 1
    for i in range(1, len(s)):
        if s.iloc[i-1] == 1:
            s.iloc[i] = -1 if c.iloc[i] < c.iloc[i-1] * 0.95 else 1
    return s

STRATEGIES = {
    "dual_ma":            ("双均线金叉", sig_dual_ma),
    "trend_5d":           ("五日趋势", sig_trend_5d),
    "ma60_trend":         ("MA60趋势", sig_ma60_trend),
    "bollinger":          ("布林带反转", sig_bollinger),
    "rsi_reversion":      ("RSI超卖回归", sig_rsi_reversion),
    "rsi_extreme":        ("RSI极端(20/60)", sig_rsi_extreme),
    "pullback_20d":       ("缩量回调20日", sig_pullback_20d),
    "pullback_deep":      ("深度回调", sig_pullback_deep),
    "pullback_ma60":      ("回踩MA60", sig_pullback_ma60),
    "breakout_20d":       ("突破20日高", sig_breakout_20d),
    "breakout_vol":       ("放量突破", sig_breakout_vol),
    "volume_surge":       ("量比突增", sig_volume_surge),
    "momentum":           ("20日动量", sig_momentum),
    "macd_cross":         ("MACD金叉", sig_macd_cross),
    "bb_rsi":             ("布林+RSI", sig_bb_rsi),
    "trend_pullback":     ("趋势+回调", sig_trend_pullback),
    "rsi_macd":           ("RSI+MACD", sig_rsi_macd),
    "breakout_ma60":      ("MA60突破", sig_breakout_ma60),
}

# ============================================================================
# 模拟器（统一规则）
# ============================================================================

def simulate(df, signals, capital=100000.0, commission=0.0003,
             stop_loss=0.05, take_profit=0.15):
    """返回 (总收益率%, 交易次数, 胜率%, 最大回撤%, 年化%)。"""
    close = df['close'].values
    sig = signals.values
    n = len(df)

    cash = capital
    pos = 0.0
    entry = 0.0
    trades = 0
    wins = 0
    peak = capital
    max_dd = 0.0
    last_val = capital
    years = max((df.index[-1] - df.index[0]).days / 365.25, 0.1)

    for i in range(1, n):
        price = close[i]
        s = sig[i]

        if s == 1 and pos == 0:
            pos = cash * 0.95 / price
            entry = price
            cash -= pos * price * (1 + commission)
        elif s == -1 and pos > 0:
            pnl = price / entry - 1
            cash += pos * price * (1 - commission)
            trades += 1
            if pnl > 0: wins += 1
            pos, entry = 0.0, 0.0
        elif pos > 0:
            pnl = price / entry - 1
            if pnl <= -stop_loss or pnl >= take_profit:
                cash += pos * price * (1 - commission)
                trades += 1
                if pnl > 0: wins += 1
                pos, entry = 0.0, 0.0

        val = cash + pos * price
        last_val = val
        if val > peak: peak = val
        dd = (peak - val) / peak
        if dd > max_dd: max_dd = dd

    if pos > 0:  # 尾盘强制平仓
        pnl = close[-1] / entry - 1
        cash += pos * close[-1] * (1 - commission)
        trades += 1
        if pnl > 0: wins += 1

    total_ret = (cash / capital - 1) * 100
    annual = ((cash / capital) ** (1 / years) - 1) * 100 if cash > 0 else -100
    win_rate = wins / trades * 100 if trades else 0.0
    return round(total_ret, 2), trades, round(win_rate, 1), round(max_dd * 100, 2), round(annual, 2)

# ============================================================================
# 主流程
# ============================================================================

def load_data(data_dir):
    files = glob.glob(str(Path(data_dir) / "*.csv"))
    out = {}
    for f in files:
        code = Path(f).stem
        try:
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            if len(df) < 1500:
                continue
            df.columns = [c.lower() for c in df.columns]
            if not {'open', 'high', 'low', 'close', 'volume'} <= set(df.columns):
                continue
            # 数据清洗：剔除 OHLC 含非正价格的脏数据（如退市/异常值）
            ohlc = df[['open', 'high', 'low', 'close']]
            if (ohlc <= 0).any().any() or ohlc.isna().any().any():
                continue
            if (df['volume'] < 0).any():
                continue
            out[code] = df
        except Exception:
            pass
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=500, help="抽样股票数")
    ap.add_argument("--data-dir", default="C:/Users/ASUS/qtrade/data/cache")
    ap.add_argument("--output", default="factor_backtest_results.csv")
    args = ap.parse_args()

    t0 = time.time()
    data = load_data(args.data_dir)
    codes = sorted(data.keys())
    if args.sample and args.sample < len(codes):
        random.seed(2024)
        codes = random.sample(codes, args.sample)
    print(f"样本股票: {len(codes)} 只（共 {len(data)} 只有效数据）")

    rows = []
    for ci, code in enumerate(codes):
        df = data[code]
        for name, (label, gen) in STRATEGIES.items():
            try:
                sig = gen(df)
                if (sig == 1).sum() == 0 and (sig == -1).sum() == 0:
                    continue  # 无信号
                ret, trades, wr, mdd, ann = simulate(df, sig)
                rows.append({
                    "symbol": code, "strategy": name, "label": label,
                    "total_return": ret, "trades": trades, "win_rate": wr,
                    "max_drawdown": mdd, "annual": ann,
                })
            except Exception:
                pass
        if (ci + 1) % 100 == 0:
            print(f"  进度 {ci+1}/{len(codes)}  ({time.time()-t0:.0f}s)")

    res = pd.DataFrame(rows)
    res.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"\n结果已保存: {args.output} ({len(res)} 行)")

    # ===== 汇总 =====
    summary = []
    for name, (label, _) in STRATEGIES.items():
        sub = res[res['strategy'] == name]
        if sub.empty:
            continue
        trades_total = int(sub['trades'].sum())
        avg_trades = sub['trades'].mean()
        summary.append({
            "策略": label,
            "股票数": len(sub),
            "平均收益率%": round(sub['total_return'].mean(), 2),
            "中位收益率%": round(sub['total_return'].median(), 2),
            "正收益占比%": round((sub['total_return'] > 0).mean() * 100, 1),
            "平均胜率%": round(sub['win_rate'].mean(), 1),
            "平均交易次数": round(avg_trades, 1),
            "总交易次数": trades_total,
            "平均回撤%": round(sub['max_drawdown'].mean(), 2),
            "平均年化%": round(sub['annual'].mean(), 2),
        })
    summ = pd.DataFrame(summary)
    summ = summ.sort_values("平均收益率%", ascending=False)
    pd.set_option('display.width', 200)
    pd.set_option('display.max_columns', 20)
    print("\n===== 全市场因子回测汇总（按平均收益率排序） =====")
    print(summ.to_string(index=False))
    print(f"\n总耗时: {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
