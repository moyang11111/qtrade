"""
使用 a-stock-data skill 进行数据驱动选股 + 回测
策略：
1. 使用东财 push2 接口获取中证500成分股（实时行情）
2. 拉取资金流向数据筛选强势股
3. 下载历史K线数据
4. 运行策略回测
5. 分析结果并优化策略
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import time
import json

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

def get_csi500_stocks_push2():
    """使用东财 push2 获取中证500成分股（实时行情）"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "600", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "b:BK0477",  # 中证500板块代码
        "fields": "f2,f3,f4,f12,f13,f14,f20,f21,f62",
    }
    headers = {"User-Agent": UA}

    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        d = r.json()
    except Exception as e:
        print(f"[ERROR] push2 请求失败: {e}")
        return []

    items = d.get("data", {}).get("diff", [])
    if not items:
        print("[WARN] 未获取到成分股数据，尝试备选方案")
        return get_csi500_stocks_fallback()

    stocks = []
    for item in items:
        code = str(item.get("f12", ""))
        name = item.get("f14", "")
        price_raw = item.get("f2", 0)
        change_pct = item.get("f3", 0)
        main_net = item.get("f62", 0)  # 主力净流入

        # 转换价格为float
        try:
            price = float(price_raw) if price_raw and str(price_raw) != "-" else 0
        except (ValueError, TypeError):
            price = 0

        if code and name and price > 0:
            stocks.append({
                "code": code,
                "name": name,
                "price": price,
                "change_pct": change_pct,
                "main_net_today": main_net,
            })

    return stocks

def get_csi500_stocks_fallback():
    """备选方案：预定义中证500成分股（选取多行业代表性股票）"""
    stocks = [
        # 科技
        {"code": "300033", "name": "同花顺"},
        {"code": "002049", "name": "紫光国微"},
        {"code": "688012", "name": "中微公司"},
        {"code": "002371", "name": "北方华创"},
        {"code": "300394", "name": "天孚通信"},
        # 新能源
        {"code": "300750", "name": "宁德时代"},
        {"code": "601012", "name": "隆基绿能"},
        {"code": "300274", "name": "阳光电源"},
        {"code": "002709", "name": "天赐材料"},
        {"code": "300014", "name": "亿纬锂能"},
        # 医药
        {"code": "300760", "name": "迈瑞医疗"},
        {"code": "300347", "name": "泰格医药"},
        {"code": "300122", "name": "智飞生物"},
        {"code": "002007", "name": "华兰生物"},
        {"code": "300142", "name": "沃森生物"},
        # 制造/半导体
        {"code": "002812", "name": "恩捷股份"},
        {"code": "603986", "name": "兆易创新"},
        {"code": "688036", "name": "传音控股"},
        {"code": "002463", "name": "沪电股份"},
        {"code": "603501", "name": "韦尔股份"},
        # 消费
        {"code": "000858", "name": "五粮液"},
        {"code": "600519", "name": "贵州茅台"},
        {"code": "002714", "name": "牧原股份"},
        {"code": "600887", "name": "伊利股份"},
        {"code": "000568", "name": "泸州老窖"},
        # 金融
        {"code": "601688", "name": "华泰证券"},
        {"code": "600030", "name": "中信证券"},
        {"code": "601318", "name": "中国平安"},
        {"code": "600036", "name": "招商银行"},
        {"code": "601166", "name": "兴业银行"},
    ]
    return stocks

def get_stock_fund_flow_120d(code):
    """获取个股120日资金流（东财push2his）"""
    market_code = 1 if code.startswith("6") else 0
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "secid": f"{market_code}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
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
    except Exception as e:
        print(f"[WARN] {code} 资金流请求失败: {e}")
        return []

    klines = d.get("data", {}).get("klines", [])
    rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 7:
            rows.append({
                "date": parts[0],
                "main_net": float(parts[1]) if parts[1] != "-" else 0,
                "small_net": float(parts[2]) if parts[2] != "-" else 0,
                "mid_net": float(parts[3]) if parts[3] != "-" else 0,
                "large_net": float(parts[4]) if parts[4] != "-" else 0,
                "super_net": float(parts[5]) if parts[5] != "-" else 0,
            })
    return rows

def fetch_kline_mootdx(code, days=800):
    """使用 mootdx 获取K线数据"""
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market='std')
        market = 1 if code.startswith("6") else 0
        klines = client.bars(symbol=code, category=4, count=days)
        if klines is None or len(klines) == 0:
            return None

        df = pd.DataFrame(klines)
        df = df[['open', 'high', 'low', 'close', 'vol', 'datetime']]
        df.columns = ['open', 'high', 'low', 'close', 'volume', 'date']
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        df = df.sort_index()
        return df
    except Exception as e:
        print(f"[WARN] mootdx K线获取失败 {code}: {e}")
        return None

def select_strong_stocks(stocks, top_n=20):
    """基于资金流筛选强势股"""
    results = []
    for i, stock in enumerate(stocks[:50]):
        code = stock['code']
        name = stock['name']

        print(f"[{i+1}/50] 分析 {code} {name}...", end=" ")

        # 获取120日资金流
        flow_data = get_stock_fund_flow_120d(code)
        if not flow_data:
            print("无资金流数据")
            continue

        # 计算近20日主力净流入
        recent_20 = flow_data[-20:]
        total_main = sum(d["main_net"] for d in recent_20)
        total_super = sum(d["super_net"] for d in recent_20)

        # 计算资金流趋势（近20日 vs 前20日）
        if len(flow_data) >= 40:
            prev_20 = flow_data[-40:-20]
            prev_main = sum(d["main_net"] for d in prev_20)
            trend = total_main - prev_main
        else:
            trend = 0

        results.append({
            "code": code,
            "name": name,
            "main_net_20d": total_main,
            "super_net_20d": total_super,
            "trend": trend,
        })

        print(f"主力净流入={total_main/1e8:.2f}亿 趋势={trend/1e8:.2f}亿")
        time.sleep(0.1)

    if not results:
        print("[ERROR] 未获取到任何资金流数据")
        return stocks[:top_n]  # 返回原始列表

    # 按主力净流入排序
    df = pd.DataFrame(results)
    df = df.sort_values("main_net_20d", ascending=False)

    print(f"\n=== 资金流 TOP {top_n} ===")
    print(df.head(top_n).to_string())

    return df.head(top_n).to_dict('records')

def main():
    print("=" * 80)
    print("A股数据驱动选股 + 回测（使用 a-stock-data skill）")
    print("=" * 80)

    # 1. 获取中证500成分股
    print("\n[1/4] 获取中证500成分股...")
    constituents = get_csi500_stocks_push2()
    print(f"共 {len(constituents)} 只股票")

    if len(constituents) == 0:
        print("[ERROR] 未获取到成分股，退出")
        return []

    # 2. 基于资金流筛选强势股
    print("\n[2/4] 基于120日资金流筛选强势股...")
    strong_stocks = select_strong_stocks(constituents, top_n=15)

    # 3. 下载K线数据
    print("\n[3/4] 下载历史K线数据...")
    cache_dir = Path("data/cache")
    cache_dir.mkdir(parents=True, exist_ok=True)

    downloaded = []
    for stock in strong_stocks[:15]:
        code = stock['code']
        name = stock['name']
        print(f"下载 {code} {name}...", end=" ")

        df = fetch_kline_mootdx(code, days=800)
        if df is not None and len(df) > 100:
            # 保存数据
            cache_file = cache_dir / f"{code}.csv"
            df.to_csv(cache_file)
            print(f"OK {len(df)} bars")
            downloaded.append(stock)
        else:
            print("FAIL")
        time.sleep(0.2)

    print(f"\n成功下载 {len(downloaded)} 只股票")

    # 保存选股结果
    result_file = Path("results") / "strong_stocks_selection.csv"
    result_file.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(downloaded).to_csv(result_file, index=False)
    print(f"选股结果已保存: {result_file}")

    # 4. 输出股票代码供回测使用
    print("\n[4/4] 选中的股票代码:")
    codes = [s['code'] for s in downloaded]
    print(f"SYMBOLS = {codes}")

    return downloaded

if __name__ == "__main__":
    main()
