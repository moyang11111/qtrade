"""龙虎榜（LHB）数据获取模块。

基于 akshare 东方财富接口，获取龙虎榜上榜股票列表，
支持按热度（涨幅、净买额、上榜次数）筛选候选股票池。
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger("qtrade.data.lhb")


def get_lhb_stocks(
    start_date: str,
    end_date: Optional[str] = None,
    *,
    min_net_buy: float = 0,
    min_rise: float = 0,
) -> pd.DataFrame:
    """获取龙虎榜每日上榜股票详情。

    参数
    ----------
    start_date : str
        起始日期，格式 YYYYMMDD，如 "20250301"
    end_date : str, optional
        截止日期，默认与 start_date 相同
    min_net_buy : float
        净买额下限（元），默认 0 表示仅保留净买入的股票
    min_rise : float
        涨跌幅下限（%），默认 0 表示仅保留上涨的股票

    返回
    ----------
    pd.DataFrame
        字段：代码, 名称, 上榜日, 涨跌幅, 龙虎榜净买额, 龙虎榜买入额,
        龙虎榜卖出额, 龙虎榜成交额, 市场总成交额, 净买额占总成交比,
        换手率, 流通市值, 上榜原因, 上榜后1/2/5/10日涨跌幅

        注意：已按 涨跌幅 降序排列。
    """
    try:
        import akshare as ak
    except ImportError:
        raise ImportError(
            "龙虎榜数据需要 akshare 库。请运行: pip install akshare"
        )

    if end_date is None:
        end_date = start_date

    logger.info("获取龙虎榜数据: %s ~ %s", start_date, end_date)

    try:
        df = ak.stock_lhb_detail_em(start_date=start_date, end_date=end_date)
    except Exception:
        logger.warning("akshare 龙虎榜接口异常，尝试单日获取")
        # 跨月查询可能失败，逐天重试
        date_range = pd.date_range(start=start_date, end=end_date, freq="D")
        frames = []
        for d in date_range:
            ds = d.strftime("%Y%m%d")
            try:
                day_df = ak.stock_lhb_detail_em(start_date=ds, end_date=ds)
                frames.append(day_df)
            except Exception:
                logger.debug("日期 %s 龙虎榜获取失败，跳过", ds)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    if df.empty:
        logger.warning("龙虎榜无数据: %s ~ %s", start_date, end_date)
        return df

    # ── 列名标准化 ──
    rename_map = {
        "代码": "code",
        "名称": "name",
        "上榜日": "listing_date",
        "涨跌幅": "pct_change",
        "龙虎榜净买额": "net_buy",
        "龙虎榜买入额": "buy_amount",
        "龙虎榜卖出额": "sell_amount",
        "龙虎榜成交额": "lhb_volume",
        "市场总成交额": "total_volume",
        "净买额占总成交比": "net_buy_pct",
        "成交额占总成交比": "lhb_volume_pct",
        "换手率": "turnover",
        "流通市值": "float_mv",
        "上榜原因": "reason",
        "上榜后1日": "post_1d",
        "上榜后2日": "post_2d",
        "上榜后5日": "post_5d",
        "上榜后10日": "post_10d",
    }
    # 只保留存在且不是空列的映射
    rename_map = {k: v for k, v in rename_map.items() if k in df.columns}
    df = df.rename(columns=rename_map)

    # ── 数值清洗 ──
    numeric_cols = ["pct_change", "net_buy", "buy_amount", "sell_amount",
                    "lhb_volume", "total_volume", "turnover", "float_mv",
                    "post_1d", "post_2d", "post_5d", "post_10d"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # ── 过滤 ──
    before = len(df)

    if "net_buy" in df.columns and min_net_buy > 0:
        df = df[df["net_buy"] >= min_net_buy]

    if "pct_change" in df.columns:
        df = df[df["pct_change"] >= min_rise]

    logger.info("龙虎榜过滤: %d → %d 只 (净买≥%.0e, 涨幅≥%.1f%%)",
                before, len(df), min_net_buy, min_rise)

    # 按涨跌幅降序排列
    if "pct_change" in df.columns:
        df = df.sort_values("pct_change", ascending=False)

    return df.reset_index(drop=True)


def get_lhb_hot_stocks(
    days: int = 10,
    *,
    min_rise: float = 5.0,
    min_net_buy: float = 3_000_000,  # 净买额 ≥ 300万
    max_stocks: int = 50,
) -> list[str]:
    """获取龙虎榜"热度高"的候选股票。

    筛选条件：
    - 上榜日涨幅 ≥ min_rise（默认 5%）
    - 净买额 ≥ min_net_buy（默认 300 万元）
    - 合并去重后取前 max_stocks 只

    参数
    ----------
    days : int
        回溯天数，默认 10 个交易日
    min_rise : float
        上榜日最低涨幅（%）
    min_net_buy : float
        最低净买入金额（元）
    max_stocks : int
        返回的最多股票数

    返回
    ----------
    list[str]
        股票代码列表，如 ["002901", "300750", ...]
    """
    from datetime import datetime, timedelta

    end = datetime.now()
    start = end - timedelta(days=max(days + 10, 15))  # 多取几天防止周末节假日

    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")

    df = get_lhb_stocks(
        start_str, end_str,
        min_rise=min_rise,
        min_net_buy=min_net_buy,
    )

    if df.empty:
        logger.warning("未找到符合条件的龙虎榜强股")
        return []

    # 按上榜次数排序，取热度最高的
    if "code" in df.columns:
        counts = df["code"].value_counts().head(max_stocks)
        stocks = counts.index.tolist()
    else:
        stocks = df["code"].unique().tolist()[:max_stocks] if "code" in df.columns else []

    logger.info("龙虎榜强股池: %d 只 (涨幅≥%.1f%%, 净买≥%.0e, %d 日内)",
                len(stocks), min_rise, min_net_buy, days)
    return stocks


def get_lhb_stock_statistics(
    period: str = "近一月",
) -> pd.DataFrame:
    """获取龙虎榜个股统计（上榜次数、机构参与等）。

    参数
    ----------
    period : str
        统计周期，可选 {"近一月", "近三月", "近六月", "近一年"}

    返回
    ----------
    pd.DataFrame
        字段：代码, 名称, 最近上榜日, 收盘价, 涨跌幅, 上榜次数,
        龙虎榜净买额, 机构买入净额, 机构买入总额, 机构卖出总额, 等
    """
    try:
        import akshare as ak
    except ImportError:
        raise ImportError("需要 akshare。请运行: pip install akshare")

    logger.info("获取龙虎榜个股统计: %s", period)
    df = ak.stock_lhb_stock_statistic_em(symbol=period)

    if df.empty:
        return df

    # 标准化
    rename_map = {
        "代码": "code",
        "名称": "name",
        "最近上榜日": "last_date",
        "收盘价": "close",
        "涨跌幅": "pct_change",
        "上榜次数": "listing_count",
        "龙虎榜净买额": "net_buy",
        "龙虎榜买入额": "buy_amount",
        "龙虎榜卖出额": "sell_amount",
        "龙虎榜总成交额": "lhb_volume",
        "买方机构次数": "buy_inst_count",
        "卖方机构次数": "sell_inst_count",
        "机构买入净额": "inst_net_buy",
        "机构买入总额": "inst_buy",
        "机构卖出总额": "inst_sell",
    }
    rename_map = {k: v for k, v in rename_map.items() if k in df.columns}
    df = df.rename(columns=rename_map)

    return df.reset_index(drop=True)
