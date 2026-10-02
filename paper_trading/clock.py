"""Exchange calendar and fresh execution quotes, independent of display history."""
from datetime import datetime, time
import math
import threading


def load_calendar():
    from qtrade_adapters.deepseek_harness.snapshot_pipeline import load_trade_calendar_dates
    return load_trade_calendar_dates()


class TradingClock:
    def __init__(self, now=None, calendar_loader=None):
        self.now = now or datetime.now
        self.loader = calendar_loader or load_calendar
        self._dates = ()
        self._loaded = None
        self._lock = threading.RLock()

    def dates(self):
        with self._lock:
            now = self.now()
            if self._loaded is None or (now - self._loaded).total_seconds() > 3600:
                try:
                    dates = tuple(sorted(set(str(d)[:10] for d in self.loader())))
                    if not dates:
                        raise ValueError('empty calendar')
                    for day in dates:
                        datetime.fromisoformat(day)
                    self._dates, self._loaded = dates, now
                except Exception as exc:
                    raise ValueError('交易日历不可用，暂停成交') from exc
            return self._dates

    def today(self):
        return self.now().date().isoformat()

    def next_day(self, day):
        dates = self.dates()
        if day not in dates:
            raise ValueError('信号日不在交易日历中')
        target = next((d for d in dates if d > day), None)
        if target is None:
            raise ValueError('下一交易日未确认')
        return target

    def is_session(self):
        now = self.now()
        if self.today() not in self.dates():
            return False
        current = now.time()
        return time(9, 30) <= current < time(11, 30) or time(13) <= current < time(14, 57)

    def validate_quote(self, quote):
        if not self.is_session():
            raise ValueError('非连续交易时段，等待下一有效交易时段')
        try:
            timestamp = datetime.fromisoformat(str(quote.timestamp))
            lag = (self.now() - timestamp).total_seconds()
            values = [float(quote.price), float(quote.open), float(quote.high), float(quote.low)]
            valid = all(math.isfinite(v) and v > 0 for v in values)
            valid = valid and float(quote.volume) > 0
        except (ValueError, TypeError, AttributeError):
            raise ValueError('行情时间或价格无效') from None
        if getattr(quote, 'stale', True) or timestamp.date().isoformat() != self.today() or not -5 <= lag <= 120:
            raise ValueError('行情已过期，等待当日有效实时价')
        if not valid or quote.low > quote.price or quote.high < quote.price:
            raise ValueError('行情价格无效或停牌')
