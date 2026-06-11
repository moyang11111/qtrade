"""QTrade Paper Trading — Zero-dependency A-share paper trading system.

Uses Tencent real-time quotes + MockBroker to simulate trading.
Strategy: PullbackDeepSignal (deep pullback 30-45% OR BB mid touch)
Exit: Take-profit 10% / Stop-loss 5%

Usage:
  python scripts/paper_trading.py
  python scripts/paper_trading.py --symbols 600156,002082 --capital 500000
"""

import os
import sys
import time
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
import signal
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).parent.parent
os.chdir(str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import json

import pandas as pd
import numpy as np

from qtrade.live_trading.broker import MockBroker, OrderSide, OrderType
import qtrade.strategy  # 触发所有策略注册
from qtrade.strategy.registry import get_signal_generator, list_strategies

# 行情源：优先用通达信（pytdx TCP），失败则用腾讯 HTTP
def _create_feed(poll_interval=3.0):
    # TDX: 快速尝试（本机网络好，秒连）
    try:
        from qtrade.live_trading.tdx_feed import TdxQuoteFeed
        feed = TdxQuoteFeed(poll_interval=poll_interval)
        if feed.connect():
            print("  [行情] 通达信 TDX")
            return feed, "TDX"
        feed.disconnect()
    except Exception as e:
        pass  # TDX 不可用时静默回退
    # Fallback: 腾讯 HTTP
    from qtrade.live_trading.baidu_feed import BaiduQuoteFeed
    print("  [行情] 腾讯 HTTP")
    return BaiduQuoteFeed(poll_interval=max(poll_interval, 5.0)), "Tencent"

RUNNING = True

def on_sigint(sig, frame):
    global RUNNING
    RUNNING = False
    print("\nShutting down...")

signal.signal(signal.SIGINT, on_sigint)


class PaperTrader:
    """Paper trading system."""

    def __init__(self, symbols, capital=1_000_000, poll_interval=5.0, strategy_name="pullback_20d"):
        self.symbols = symbols
        self.capital = capital
        self.broker = MockBroker(initial_cash=capital)
        self.feed, self.feed_type = _create_feed(poll_interval)

        # 按名称加载策略，默认 Pullback20D
        strategy_cls = get_signal_generator(strategy_name)
        self.strategy = strategy_cls({})
        self.strategy_name = strategy_name
        # Pullback20D 不自带止盈止损，其他策略用 SignalFollower 的 SL/TP
        self._use_sltp = strategy_name not in ("pullback_20d",)

        self.dfs = {}
        self.entry_prices = {}
        self.highest = {}
        self.trade_log = []

        self.take_profit_pct = 0.10
        self.stop_loss_pct = 0.05

        # 持久化文件
        self.state_file = Path("data/paper_state.json")

    def save_state(self):
        """保存当前状态到磁盘。"""
        acc = self.broker.get_account()
        positions = []
        for p in self.broker.get_positions():
            if p.quantity > 0:
                positions.append({
                    "symbol": p.symbol,
                    "quantity": p.quantity,
                    "avg_price": p.avg_price,
                })
        data = {
            "cash": acc.cash,
            "positions": positions,
            "entry_prices": self.entry_prices,
            "highest": self.highest,
            "trades": self.trade_log,
            "capital": self.capital,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_state(self):
        """从磁盘加载上次状态。返回 True 表示成功恢复。"""
        if not self.state_file.exists():
            return False
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 恢复现金
            saved_cash = data.get("cash", 0)
            # 重建 broker（MocBroker 不能直接设 cash，通过买入卖出重建）
            self.broker = MockBroker(initial_cash=self.capital)
            self.broker.cash = saved_cash  # 直接设内部 cash
            # 恢复持仓
            for pos in data.get("positions", []):
                sym = pos["symbol"]
                qty = pos["quantity"]
                avg = pos["avg_price"]
                # 模拟持仓重建（通过手动操作 positions dict）
                self.broker.submit_order(
                    symbol=sym, side=OrderSide.BUY, quantity=qty,
                    order_type=OrderType.MARKET)
                # 调整均价
                if sym in self.broker.positions:
                    self.broker.positions[sym].avg_price = avg
            # 恢复入场价记录
            self.entry_prices = data.get("entry_prices", {})
            self.highest = data.get("highest", {})
            # 恢复交易记录
            self.trade_log = data.get("trades", [])
            print(f"  [恢复] 上次状态: {data.get('saved_at', '未知')}")
            print(f"         现金={saved_cash:,.0f}  持仓={len(data.get('positions',[]))}只  交易={len(self.trade_log)}笔")
            return True
        except Exception as e:
            print(f"  [LOAD] Failed to restore state: {e}")
            return False

    def load_data(self):
        """Load historical data from CSV cache."""
        cache_dir = Path("data/cache")
        for sym in self.symbols:
            path = cache_dir / f"{sym}.csv"
            if not path.exists():
                print(f"  {sym}: no cache, skipping")
                continue
            try:
                df = pd.read_csv(path, parse_dates=["date"], index_col="date")
                df.columns = [c.lower() for c in df.columns]
                need = {"open", "high", "low", "close", "volume"}
                if not need.issubset(set(df.columns)):
                    print(f"  {sym}: missing cols, skipping")
                    continue
                if len(df) >= 120:
                    self.dfs[sym] = df
                    print(f"  {sym}: OK {len(df)} rows [{df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}]")
                else:
                    print(f"  {sym}: too few rows ({len(df)}), skipping")
            except Exception as e:
                print(f"  {sym}: load error: {e}")

    def on_tick(self, tick):
        """Process real-time tick."""
        sym = tick.symbol
        price = tick.price
        self.broker.set_price(sym, price)

        # Check SL/TP for existing positions (disabled for Pullback20D)
        if self._use_sltp and sym in self.entry_prices and self.entry_prices[sym] > 0:
            entry = self.entry_prices[sym]
            self.highest[sym] = max(self.highest.get(sym, entry), price)

            # Take profit
            if price >= entry * (1 + self.take_profit_pct):
                pos = self.broker.get_position(sym)
                if pos and pos.quantity > 0:
                    self._sell(sym, price, "TP+10%")
                    return

            # Stop loss
            if price <= entry * (1 - self.stop_loss_pct):
                pos = self.broker.get_position(sym)
                if pos and pos.quantity > 0:
                    self._sell(sym, price, "SL-5%")
                    return

    def check_signals(self):
        """Run strategy on latest data, execute buy AND sell orders."""
        for sym in list(self.dfs.keys()):
            df = self.dfs[sym]
            pos = self.broker.get_position(sym)

            try:
                sig_df = self.strategy.generate_signals(df)
                latest = sig_df.iloc[-1]
                action = int(latest.get("signal_action", 0))

                if action == 1 and (pos is None or pos.quantity == 0):
                    # Buy signal + no existing position
                    price = self.feed.get_price(sym)
                    if price and price > 0:
                        strength = float(latest.get("signal_strength", 0.5))
                        self._buy(sym, price, strength)

                elif action == -1 and pos and pos.quantity > 0:
                    # Sell signal + has position (Pullback20D triggers this at t+20)
                    price = self.feed.get_price(sym)
                    if price and price > 0:
                        self._sell(sym, price, "signal")
            except Exception:
                pass

    def _buy(self, sym, price, strength):
        """Execute buy order."""
        acc = self.broker.get_account()
        pct = 0.95 * max(0.3, float(strength))
        qty = int(acc.cash * pct / price / 100) * 100
        if qty < 100:
            return

        self.broker.submit_order(symbol=sym, side=OrderSide.BUY, quantity=qty, order_type=OrderType.MARKET)
        self.entry_prices[sym] = price
        self.highest[sym] = price

        rec = dict(time=datetime.now().strftime("%H:%M:%S"), sym=sym, act="BUY",
                   price=price, qty=qty, amt=qty*price, reason=f"信号强度={strength:.2f}")
        self.trade_log.append(rec)
        print(f"\n  >>> 买入 {sym}  价格 {price:.2f}  数量 {qty}股  金额 {qty*price:,.0f}元")
        self._print_positions()

    def _sell(self, sym, price, reason):
        """Execute sell order."""
        pos = self.broker.get_position(sym)
        if not pos or pos.quantity <= 0:
            return

        self.broker.submit_order(symbol=sym, side=OrderSide.SELL, quantity=pos.quantity, order_type=OrderType.MARKET)

        ep = self.entry_prices.get(sym, price)
        pnl = (price - ep) * pos.quantity
        pnl_pct = (price / ep - 1) * 100

        rec = dict(time=datetime.now().strftime("%H:%M:%S"), sym=sym, act="SELL",
                   price=price, qty=pos.quantity, amt=pos.quantity*price, pnl=pnl, pnl_pct=pnl_pct, reason=reason)
        self.trade_log.append(rec)

        tag = "盈利" if pnl > 0 else "亏损"
        print(f"\n  <<< 卖出 {sym}  {tag}  价格 {price:.2f}  盈亏 {pnl:+,.0f}元 ({pnl_pct:+.1f}%)  原因: {reason}")
        self._print_positions()

        self.entry_prices.pop(sym, None)
        self.highest.pop(sym, None)

    def _print_positions(self):
        """Print current positions table."""
        positions = [p for p in self.broker.get_positions() if p.quantity > 0]
        if not positions:
            print("  [空仓]")
            return
        print(f"\n  {'代码':<10} {'现价':>8} {'持仓':>6} {'市值':>10} {'成本':>8} {'浮动盈亏':>10} {'盈亏%':>7}")
        print(f"  {'-'*10} {'-'*8} {'-'*6} {'-'*10} {'-'*8} {'-'*10} {'-'*7}")
        for p in positions:
            cur = self.feed.get_price(p.symbol) or p.avg_price
            mv = p.quantity * cur
            upnl = (cur - p.avg_price) * p.quantity
            upnl_pct = (cur / p.avg_price - 1) * 100 if p.avg_price > 0 else 0
            print(f"  {p.symbol:<10} {cur:>8.2f} {p.quantity:>6} {mv:>10,.0f} "
                  f"{p.avg_price:>8.2f} {upnl:>+10,.0f} {upnl_pct:>+6.1f}%")
        print()

    def dashboard(self):
        """Print status dashboard."""
        acc = self.broker.get_account()
        positions = [p for p in self.broker.get_positions() if p.quantity > 0]
        ret = acc.portfolio_value - self.capital
        ret_pct = (acc.portfolio_value / self.capital - 1) * 100

        print(f"\n{'='*60}")
        print(f"  QTrade 模拟盘  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  初始资金 {self.capital:,.0f}  当前权益 {acc.portfolio_value:,.0f}  "
              f"总收益 {ret:+,.0f} ({ret_pct:+.1f}%)")
        print(f"  可用现金 {acc.cash:,.0f}  持仓市值 {acc.long_market_value:,.0f}  "
              f"持仓 {len(positions)}只  交易 {len(self.trade_log)}笔")

        if positions:
            print()
            self._print_positions()
        else:
            print("\n  [等待策略信号中...]")

        if self.trade_log:
            print(f"  最近成交:")
            for t in self.trade_log[-5:]:
                if t["act"] == "BUY":
                    print(f"    买入 {t['time']} {t['sym']} @{t['price']:.2f} x{t['qty']}股  {t.get('reason','')}")
                else:
                    pnl = t.get('pnl', 0)
                    emoji = "+" if pnl > 0 else ""
                    print(f"    卖出 {t['time']} {t['sym']} @{t['price']:.2f}  盈亏 {pnl:+,.0f}元")

    def run(self, interval=30.0):
        """Main loop."""
        print(f"\n{'='*60}")
        print(f"  QTrade 模拟盘 — {self.strategy_name} 策略")
        print(f"  监控股票: {', '.join(self.symbols)}")
        print(f"  买入: 回调15-40% + 成交量收缩 < 0.7")
        print(f"  卖出: 持有20个交易日 / 由策略信号触发")
        print(f"{'='*60}")

        self.load_data()
        print(f"  有效股票: {len(self.dfs)}只")

        # 尝试恢复上次状态
        restored = self.load_state()
        if not restored:
            print("  [新建] 初始资金开始\n")
        else:
            print()

        self.broker.connect()
        self.feed.on_tick(self.on_tick)
        self.feed.connect()
        self.feed.subscribe(list(self.dfs.keys()))

        print("  等待行情...")
        time.sleep(3)

        # Initial signal check
        self.check_signals()

        last_dashboard = time.time()
        last_signal = time.time()
        tick_count = 0

        try:
            while RUNNING:
                time.sleep(0.5)
                tick_count += 1

                if time.time() - last_dashboard >= interval:
                    self.dashboard()
                    last_dashboard = time.time()

                if time.time() - last_signal >= 60:
                    self.check_signals()
                    last_signal = time.time()

        except KeyboardInterrupt:
            pass
        finally:
            print("\n\n  正在关闭...")
            self.save_state()
            print(f"  状态已保存到 {self.state_file}")
            self.feed.disconnect()
            self.broker.disconnect()
            self.dashboard()
            print(f"\n  共 {len(self.trade_log)} 笔交易")
            print("  已退出")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="002580,000066,002297,002757,600130,002328,002176")
    parser.add_argument("--strategy", default="pullback_20d", help=f"策略名称: {', '.join(list_strategies())}")
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--poll", type=float, default=5)
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    trader = PaperTrader(symbols, args.capital, args.poll, args.strategy)
    trader.run(args.interval)


if __name__ == "__main__":
    main()
