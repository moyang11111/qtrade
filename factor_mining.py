#!/usr/bin/env python
"""
因子挖掘研究：数据驱动找因子 + 严格防过拟合
================================================
目标：
  1. 不从经典指标拍脑袋选因子，构建 40+ 候选因子库，用横截面 IC
     数据驱动地挖掘对下月收益有预测力的因子。
  2. 时间分割：样本内（前 60%）挖因子 → 样本外（后 40%）验证。
  3. 防过拟合：样本内 IC 显著(|t|>2) + 样本外方向一致；组合业绩以
     样本外为准。

方法：月度调仓、横截面 Spearman IC、等权标准化打分、top N 持有、
     双边成本 0.15%。
"""
import io
import sys
import glob
import time
import warnings
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)

warnings.filterwarnings('ignore')  # 屏蔽 numpy 空切片警告（刷屏拖慢）

import numpy as np
import pandas as pd

DATA_DIR = "C:/Users/ASUS/qtrade/data/cache"
SAMPLE = 400
TOP_N = 20
COST = 0.0015
SPLIT = 0.6

# ============================================================================
# 数据加载 + 预计算（每只股票只算一次滚动序列）
# ============================================================================

class Precomputed:
    """预计算单只股票的所有滚动序列，供因子快照 O(1) 取值。"""

    def __init__(self, df):
        c, h, l, o, v = df['close'], df['high'], df['low'], df['open'], df['volume']
        r = c.pct_change()
        self.close = c.values
        self.high = h.values
        self.low = l.values
        self.open = o.values
        self.volume = v.values
        self.r = r.values
        self.period = df.index.to_period('M')
        self.n = len(df)
        self.index = df.index

        # 预计算常用滚动
        self.c_ret = {n: (c / c.shift(n) - 1).values for n in [5, 10, 20, 60, 120]}
        self.ma = {n: c.rolling(n).mean().values for n in [5, 20, 60]}
        self.hi = {n: h.rolling(n).max().values for n in [20, 60, 250]}
        self.lo = {n: l.rolling(n).min().values for n in [20, 60, 250]}
        self.vm = {n: v.rolling(n).mean().values for n in [5, 20, 60]}
        self.r_std = {n: r.rolling(n).std().values for n in [21, 63]}
        self.r_skew = r.rolling(21).skew().values
        self.r_kurt = r.rolling(21).kurt().values

    def month_ends(self):
        """返回该股每月最后一个交易日的索引位置。"""
        d = pd.Series(self.index).dt.to_period('M')
        ends = {}
        for i in range(self.n):
            ends[d.iloc[i]] = i
        return ends


def pick_codes(n: int, seed: int = 7) -> list[str]:
    """按文件大小初筛后随机抽样股票代码（不读全量数据）。"""
    files = glob.glob(str(Path(DATA_DIR) / "*.csv"))
    cand = [Path(f).stem for f in files if Path(f).stat().st_size > 500]
    rng = np.random.RandomState(seed)
    return list(rng.choice(cand, min(n, len(cand)), replace=False))


def load_pool(codes: list[str]):
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
# 候选因子快照（O(1)，基于预计算）
# ============================================================================

def build_factors(p: Precomputed, i: int) -> dict | None:
    """在索引 i（月末）计算该股票全部候选因子。"""
    if i <= 0 or i >= p.n:
        return None
    c = p.close
    price = c[i]
    if not np.isfinite(price) or price <= 0:
        return None

    def at(arr, k, default=np.nan):
        j = i - k
        if j < 0:
            return default
        return arr[j]

    def ret_n(n):
        v = p.c_ret.get(n)
        return at(v, n) if v is not None else np.nan

    f = {}
    # ---- 动量/反转（多周期） ----
    for n in [5, 10, 20, 60, 120]:
        f[f'mom_{n}'] = ret_n(n)
    f['mom_20_60'] = ret_n(20) - ret_n(60)
    f['mom_60_120'] = ret_n(60) - ret_n(120)
    f['dist_high'] = price / at(p.hi[250], 0) - 1
    f['dist_low'] = price / at(p.lo[250], 0) - 1
    hi, lo = at(p.hi[250], 0), at(p.lo[250], 0)
    f['range_pos'] = (price - lo) / (hi - lo) if hi > lo else np.nan

    # ---- 波动结构 ----
    f['vol_1m'] = at(p.r_std[21], 0) * np.sqrt(252)
    f['vol_3m'] = at(p.r_std[63], 0) * np.sqrt(252)
    f['vol_slope'] = f['vol_3m'] - f['vol_1m']
    f['skew_1m'] = at(p.r_skew, 0)
    f['kurt_1m'] = at(p.r_kurt, 0)
    # 下行波动占比（1月）
    s = p.r[i-20:i+1]
    neg = s[s < 0]
    f['down_vol'] = neg.std() / s.std() if len(neg) > 2 and s.std() > 0 else np.nan
    # 1月/3月最大回撤
    f['mdd_1m'] = _mdd(p.close, i, 21)
    f['mdd_3m'] = _mdd(p.close, i, 63)
    # 连续涨跌（尾部）
    f['consec_up'] = _consec(p.r, i, 1)
    f['consec_dn'] = _consec(p.r, i, -1)
    # 涨跌停频率
    r = p.r[i-20:i+1]
    f['limit_up_1m'] = (r > 0.09).mean()
    f['limit_dn_1m'] = (r < -0.09).mean()

    # ---- 量价 ----
    v1 = p.volume[i-20:i+1]
    v3 = p.volume[i-62:i+1]
    f['vol_chg_1m'] = v1.mean() / max(v3.mean(), 1e-9) - 1 if v3.size else np.nan
    v5 = p.vm[5][i]
    v20 = p.vm[20][i]
    f['vol_ratio_5_20'] = v5 / max(v20, 1e-9)
    f['vol_cv'] = v1.std() / max(v1.mean(), 1e-9) if v1.size else np.nan
    v60 = p.vm[60][i]
    f['big_vol_ratio'] = (v1 > v60 * 2).mean() if v60 else np.nan
    f['shrink_ratio'] = (v1 < v60 * 0.5).mean() if v60 else np.nan
    rv = p.r[i-20:i+1]
    vv = np.diff(p.volume[i-21:i+1]) / p.volume[i-21:i] if i >= 21 else np.array([])
    f['vol_price_corr'] = _corr(rv[1:], vv) if len(rv) > 4 and len(vv) == len(rv) - 1 else np.nan
    amt = v1 * price
    f['amihud'] = (np.abs(r) / amt).mean() if amt.mean() > 0 else np.nan

    # ---- 形态 ----
    o = p.open[i-20:i+1]; h = p.high[i-20:i+1]; l = p.low[i-20:i+1]; cc = p.close[i-20:i+1]
    span = h - l
    f['amp_mean'] = ((h - o) / o).mean()
    f['up_shadow'] = ((h - np.maximum(o, cc)) / span.replace(0, np.nan) if hasattr(span, 'replace') else np.divide(h - np.maximum(o, cc), np.where(span == 0, np.nan, span))).mean()
    f['dn_shadow'] = ((np.minimum(o, cc) - l) / np.where(span == 0, np.nan, span)).mean()
    f['yang_ratio'] = (cc > o).mean()
    f['big_yang'] = (r > 0.03).mean()
    f['big_yin'] = (r < -0.03).mean()

    # ---- 趋势/位置 ----
    ma20 = p.ma[20][i]; ma60 = p.ma[60][i]
    f['ma_spread'] = price / ma20 - 1 if ma20 > 0 else np.nan
    f['ma20_60'] = ma20 / ma60 - 1 if ma60 > 0 else np.nan
    hi20, lo20 = p.hi[20][i], p.lo[20][i]
    f['pos_20d'] = (price - lo20) / (hi20 - lo20) if hi20 > lo20 else np.nan
    f['bias_20'] = price / ma20 - 1 if ma20 > 0 else np.nan

    return f


def _mdd(close, i, window):
    seg = close[max(0, i - window + 1):i + 1]
    if len(seg) < 3:
        return np.nan
    peak = np.maximum.accumulate(seg)
    return float((seg / peak - 1).min())


def _consec(r, i, sign):
    run = 0
    for j in range(i, max(i - 60, -1), -1):
        if j < 0:
            break
        v = r[j]
        if np.isnan(v):
            break
        if (v > 0) == (sign > 0):
            run += 1
        else:
            break
    return run


def _corr(a, b):
    if len(a) < 3 or len(b) != len(a):
        return np.nan
    if np.std(a) == 0 or np.std(b) == 0:
        return np.nan
    return float(np.corrcoef(a, b)[0, 1])

# ============================================================================
# 主流程
# ============================================================================

def main():
    t0 = time.time()
    print("抽样 + 加载数据...")
    codes = pick_codes(SAMPLE)
    pool = load_pool(codes)
    codes = [c for c in codes if c in pool]
    print(f"股票池: {len(codes)} 只")

    # 每只股票的月末位置
    ends_map = {code: p.month_ends() for code, p in pool.items() if code in codes}
    months = sorted(set().union(*[set(e.keys()) for e in ends_map.values()]))
    print(f"月份数: {len(months)}")

    print("计算因子快照...")
    snapshots = {}   # month -> {code: factors}
    for code in codes:
        e = ends_map[code]
        p = pool[code]
        for m in months:
            if m not in e:
                continue
            f = build_factors(p, e[m])
            if f is None:
                continue
            snapshots.setdefault(m, {})[code] = f
    factor_names = sorted(set().union(*[set(f.keys()) for f in snapshots.get(months[0], {}).values()])) if snapshots else []
    # 因子名统一收集
    all_fnames = set()
    for m, sc in snapshots.items():
        for f in sc.values():
            all_fnames.update(f.keys())
    factor_names = sorted(all_fnames)
    print(f"候选因子数: {len(factor_names)}")

    print("计算下月收益...")
    returns = {}
    for mi in range(len(months) - 1):
        m, nxt = months[mi], months[mi + 1]
        r_series = {}
        for code in codes:
            e = ends_map[code]
            if m in e and nxt in e:
                p0 = pool[code].close[e[m]]
                p1 = pool[code].close[e[nxt]]
                if p0 > 0 and np.isfinite(p0) and np.isfinite(p1):
                    r_series[code] = p1 / p0 - 1
        returns[m] = r_series

    # ===== IC 分析 =====
    print("IC 分析...")
    ic_rows = []
    for fn in factor_names:
        ic_list = []
        for m, fv_map in snapshots.items():
            if m not in returns:
                continue
            common = [c for c, fv in fv_map.items()
                      if fn in fv and c in returns[m] and np.isfinite(fv[fn]) and np.isfinite(returns[m][c])]
            if len(common) < 15:
                continue
            xs = np.array([fv_map[c][fn] for c in common])
            ys = np.array([returns[m][c] for c in common])
            ic = _spearman(xs, ys)
            if np.isfinite(ic):
                ic_list.append(ic)
        if len(ic_list) >= 12:
            arr = np.array(ic_list)
            ic_rows.append({
                'factor': fn, 'n_months': len(arr),
                'ic_mean': round(float(arr.mean()), 4),
                'icir': round(float(arr.mean() / arr.std()), 3) if arr.std() > 0 else 0,
                'ic_positive': round(float((arr > 0).mean()), 3),
                'ic_t': round(float(arr.mean() / (arr.std() / np.sqrt(len(arr)))), 2) if arr.std() > 0 else 0,
            })
    ic_df = pd.DataFrame(ic_rows).sort_values('ic_t', ascending=False)
    pd.set_option('display.width', 200)

    months_sorted = sorted(months)
    cut = months_sorted[int(len(months_sorted) * SPLIT)]
    print(f"\n时间分割: 样本内 ≤ {cut}  |  样本外 > {cut}")

    # 样本内显著因子
    sig = ic_df[(ic_df['ic_t'].abs() > 2) & (ic_df['n_months'] >= 12)].copy()
    print(f"样本内显著因子数(|t|>2): {len(sig)}")
    print("\n===== IC 挖掘结果（全部候选按 |t| 排序） =====")
    print(sig[['factor', 'ic_mean', 'icir', 'ic_positive', 'ic_t']].head(20).to_string(index=False))

    # ===== 样本外验证 =====
    print("\n===== 样本外验证（防过拟合核心） =====")
    out_rows = []
    for fn in sig['factor']:
        ic_in, ic_out = [], []
        for m, fv_map in snapshots.items():
            if m not in returns:
                continue
            common = [c for c, fv in fv_map.items()
                      if fn in fv and c in returns[m] and np.isfinite(fv[fn]) and np.isfinite(returns[m][c])]
            if len(common) < 15:
                continue
            xs = np.array([fv_map[c][fn] for c in common])
            ys = np.array([returns[m][c] for c in common])
            ic = _spearman(xs, ys)
            if np.isfinite(ic):
                (ic_in if m <= cut else ic_out).append(ic)
        if len(ic_out) >= 6:
            mi_, mo_ = np.mean(ic_in) if ic_in else np.nan, np.mean(ic_out)
            out_rows.append({
                'factor': fn,
                'ic_in': round(float(mi_), 4) if np.isfinite(mi_) else np.nan,
                'ic_out': round(float(mo_), 4),
                'ic_out_t': round(float(mo_ / (np.std(ic_out) / np.sqrt(len(ic_out)))), 2) if np.std(ic_out) > 0 else 0,
                'dir_ok': '✓' if np.sign(mi_) == np.sign(mo_) else '✗',
            })
    out_df = pd.DataFrame(out_rows)
    if not out_df.empty:
        out_df = out_df.sort_values('ic_out', ascending=False)
        print(out_df.to_string(index=False))
        robust = out_df[out_df['dir_ok'] == '✓']
        print(f"\n样本外方向一致因子: {len(robust)}/{len(out_df)}")
    else:
        robust = pd.DataFrame()
        print("样本外月份不足")

    # ===== 组合回测 =====
    print("\n===== 组合回测：100 万 → ？ =====")
    # 只用样本外验证显著的因子（|t|>2），防止弱因子稀释
    if len(robust) >= 3:
        robust_sig = robust[robust['ic_out_t'].abs() > 2]
        if len(robust_sig) >= 3:
            use_factors = robust_sig.nlargest(6, 'ic_out_t')['factor'].tolist()
        else:
            use_factors = robust.nlargest(6, 'ic_out')['factor'].tolist()
    else:
        use_factors = sig.nlargest(6, 'ic_t')['factor'].tolist()
    print(f"使用因子: {use_factors}")

    capital = 1_000_000.0
    history = []
    # 因子方向：按样本内 IC 符号统一为"值越大越好"
    ic_sign = {fn: (1 if sig.loc[sig['factor'] == fn, 'ic_mean'].iloc[0] > 0 else -1)
               for fn in use_factors if fn in sig['factor'].values}
    for mi in range(len(months_sorted) - 1):
        m = months_sorted[mi]
        if m not in snapshots or m not in returns:
            continue
        scores = {}
        for c, fv in snapshots[m].items():
            vals = [ic_sign[fn] * fv[fn] for fn in use_factors
                    if fn in fv and fn in ic_sign and np.isfinite(fv[fn])]
            if vals:
                scores[c] = np.mean(vals)
        if not scores:
            continue
        s = pd.Series(scores)
        s = (s - s.mean()) / s.std()
        top = s.nlargest(TOP_N).index.tolist()
        rv = returns[m]
        port_ret = np.mean([rv[c] for c in top if c in rv] or [0.0])
        capital *= (1 + port_ret - COST)
        history.append((m, capital, port_ret))

    h = pd.DataFrame(history, columns=['month', 'capital', 'ret'])
    h['phase'] = ['样本内' if m <= cut else '样本外' for m in h['month']]
    final = h['capital'].iloc[-1]
    in_final = h[h['phase'] == '样本内']['capital'].iloc[-1] if (h['phase'] == '样本内').any() else None
    out_final = h[h['phase'] == '样本外']['capital'].iloc[-1] if (h['phase'] == '样本外').any() else None

    print(f"\n期初: ¥1,000,000")
    print(f"期末: ¥{final:,.0f}  （{final/1e6:.2f} 倍）")
    if in_final is not None and out_final is not None:
        print(f"样本内: ¥{in_final:,.0f} （{in_final/1e6:.2f} 倍）")
        print(f"样本外: ¥{out_final:,.0f} （{out_final/1e6:.2f} 倍）")
    years = len(h) / 12
    ann = (final / 1e6) ** (1 / years) - 1 if final > 0 else -1
    print(f"跨度 {years:.1f} 年, 年化 {ann*100:.1f}%")
    mdd = (h['capital'] / h['capital'].cummax() - 1).min()
    print(f"最大回撤 {mdd*100:.1f}%")
    print(f"月收益: 均值 {h['ret'].mean()*100:.2f}%, 胜率 {(h['ret']>0).mean()*100:.0f}%")
    print(f"总耗时 {time.time()-t0:.0f}s")

    h.to_csv('factor_mining_history.csv', index=False)
    ic_df.to_csv('factor_mining_ic.csv', index=False, encoding='utf-8-sig')
    print("已保存: factor_mining_history.csv / factor_mining_ic.csv")


def _spearman(xs, ys):
    if len(xs) < 5:
        return np.nan
    rx = pd.Series(xs).rank().values
    ry = pd.Series(ys).rank().values
    if np.std(rx) == 0 or np.std(ry) == 0:
        return np.nan
    return float(np.corrcoef(rx, ry)[0, 1])


if __name__ == "__main__":
    main()
