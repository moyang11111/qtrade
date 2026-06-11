"""QTrade 多策略模拟盘 —— 多策略并行 + 分仓。

用法:
  python scripts/paper_trading.py
  python scripts/paper_trading.py --strategies pullback_20d:500000,dual_ma:500000
"""

import os, sys, json, time, signal, argparse
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)

import pandas as pd, numpy as np

from qtrade.live_trading.broker import MockBroker, OrderSide, OrderType
import qtrade.strategy
from qtrade.strategy.registry import get_signal_generator, list_strategies

RUNNING = True
signal.signal(signal.SIGINT, lambda *_: globals().update(RUNNING=False) or print("\nShutting down..."))


def fetch_live_price(symbol: str) -> float:
    """直接获取单只股票的实时价格（不走行情缓存）。"""
    import urllib.request
    prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
    url = f"https://qt.gtimg.cn/q={prefix}{symbol}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=5)
        line = resp.read().decode("gbk").split(";")[0]
        if '"' in line:
            vals = line.split('"')[1].split("~")
            return float(vals[3]) if vals[3] else 0
    except Exception:
        pass
    return 0


def _create_feed(poll=3.0):
    try:
        from qtrade.live_trading.tdx_feed import TdxQuoteFeed
        f = TdxQuoteFeed(poll_interval=poll)
        if f.connect(): return f, "TDX"
    except: pass
    from qtrade.live_trading.baidu_feed import BaiduQuoteFeed
    return BaiduQuoteFeed(poll_interval=max(poll, 5.0)), "Tencent"


class StrategySlot:
    """一个策略 + 一份资金的独立账户。"""

    def __init__(self, name: str, capital: float, symbols: list[str], feed):
        self.name = name
        self.capital = capital
        self.symbols = symbols
        self.broker = MockBroker(initial_cash=capital)
        self.strategy = get_signal_generator(name)({})
        self.feed = feed
        self.dfs = {}
        self.entry_prices = {}
        self.trade_log = []
        self._use_sltp = name not in ("pullback_20d",)

    def load_data(self):
        cache = Path("data/cache")
        for sym in self.symbols:
            path = cache / f"{sym}.csv"
            if path.exists():
                df = pd.read_csv(path, parse_dates=["date"], index_col="date")
                df.columns = [c.lower() for c in df.columns]
                if all(c in df.columns for c in ["open","high","low","close","volume"]) and len(df) >= 120:
                    self.dfs[sym] = df

    def check(self):
        """检查信号，执行买卖。"""
        for sym in list(self.dfs.keys()):
            df = self.dfs[sym]
            pos = self.broker.get_position(sym)
            try:
                sig_df = self.strategy.generate_signals(df)
                latest = sig_df.iloc[-1]
                action = int(latest.get("signal_action", 0))

                if action == 1 and (pos is None or pos.quantity == 0):
                    price = self.feed.get_price(sym) or fetch_live_price(sym)
                    if price and price > 0:
                        self._buy(sym, price, float(latest.get("signal_strength", 0.5)))
                    else:
                        # 价格获取失败，等待下次检查
                        pass

                elif action == -1 and pos and pos.quantity > 0:
                    price = self.feed.get_price(sym) or fetch_live_price(sym)
                    if price and price > 0:
                        self._sell(sym, price, "signal")
            except Exception as e:
                print(f"  [{self.name}] {sym} check error: {e}")

    def _buy(self, sym, price, strength):
        acc = self.broker.get_account()
        pct = 0.95 * max(0.3, float(strength))
        qty = int(acc.cash * pct / price / 100) * 100
        if qty < 100:
            return
        self.broker.submit_order(symbol=sym, side=OrderSide.BUY, quantity=qty, order_type=OrderType.MARKET)
        self.entry_prices[sym] = price
        self.trade_log.append(dict(time=datetime.now().strftime("%H:%M:%S"), sym=sym, act="BUY",
                                   price=round(price,2), qty=qty, amt=round(qty*price,0)))
        print(f"\n  [{self.name}] 买入 {sym} @ {price:.2f} x{qty}股 = {qty*price:,.0f}元")

    def _sell(self, sym, price, reason):
        pos = self.broker.get_position(sym)
        if not pos or pos.quantity <= 0:
            return
        self.broker.submit_order(symbol=sym, side=OrderSide.SELL, quantity=pos.quantity, order_type=OrderType.MARKET)
        ep = self.entry_prices.get(sym, price)
        pnl = (price - ep) * pos.quantity
        self.trade_log.append(dict(time=datetime.now().strftime("%H:%M:%S"), sym=sym, act="SELL",
                                   price=round(price,2), qty=pos.quantity, amt=round(pos.quantity*price,0),
                                   pnl=round(pnl,0), reason=reason))
        tag = "+" if pnl > 0 else ""
        print(f"\n  [{self.name}] 卖出 {sym} @ {price:.2f}  盈亏 {pnl:+,.0f}元  [{reason}]")
        self.entry_prices.pop(sym, None)

    def on_tick(self, tick):
        sym, price = tick.symbol, tick.price
        if sym not in self.symbols:
            return
        self.broker.set_price(sym, price)
        # SL/TP for non-pullback_20d strategies
        if self._use_sltp and sym in self.entry_prices and self.entry_prices[sym] > 0:
            ep = self.entry_prices[sym]
            pos = self.broker.get_position(sym)
            if pos and pos.quantity > 0:
                if price >= ep * 1.10:
                    self._sell(sym, price, "止盈+10%")
                elif price <= ep * 0.95:
                    self._sell(sym, price, "止损-5%")


class MultiPaperTrader:
    """多策略并行的模拟盘。"""

    def __init__(self, slots_config: list[tuple[str, float, list[str]]], poll_interval=5.0):
        self.feed, self.feed_type = _create_feed(poll_interval)
        self.slots = []

        all_symbols = set()
        for name, capital, syms in slots_config:
            slot = StrategySlot(name, capital, syms, self.feed)
            slot.load_data()
            self.slots.append(slot)
            all_symbols.update(syms)

        self.feed.on_tick(self._on_tick)
        self.feed.subscribe(list(all_symbols))
        self.total_trades = 0

    def _on_tick(self, tick):
        for slot in self.slots:
            slot.on_tick(tick)

    def check_all(self):
        for slot in self.slots:
            slot.check()

    def dashboard(self):
        """多策略仪表盘。"""
        total_equity = 0
        total_cash = 0
        total_trades = 0
        total_positions = 0

        print(f"\n{'='*65}")
        print(f"  QTrade 多策略模拟盘  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*65}")

        for slot in self.slots:
            acc = slot.broker.get_account()
            eq = acc.portfolio_value
            ret = eq - slot.capital
            ret_pct = (eq / slot.capital - 1) * 100
            positions = [p for p in slot.broker.get_positions() if p.quantity > 0]
            sig_count = sum((slot.strategy.generate_signals(slot.dfs[sym]).iloc[-1]["signal_action"] == 1)
                           for sym in slot.dfs.keys() if sym in slot.dfs)

            print(f"\n  [{slot.name}] 资金={slot.capital/10000:.0f}万  权益={eq:,.0f}  "
                  f"收益={ret:+,.0f}({ret_pct:+.1f}%)  持仓={len(positions)}只  "
                  f"交易={len(slot.trade_log)}笔  当前买入信号={sig_count}")

            if positions:
                print(f"    {'代码':<10} {'现价':>8} {'持仓':>6} {'成本':>8} {'浮盈':>10} {'盈%':>7}")
                print(f"    {'-'*10} {'-'*8} {'-'*6} {'-'*8} {'-'*10} {'-'*7}")
                shown = 0
                for p in positions:
                    shown += 1
                    if shown > 20:
                        print(f"    ... 还有 {len(positions)-20} 个")
                        break
                    cur = self.feed.get_price(p.symbol) or p.avg_price
                    upnl = (cur - p.avg_price) * p.quantity
                    upnl_pct = (cur / p.avg_price - 1) * 100 if p.avg_price > 0 else 0
                    print(f"    {p.symbol:<10} {cur:>8.2f} {p.quantity:>6} {p.avg_price:>8.2f} {upnl:>+10,.0f} {upnl_pct:>+6.1f}%")

            total_equity += eq
            total_cash += acc.cash
            total_trades += len(slot.trade_log)
            total_positions += len(positions)

        print(f"\n  ── 合计 ──")
        print(f"  总权益: {total_equity:,.0f}  总现金: {total_cash:,.0f}  "
              f"总持仓: {total_positions}只  总交易: {total_trades}笔")

    def run(self, interval=30.0):
        print(f"\n{'='*65}")
        print(f"  QTrade 多策略模拟盘")
        for slot in self.slots:
            symbols_str = ','.join(slot.symbols[:3])
            if len(slot.symbols) > 3:
                symbols_str += f'...({len(slot.symbols)}只)'
            print(f"  [{slot.name}]  {slot.capital/10000:.0f}万  监控: {symbols_str}")
        print(f"{'='*65}")

        self.dashboard()

        last_db = time.time()
        last_check = 0  # 立即执行首次检查

        try:
            while RUNNING:
                time.sleep(0.5)
                now = time.time()
                if now - last_db >= interval:
                    self.dashboard()
                    last_db = now
                if now - last_check >= 30:  # 每30秒检查信号
                    self.check_all()
                    last_check = now
        except KeyboardInterrupt:
            pass
        finally:
            print("\n\nShutting down...")
            self.feed.disconnect()
            self.dashboard()

            # Print summary
            print(f"\n=== Session Summary ===")
            for slot in self.slots:
                w = sum(1 for t in slot.trade_log if t.get("pnl", 0) > 0)
                t = sum(1 for t in slot.trade_log if t.get("act") == "SELL")
                acc = slot.broker.get_account()
                print(f"  [{slot.name}] {len(slot.trade_log)}笔  卖出{t}次  "
                      f"胜{w}({w/t*100:.0f}% if t else '-')  "
                      f"权益={acc.portfolio_value:,.0f}({acc.portfolio_value/slot.capital*100-100:+.1f}%)")
            print("Done.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", default="pullback_20d:500000,pullback_deep:500000",
                        help="策略:资金, 如 pullback_20d:500000,dual_ma:300000")
    parser.add_argument("--symbols", default="002580,000066,002297,002757,600130,002328,002176",
                        help="监控股票（逗号分隔），--all 监控全市场")
    parser.add_argument("--all", action="store_true", help="监控所有缓存的主板股票")
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--poll", type=float, default=5)
    parser.add_argument("--list-strategies", action="store_true")
    args = parser.parse_args()

    if args.list_strategies:
        print(f"Available: {', '.join(list_strategies())}")
        return

    # --all 默认全市场，--symbols 指定则覆盖
    if args.symbols != parser.get_default("symbols"):
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.all:
        cache = Path("data/cache")
        symbols = sorted([f.stem for f in cache.glob("*.csv") if f.stem.startswith(("60", "00"))])
    else:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    if len(symbols) > 100:
        print(f"全市场监控: {len(symbols)} 只主板股票，加载较慢请等待...")

    # 解析策略配置: name:capital,name:capital
    slots_config = []
    for part in args.strategies.split(","):
        name, cap = part.split(":")
        slots_config.append((name.strip(), float(cap), symbols))

    trader = MultiPaperTrader(slots_config, args.poll)
    trader.run(args.interval)


if __name__ == "__main__":
    main()
