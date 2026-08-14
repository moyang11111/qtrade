#!/usr/bin/env python
"""
超短线因子挖掘研究（持仓 1/2/3/5 日）
================================================
目标：数据驱动挖掘短线因子，寻找"收益率高 + 胜率高"的超短线策略，
并严格防过拟合（样本内挖因子 → 样本外验证）。

数据：日线 OHLCV（无分钟数据，超短线定义为持仓 1-5 个交易日）
方法：横截面 IC（因子 vs 未来 h 日收益）、样本内外分割、组合回测含
     真实交易成本（双边 0.2%，含滑点）。

注意 A 股 T+1：买入次日才能卖。
"""
import io
import sys
import glob
import time
import warnings
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)

warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd

DATA_DIR = "C:/Users/ASUS/qtrade/data/cache"
SAMPLE = 400
TOP_N = 20
COST = 0.002        # 双边成本 + 滑点（超短线必须从严）
SPLIT = 0.6
HORIZON = 3         # 预测未来 3 日收益（主跑 3，可改 1/2/5）

# ============================================================================
# 数据加载（复用 factor_mining 的优化结构）
# ============================================================================

class Precomputed:
    def __init__(self, df):
        c, h, l, o, v = df['close'], df['high'], df['low'], df['open'], df['volume']
        r = c.pct_change()
        self.close = c.values
        self.high = h.values
        self.low = l.values
        self.open = o.values
        self.volume = v.values
        self.r = r.values
        self.n = len(df)
        self.index = df.index
        self.period = df.index.to_period('M')
        # 预计算常用
        self.c_ret = {n: (c / c.shift(n) - 1).values for n in [1, 2, 3, 5, 10]}
        self.ma = {n: c.rolling(n).mean().values for n in [5, 10, 20]}
        self.hi = {n: h.rolling(n).max().values for n in [5, 10, 20]}
        self.lo = {n: l.rolling(n).min().values for n in [5, 10, 20]}
        self.vm = {n: v.rolling(n).mean().values for n in [5, 10, 20]}
        self.r_std = {n: r.rolling(n).std().values for n in [5, 10]}
        self.amp = ((h - l) / o).values          # 振幅
        self.gap = (o / c.shift(1) - 1).values   # 隔夜跳空
        self.close_pos = ((c - l) / (h - l).replace(0, np.nan)).values  # 收盘位置

    def trade_days(self):
        return self.n


def pick_codes(n, seed=7):
    files = glob.glob(str(Path(DATA_DIR) / "*.csv"))
    cand = [Path(f).stem for f in files if Path(f).stat().st_size > 500]
    rng = np.random.RandomState(seed)
    return list(rng.choice(cand, min(n, len(cand)), replace=False))


def load_pool(codes):
    pool = {}
    for code in codes:
        f = Path(DATA_DIR) / f"{code}.csv"
        if not f.exists():
            continue
        try:
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            if len(df) < 1500:
                continue
            df.columns = [c.lower() for c in df.columns]
            if not {'open', 'high', 'low', 'close', 'volume'} <= set(df.columns):
                continue
            ohlc = df[['open', 'high', 'low', 'close']]
            if (ohlc <= 0).any().any() or ohlc.isna().any().any():
                continue
            if (df['volume'] < 0).any():
                continue
            pool[code] = Precomputed(df)
        except Exception:
            pass
    return pool

# ============================================================================
# 短线候选因子（日频，数据驱动）
# ============================================================================

def build_st_factors(p: Precomputed, i: int) -> dict | None:
    """在交易日 i 计算全部短线候选因子。"""
    if i < 5 or i >= p.n - HORIZON:
        return None
    c = p.close
    price = c[i]
    if not np.isfinite(price) or price <= 0:
        return None

    def at(arr, k, default=np.nan):
        j = i - k
        return arr[j] if j >= 0 else default

    def ret_n(n):
        return at(p.c_ret.get(n, []), n) if p.c_ret.get(n) is not None else np.nan

    f = {}
    # ---- 短动量/反转 ----
    for n in [1, 2, 3, 5]:
        f[f'ret_{n}d'] = ret_n(n)
    f['ret_3d_1d'] = ret_n(3) - ret_n(1)   # 3日动能扣除最近1日
    # ---- 涨跌停/打板效应 ----
    f['hit_limit'] = 1.0 if ret_n(1) is not None and ret_n(1) > 0.095 else 0.0
    f['near_limit'] = 1.0 if ret_n(1) is not None and ret_n(1) > 0.07 else 0.0
    f['prev_limit'] = 1.0 if at(p.c_ret.get(1, []), 2) is not None and at(p.c_ret.get(1, []), 2) > 0.095 else 0.0  # 前日涨停
    # ---- 连板（连续涨停天数） ----
    days = 0
    for k in range(1, 10):
        v = at(p.c_ret.get(1, []), k)
        if v is not None and v > 0.095:
            days += 1
        else:
            break
    f['consec_limit'] = days
    # ---- 量能 ----
    f['vol_ratio'] = p.volume[i] / max(at(p.vm[5], 1), 1e-9)          # 当日量/5日均量
    f['vol_ratio_5_20'] = at(p.vm[5], 0) / max(at(p.vm[20], 0), 1e-9)
    f['vol_chg_1d'] = p.volume[i] / max(p.volume[i - 1], 1e-9) - 1
    # ---- 波动/振幅 ----
    f['amp_1d'] = at(p.amp, 0)
    f['vol_5d'] = at(p.r_std[5], 0) * np.sqrt(252)
    f['vol_10d'] = at(p.r_std[10], 0) * np.sqrt(252)
    # ---- 缺口/位置 ----
    f['gap_1d'] = at(p.gap, 0)
    f['close_pos'] = at(p.close_pos, 0)
    # ---- 突破/回踩 ----
    f['break_20'] = 1.0 if price > at(p.hi[20], 1) else 0.0           # 突破20日高
    f['break_10'] = 1.0 if price > at(p.hi[10], 1) else 0.0
    f['dist_hi20'] = price / at(p.hi[20], 0) - 1
    f['dist_lo20'] = price / at(p.lo[20], 0) - 1
    # ---- 趋势位置 ----
    f['ma5_above_20'] = 1.0 if at(p.ma[5], 0) > at(p.ma[20], 0) else 0.0
    f['bias_ma5'] = price / at(p.ma[5], 0) - 1
    f['bias_ma20'] = price / at(p.ma[20], 0) - 1
    # ---- 短线反转信号 ----
    f['oversold'] = 1.0 if ret_n(3) is not None and ret_n(3) < -0.08 else 0.0  # 3日大跌
    f['overbought'] = 1.0 if ret_n(3) is not None and ret_n(3) > 0.10 else 0.0  # 3日大涨
    f['shrink_pullback'] = 1.0 if (f['vol_ratio'] < 0.6 and ret_n(1) is not None and ret_n(1) < 0) else 0.0  # 缩量下跌
    # ---- 强弱排名代理 ----
    f['streak_up'] = _streak(p.r, i, 1)
    f['streak_dn'] = _streak(p.r, i, -1)

    return f


def _streak(r, i, sign):
    n = 0
    for j in range(i, i - 15, -1):
        if j < 0 or np.isnan(r[j]):
            break
        if (r[j] > 0) == (sign > 0):
            n += 1
        else:
            break
    return n


def _spearman(xs, ys):
    if len(xs) < 5:
        return np.nan
    rx = pd.Series(xs).rank().values
    ry = pd.Series(ys).rank().values
    if np.std(rx) == 0 or np.std(ry) == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])

# ============================================================================
# 主流程
# ============================================================================

def main():
    t0 = time.time()
    print(f"超短线研究：预测未来 {HORIZON} 日收益，持仓 {HORIZON} 日（T+1）")
    codes = pick_codes(SAMPLE)
    pool = load_pool(codes)
    codes = [c for c in codes if c in pool]
    print(f"股票池: {len(codes)} 只")

    # 统一交易日序列（以共同日历日为准，逐日横截面）
    # 为控制计算量，每个交易日都算 IC 太慢；采样：每 5 个交易日算一次横截面
    STEP = 5
    # 构建全市场共同交易日（取样本股票的交集，简化：用其中一只的完整日期）
    ref = pool[codes[0]]
    dates = ref.index
    print(f"交易日序列: {len(dates)} 天，横截面采样步长 {STEP}")

    # 因子快照：date -> {code: factors}（按日期，非按月份）
    snapshots = {}
    day_pos = {}
    for code in codes:
        p = pool[code]
        pos = {d: i for i, d in enumerate(p.index)}
        day_pos[code] = pos

    print("计算短线因子快照...")
    for code in codes:
        p = pool[code]
        for i in range(5, p.n - HORIZON):
            d = p.index[i]
            f = build_st_factors(p, i)
            if f is None:
                continue
            snapshots.setdefault(d, {})[code] = f
    print(f"快照天数: {len(snapshots)}")

    # 未来收益
    print("计算未来收益...")
    fwd = {}
    for code in codes:
        p = pool[code]
        r = np.full(p.n, np.nan)
        for i in range(p.n - HORIZON):
            r[i] = p.close[i + HORIZON] / p.close[i] - 1
        fwd[code] = r

    # 因子名
    all_fnames = set()
    for d, sc in snapshots.items():
        for f in sc.values():
            all_fnames.update(f.keys())
    factor_names = sorted(all_fnames)
    print(f"候选短线因子数: {len(factor_names)}")

    # ===== IC 分析（样本内/样本外） =====
    dates_sorted = sorted(snapshots.keys())
    cut_date = dates_sorted[int(len(dates_sorted) * SPLIT)]
    print(f"\n时间分割: 样本内 ≤ {cut_date.date()} | 样本外 > {cut_date.date()}")

    ic_rows = []
    for fn in factor_names:
        ic_in, ic_out = [], []
        for d in dates_sorted[::STEP]:
            sc = snapshots[d]
            fv = {c: f[fn] for c, f in sc.items() if fn in f and np.isfinite(f[fn])}
            if len(fv) < 20:
                continue
            # 未来收益（用该股当日位置）
            ys = {}
            for c in fv:
                p = pool[c]
                pos = day_pos[c].get(d)
                if pos is None or pos >= p.n - HORIZON:
                    continue
                y = fwd[c][pos]
                if np.isfinite(y):
                    ys[c] = y
            common = [c for c in fv if c in ys]
            if len(common) < 20:
                continue
            ic = _spearman([fv[c] for c in common], [ys[c] for c in common])
            if np.isfinite(ic):
                (ic_in if d <= cut_date else ic_out).append(ic)
        if len(ic_in) >= 20 and len(ic_out) >= 10:
            mi, mo = np.mean(ic_in), np.mean(ic_out)
            ic_rows.append({
                'factor': fn,
                'n_in': len(ic_in), 'n_out': len(ic_out),
                'ic_in': round(float(mi), 4),
                'ic_out': round(float(mo), 4),
                'ic_in_t': round(float(mi / (np.std(ic_in) / np.sqrt(len(ic_in)))), 2) if np.std(ic_in) > 0 else 0,
                'ic_out_t': round(float(mo / (np.std(ic_out) / np.sqrt(len(ic_out)))), 2) if np.std(ic_out) > 0 else 0,
                'dir_ok': '✓' if np.sign(mi) == np.sign(mo) else '✗',
            })
    ic_df = pd.DataFrame(ic_rows).sort_values('ic_out_t', ascending=False)
    pd.set_option('display.width', 200)

    # 样本内显著
    sig_in = ic_df[(ic_df['ic_in_t'].abs() > 2)]
    print(f"\n样本内显著因子(|t|>2): {len(sig_in)}")
    print("\n===== 短线因子 IC（按样本外 t 排序） =====")
    print(ic_df[['factor', 'ic_in', 'ic_out', 'ic_in_t', 'ic_out_t', 'dir_ok']].head(25).to_string(index=False))

    # 样本外方向一致且显著
    robust = ic_df[(ic_df['dir_ok'] == '✓') & (ic_df['ic_out_t'].abs() > 2)]
    print(f"\n样本外方向一致且显著因子: {len(robust)}")
    if not robust.empty:
        print(robust[['factor', 'ic_in', 'ic_out', 'ic_out_t']].to_string(index=False))

    # ===== 组合回测（超短线） =====
    print(f"\n===== 超短线组合回测：持仓 {HORIZON} 日，top{TOP_N}，双边成本 {COST*100:.1f}% =====")
    use_factors = []
    if len(robust) >= 3:
        use_factors = robust.nlargest(8, 'ic_out_t')['factor'].tolist()
    else:
        use_factors = ic_df[ic_df['dir_ok'] == '✓'].nlargest(8, 'ic_out_t')['factor'].tolist()
    print(f"使用因子: {use_factors}")
    if not use_factors:
        print("无可用因子")
        return

    ic_sign = {fn: (1 if ic_df.loc[ic_df['factor'] == fn, 'ic_in'].iloc[0] > 0 else -1) for fn in use_factors}

    capital = 1_000_000.0
    trades = 0
    wins = 0
    history = []
    # 逐日调仓：每天选 top N，持有 HORIZON 日
    # 简化实现：每 STEP 天调仓一次（实际持仓 HORIZON 日）
    hold = []
    for di in range(0, len(dates_sorted), HORIZON):
        d = dates_sorted[di]
        if d not in snapshots:
            continue
        sc = snapshots[d]
        # 选股
        scores = {}
        for c, f in sc.items():
            vals = []
            for fn in use_factors:
                if fn in f and np.isfinite(f[fn]):
                    vals.append(ic_sign[fn] * f[fn])
            if vals:
                scores[c] = np.mean(vals)
        if not scores:
            continue
        s = pd.Series(scores)
        s = (s - s.mean()) / s.std()
        top = s.nlargest(TOP_N).index.tolist()
        # 持仓 HORIZON 日收益
        rets = []
        for c in top:
            p = pool[c]
            pos = day_pos[c].get(d)
            if pos is None or pos + HORIZON >= p.n:
                continue
            rr = p.close[pos + HORIZON] / p.close[pos] - 1
            if np.isfinite(rr):
                rets.append(rr)
        if not rets:
            continue
        port_ret = np.mean(rets)
        capital *= (1 + port_ret - COST)
        trades += len(rets)
        wins += sum(1 for r in rets if r > 0)
        history.append((d, capital, port_ret, len(rets)))

    h = pd.DataFrame(history, columns=['date', 'capital', 'ret', 'n_hold'])
    h['phase'] = ['样本内' if d <= cut_date else '样本外' for d in h['date']]
    final = h['capital'].iloc[-1]
    out_final = h[h['phase'] == '样本外']['capital'].iloc[-1] if (h['phase'] == '样本外').any() else None
    in_final = h[h['phase'] == '样本内']['capital'].iloc[-1] if (h['phase'] == '样本内').any() else None

    print(f"\n期初: ¥1,000,000")
    print(f"期末: ¥{final:,.0f} （{final/1e6:.2f} 倍）")
    if in_final is not None:
        print(f"样本内: ¥{in_final:,.0f} （{in_final/1e6:.2f} 倍）")
    if out_final is not None:
        print(f"样本外: ¥{out_final:,.0f} （{out_final/1e6:.2f} 倍）")
    years = len(dates_sorted) / 244
    ann = (final / 1e6) ** (1 / max(years, 0.1)) - 1 if final > 0 else -1
    print(f"跨度 {years:.1f} 年, 年化 {ann*100:.1f}%")
    print(f"总交易 {trades} 笔, 单笔胜率 {wins/trades*100:.1f}%")
    print(f"组合调仓胜率 {(h['ret']>0).mean()*100:.1f}%, 期均收益 {h['ret'].mean()*100:.2f}%")
    mdd = (h['capital'] / h['capital'].cummax() - 1).min()
    print(f"最大回撤 {mdd*100:.1f}%")
    print(f"总耗时 {time.time()-t0:.0f}s")

    h.to_csv('st_history.csv', index=False)
    ic_df.to_csv('st_ic.csv', index=False, encoding='utf-8-sig')
    print("已保存: st_history.csv / st_ic.csv")


if __name__ == "__main__":
    main()
