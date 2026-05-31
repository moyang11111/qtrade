"""下载全部主板股票 + 回测。"""

import os, sys, csv, time, json
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

os.chdir(Path(__file__).parent.parent)
sys.path.insert(0, "src")

CACHE = Path("data/cache")
CACHE.mkdir(exist_ok=True)

BAIDU_URL = "https://finance.pae.baidu.com/selfselect/getstockquotation"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/vnd.finance-web.v1+json",
    "Origin": "https://gushitong.baidu.com",
    "Referer": "https://gushitong.baidu.com/",
}


def gen_all_main_codes():
    """Generate all main-board codes."""
    codes = []
    for i in range(1, 4000):
        codes.append(f"{i:06d}")
    for i in range(600000, 606000):
        codes.append(f"{i:06d}")
    for i in range(900000, 900100):  # some Shanghai 9-series
        codes.append(f"{i:06d}")
    return codes


def download_one(code):
    """Download single stock, return (code, rows)."""
    if (CACHE / f"{code}.csv").exists():
        try:
            with open(CACHE / f"{code}.csv") as f:
                lines = sum(1 for _ in f) - 1
            return code, lines
        except:
            return code, 0

    try:
        r = requests.get(BAIDU_URL, params={
            "all": "1", "isIndex": "false", "isBk": "false",
            "isBlock": "false", "isFutures": "false", "isStock": "true",
            "newFormat": "1", "group": "quotation_kline_ab",
            "finClientType": "pc", "code": code, "ktype": "1",
        }, headers=HEADERS, timeout=12)
        d = r.json()
        if d.get("ResultCode") != "0":
            return code, 0
        md = d["Result"]["newMarketData"]
        rows = [l.split(",") for l in md["marketData"].split(";") if l.strip() and len(l.split(",")) >= 6]
        good = []
        for v in rows:
            try:
                ts = int(v[0])
                dt = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                o = float(v[2]) if v[2] not in ("--", "") else 0
                c = float(v[3]) if v[3] not in ("--", "") else 0
                vo = float(v[4]) if v[4] not in ("--", "") else 0
                h = float(v[5]) if v[5] not in ("--", "") else 0
                l = float(v[6]) if v[6] not in ("--", "") else 0
                good.append([dt, o, h, l, c, vo])
            except:
                pass
        if good:
            with open(CACHE / f"{code}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["date", "open", "high", "low", "close", "volume"])
                w.writerows(good)
        return code, len(good)
    except:
        return code, 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()

    all_codes = gen_all_main_codes()
    print(f"Total main-board codes: {len(all_codes)}")

    if not args.skip_download:
        # Filter: skip already cached with >=120 rows
        to_dl = []
        already = 0
        for code in all_codes:
            p = CACHE / f"{code}.csv"
            if p.exists():
                try:
                    with open(p) as f:
                        lines = sum(1 for _ in f) - 1
                    if lines >= 120:
                        already += 1
                        continue
                except:
                    pass
            to_dl.append(code)

        print(f"Already cached: {already}, To download: {len(to_dl)}")
        if to_dl:
            print(f"Downloading with {args.workers} workers...")
            ok = 0
            dl_count = 0
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(download_one, c): c for c in to_dl}
                for fut in as_completed(futures):
                    code, rows = fut.result()
                    dl_count += 1
                    if rows >= 120:
                        ok += 1
                    if dl_count % 200 == 0:
                        print(f"  {dl_count}/{len(to_dl)} downloaded, {ok} valid", flush=True)
                    time.sleep(0.02)  # rate limit
            print(f"Done: {ok} new valid stocks downloaded")

    # Count cached
    import pandas as pd, numpy as np
    valid = 0
    for code in all_codes:
        p = CACHE / f"{code}.csv"
        if p.exists():
            try:
                df = pd.read_csv(p, parse_dates=["date"], index_col="date")
                d26 = df["2026-01-01":"2026-05-27"]
                if len(d26) >= 80:
                    valid += 1
            except:
                pass

    print(f"\nTotal main-board stocks with 2026 data: {valid}")

    # ─── Run backtest ───
    print("\nRunning Pullback20D backtest...")
    all_trades = []
    for code in all_codes:
        p = CACHE / f"{code}.csv"
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p, parse_dates=["date"], index_col="date")
            df.columns = [c.lower() for c in df.columns]
            d = df["2026-01-01":"2026-05-27"].copy()
            if len(d) < 80:
                continue
        except:
            continue

        close = d["close"].values
        vol = d["volume"].values
        n = len(close)
        ma60 = pd.Series(close).rolling(60).mean().values
        peak = pd.Series(close).rolling(60).max().values
        vr = np.ones(n)
        for i in range(20, n):
            v5 = vol[max(0, i-4):i+1].mean()
            v20 = vol[max(0, i-19):i+1].mean()
            vr[i] = v5 / v20 if v20 > 0 else 1

        for i in range(60, n):
            drop = (peak[i] - close[i]) / peak[i]
            if not (0.15 <= drop <= 0.40):
                continue
            if close[i] <= ma60[i]:
                continue
            if vr[i] >= 0.7:
                continue
            ei = i + 1
            if ei >= n:
                continue
            ep = close[ei]
            xi = min(ei + 20, n - 1)
            xp = close[xi]
            ret = (xp / ep - 1) * 100
            all_trades.append({
                "sym": code,
                "ret": round(ret, 2),
                "drop": round(drop * 100, 1),
                "vr": round(vr[i], 2),
            })

    tdf = pd.DataFrame(all_trades)
    sig_stocks = tdf["sym"].nunique()
    wins = int((tdf["ret"] > 0).sum())

    print(f"\n{'='*60}")
    print(f"  Pullback20D — ALL MAIN BOARD ({valid} stocks)")
    print(f"{'='*60}")
    print(f"  Signal stocks:  {sig_stocks}/{valid} ({sig_stocks/valid*100:.1f}%)")
    print(f"  Total signals:  {len(tdf)}")
    print(f"  Avg per stock:  {len(tdf)/valid:.2f}")
    print(f"  Winners:        {wins} ({wins/len(tdf)*100:.0f}%)")
    print(f"  Avg return:     {tdf['ret'].mean():+.1f}%")
    print(f"  Median return:  {tdf['ret'].median():+.1f}%")
    print(f"  Std:            {tdf['ret'].std():.1f}%")
    print(f"  Best:           {tdf['ret'].max():+.1f}%")
    print(f"  Worst:          {tdf['ret'].min():+.1f}%")

    print(f"\n  By stock:")
    for sym, grp in tdf.groupby("sym"):
        w = int((grp["ret"] > 0).sum())
        t = len(grp)
        print(f"    {sym}: {t}T {w}W ({w/t*100:.0f}%) avg={grp['ret'].mean():+.1f}%")
