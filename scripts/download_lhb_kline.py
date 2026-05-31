"""从百度股市通 API 批量下载龙虎榜股票K线数据到本地缓存。"""

import os
import csv
import time
import requests
from datetime import datetime
from pathlib import Path

CACHE_DIR = Path(r"C:\Users\ASUS\qtrade\data\cache")
CACHE_DIR.mkdir(exist_ok=True)

BAIDU_KL_URL = "https://finance.pae.baidu.com/selfselect/getstockquotation"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/vnd.finance-web.v1+json",
    "Origin": "https://gushitong.baidu.com",
    "Referer": "https://gushitong.baidu.com/",
}

def download_baidu_kline(code: str) -> tuple[int, str]:
    """下载单只股票的百度K线，保存为CSV。返回 (行数, 日期范围)。"""
    params = {
        "all": "1", "isIndex": "false", "isBk": "false",
        "isBlock": "false", "isFutures": "false", "isStock": "true",
        "newFormat": "1", "group": "quotation_kline_ab",
        "finClientType": "pc", "code": code, "ktype": "1",
    }
    try:
        r = requests.get(BAIDU_KL_URL, params=params, headers=HEADERS, timeout=15)
        d = r.json()
        if d.get("ResultCode") != "0":
            return 0, f"API error: {d.get('ResultCode')}"

        md = d["Result"]["newMarketData"]
        keys = md["keys"]
        raw = md["marketData"]

        rows = []
        for line in raw.split(";"):
            if not line.strip():
                continue
            vals = line.split(",")
            if len(vals) < 6:
                continue
            # 解析字段
            ts = int(vals[0])
            date_str = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            o = float(vals[2]) if vals[2] != "--" and vals[2] else 0
            c = float(vals[3]) if vals[3] != "--" and vals[3] else 0
            v = float(vals[4]) if vals[4] != "--" and vals[4] else 0
            h = float(vals[5]) if vals[5] != "--" and vals[5] else 0
            l = float(vals[6]) if vals[6] != "--" and vals[6] else 0
            rows.append([date_str, o, h, l, c, v])

        if not rows:
            return 0, "empty data"

        # 写入CSV
        path = CACHE_DIR / f"{code}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "open", "high", "low", "close", "volume"])
            w.writerows(rows)

        date_range = f"{rows[0][0][:10]} ~ {rows[-1][0][:10]}"
        return len(rows), date_range

    except Exception as e:
        return 0, str(e)[:80]


# ─── 龙虎榜股票列表（从 get_lhb_hot_stocks 获取） ───
LHB_STOCKS = [
    "600719", "600172", "002297", "300632", "600726",
    "002342", "600156", "002081", "300067", "300283",
    "603459", "688811", "688260", "603779", "001257",
    "001259", "301531", "301696", "300721", "301666",
    "688143", "600396", "002428", "300841", "300534",
]

print(f"开始下载 {len(LHB_STOCKS)} 只龙虎榜股票...")
print()

ok = 0
skip = 0
fail = 0

for i, code in enumerate(LHB_STOCKS, 1):
    # 如果已缓存
    if (CACHE_DIR / f"{code}.csv").exists():
        print(f"  [{i:2d}/{len(LHB_STOCKS)}] {code} ... cached!")
        skip += 1
        continue

    rows, info = download_baidu_kline(code)
    if rows >= 120:
        status = f"OK {rows} rows ({info})"
        ok += 1
    elif rows > 0:
        status = f"SKIP only {rows} rows [{info}]"
        skip += 1
    else:
        status = f"FAIL {info}"
        fail += 1

    print(f"  [{i:2d}/{len(LHB_STOCKS)}] {code} ... {status}")
    time.sleep(0.3)  # 避免限频

print()
print(f"Done: {ok} OK, {skip} skip/cached, {fail} fail")
