"""Snapshot-bound automatic paper trading with one durable execution ledger."""
from datetime import datetime, time as datetime_time
from contextlib import closing
from functools import wraps
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import threading
import uuid
import pandas as pd
from .engine import PaperTradingEngine
from .market_data import MarketDataProvider


def serialized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._operation_lock:
            return method(self, *args, **kwargs)
    return call


class AutoPaperCoordinator:
    """自动模拟盘：基于 vended a-share-skill PaperTradingEngine（SQLite 账本）。

    - 账本 / 撮合 / T+1 / 涨跌停 / 手续费：PaperTradingEngine（MIT）
    - 信号决策：复用 AutoPaperTrader 的信号引擎
    - 元数据（running / signal_mode / last_run / positions_meta）存 auto_paper_meta.json
    """

    SIGNAL_MODES = {}
    EXECUTION_MODES = {'manual': '手动交易', 'auto': '策略自动', 'confirm': '人工确认'}
    CYCLE_SECONDS = 60
    INIT_CASH = 100000
    ACCOUNT_ID = "default"
    MAX_MOVE_GUARD = 0.20   # 主板 ±10% 涨跌停，用 ±20% 挡错价
    MAX_NEW_PER_CYCLE = 3   # 单轮最大新开仓数
    LOSS_PAUSE_PCT = -0.15  # 总资产回撤超过 15% 时暂停新开仓
    MAX_FORWARD_RECORDS = 300  # 远期验证池最大记录数
    L0_BREADTH_MIN = 0.40   # L0 择时门控：全市场宽度（站上MA20占比）低于该值不开新仓
    MAX_FAMILY_POSITIONS = 4  # 单因子族最大仓位数（单因子暴露控制）

    def __init__(self, *, service, state_dir, signal_engine, lock_factory, rs_thresholds, clock, report_provider=None):
        self._operation_lock = threading.RLock()
        self.clock = clock
        self._reports = report_provider
        self._rs_thresholds = rs_thresholds
        root = Path(state_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.meta_file = root / "auto_paper_meta.json"
        self.db_file = root / "auto_paper_state.db"
        self._import_database(root)
        self.engine_lock = lock_factory(str(self.db_file) + ".engine.lock")
        self.state = self._default_meta()
        self._signals = signal_engine()
        self.SIGNAL_MODES = self._signals.SIGNAL_MODES
        self.engine = PaperTradingEngine(str(self.db_file), market_data=MarketDataProvider(service), clock=clock)
        try:
            self.engine.get_account(self.ACCOUNT_ID)
        except ValueError:
            self.engine.create_account(self.ACCOUNT_ID, self.INIT_CASH)
        self._load_meta()
        self._migrate_legacy()
        self._save_meta()

    def _import_database(self, root):
        legacy = Path.cwd() / "auto_paper_state.db"
        if self.db_file.exists() or legacy.resolve() == self.db_file or not legacy.exists():
            return
        # SQLite's backup API copies a coherent committed database, including WAL.
        with closing(sqlite3.connect(legacy.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(self.db_file)) as destination:
                source.backup(destination)
        old_meta = Path.cwd() / "auto_paper_meta.json"
        if old_meta.exists() and not self.meta_file.exists():
            shutil.copy2(old_meta, self.meta_file)

    # ---------- 元数据 ----------

    def _default_meta(self) -> dict:
        return {
            "cash": self.INIT_CASH,
            "running": False,
            "execution_mode": "auto",
            "mode_epoch": 0,
            "signal_mode": "sequoia_oneil",
            "last_run": None,
            "last_error": None,
            "_sig_date": {},
            "_universe_n": 0,
            "positions_meta": {},
            "forward_pool": [],
            "l0_breadth": None,
            "l0_gate": True,
            "family_exposure": {},
            "equity_peak": self.INIT_CASH,
            "execution_phase": "已暂停；等待开启模拟交易",
        }

    def _load_meta(self):
        with self.engine._connect() as conn:
            record = conn.execute("SELECT payload FROM paper_automation_state WHERE account_id=?", (self.ACCOUNT_ID,)).fetchone()
            data = json.loads(record["payload"]) if record else {}
            if not record and self.meta_file.exists():
                data = json.loads(self.meta_file.read_text(encoding="utf-8"))
                data['running'] = False
            if not record:
                peak = conn.execute('SELECT MAX(net_asset) FROM account_snapshots WHERE account_id=?',(self.ACCOUNT_ID,)).fetchone()[0]
                data['equity_peak'] = max(float(data.get('equity_peak') or self.INIT_CASH),float(peak or 0))
            state = self._default_meta()
            state.update(data)
            # Trade-specific metadata is committed atomically with each fill.
            rows = conn.execute("SELECT symbol,payload FROM paper_position_meta WHERE account_id=?", (self.ACCOUNT_ID,)).fetchall()
            for row in rows:
                state["positions_meta"][row["symbol"]] = json.loads(row["payload"])
            self.state = state

    def _save_meta(self):
        payload = json.dumps(self.state, ensure_ascii=False)
        with self.engine._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO paper_automation_state(account_id,payload) VALUES(?,?)", (self.ACCOUNT_ID,payload))

    @staticmethod
    def _position_source(meta: dict) -> str:
        """把持仓元数据归一为界面使用的两类来源标签。"""
        meta = meta if isinstance(meta, dict) else {}
        explicit = str(meta.get("source") or "").strip()
        if explicit in ("决策", "策略", "手动"):
            return explicit
        reason = str(meta.get("buy_reason") or "").strip()
        return "决策" if reason.startswith("决策买入") else "策略"

    # ---------- 存量迁移 ----------

    def _migrate_legacy(self):
        legacy = self.meta_file.parent / "auto_paper_state.json"
        if not legacy.exists():
            legacy = Path.cwd() / "auto_paper_state.json"
        with self.engine._connect() as conn:
            if conn.execute("SELECT 1 FROM system_settings WHERE key='legacy_import_done'").fetchone():
                return
        if not legacy.exists():
            with self.engine._connect() as conn:
                conn.execute("INSERT OR IGNORE INTO system_settings(key,value) VALUES('legacy_import_done','no_legacy')")
            return
        with self.engine._connect() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM position_lots").fetchone()["c"]
            if n or conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]:
                conn.execute("INSERT OR IGNORE INTO system_settings(key,value) VALUES('legacy_import_done','existing_ledger')")
                return
            try:
                data = json.loads(legacy.read_text(encoding="utf-8"))
            except Exception:
                return
        self.state["signal_mode"] = data.get("signal_mode", self.state["signal_mode"])
        self.state["running"] = False
        self.state["last_run"] = data.get("last_run")
        self.state["_universe_n"] = data.get("_universe_n", 0)
        cash = float(data.get("cash", self.INIT_CASH))
        import uuid as _uuid

        with self.engine._connect() as conn:
            for sym, pos in (data.get("positions") or {}).items():
                d = pos.get("buy_date") or str(pos.get("buy_time", ""))[:10] or "2000-01-01"
                conn.execute(
                    "INSERT INTO position_lots(lot_id, account_id, symbol, acquired_date, qty, remaining_qty, cost_price, created_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (_uuid.uuid4().hex[:16], self.ACCOUNT_ID, sym, d, int(pos["qty"]), int(pos["qty"]), float(pos["buy_price"]), pos.get("buy_time") or self.clock.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
                self.state["positions_meta"][sym] = {
                    "buy_price": float(pos["buy_price"]),
                    "buy_date": d,
                    "buy_time": pos.get("buy_time", ""),
                    "buy_reason": pos.get("buy_reason", ""),
                    "source": self._position_source(pos),
                    "target_price": pos.get("target_price"),
                    "stop_price": pos.get("stop_price"),
                }
            for i, t in enumerate((data.get("trades") or [])[:1000]):
                side = str(t.get("side", "buy")).lower()
                price = float(t.get("price") or 0)
                qty = int(t.get("qty") or 0)
                amount = round(price * qty, 2)
                tid = _uuid.uuid4().hex[:16]
                conn.execute(
                    "INSERT INTO trades(trade_id, order_id, account_id, symbol, side, price, qty, amount, commission, tax, created_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (tid, tid, self.ACCOUNT_ID, t.get("symbol", ""), side, price, qty, amount,
                     round(float(t.get("cost") or amount * 0.0015), 2), 0.0, t.get("time", self.clock.now().strftime("%Y-%m-%d %H:%M:%S"))),
                )
            conn.execute("UPDATE accounts SET initial_cash = ?, cash = ?, updated_at = ? WHERE account_id = ?",
                         (self.INIT_CASH, cash, self.clock.now().strftime("%Y-%m-%d %H:%M:%S"), self.ACCOUNT_ID))
            conn.execute("INSERT INTO system_settings(key,value) VALUES('legacy_import_done','1')")
            conn.execute("INSERT OR REPLACE INTO paper_automation_state(account_id,payload) VALUES(?,?)", (self.ACCOUNT_ID,json.dumps(self.state,ensure_ascii=False)))
        self._save_meta()

    # ---------- 状态 ----------

    def _trade_note(self, order_id) -> str:
        if not order_id:
            return ""
        try:
            with self.engine._connect() as conn:
                row = conn.execute("SELECT note FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            return row["note"] if row else ""
        except Exception:
            return ""

    def _snapshots_hist(self) -> list:
        try:
            with self.engine._connect() as conn:
                rows = conn.execute(
                    "SELECT snapshot_time AS t, net_asset AS total FROM account_snapshots "
                    "WHERE account_id = ? ORDER BY snapshot_time ASC",
                    (self.ACCOUNT_ID,),
                ).fetchall()
            return [{"time": r["t"], "total": r["total"]} for r in rows][-120:]
        except Exception:
            return []

    @serialized
    def status(self, service=None) -> dict:
        self._load_meta()
        acc = self.engine.get_account(self.ACCOUNT_ID)
        pos_meta = self.state.get("positions_meta", {})
        pos_list = []
        for p in (acc.get("positions") or []):
            m = pos_meta.get(p["symbol"], {})
            buy_price = float(m.get("buy_price") or p.get("avg_cost") or 0)
            last = float(p.get("last_price") or buy_price)
            target = float(m.get("target_price") or (buy_price * (1 + self._signals.TAKE_PROFIT) if buy_price else 0))
            stop = float(m.get("stop_price") or (buy_price * (1 - self._signals.STOP_LOSS) if buy_price else 0))
            pnl = round((last - buy_price) * int(p["qty"]), 2) if buy_price else 0.0
            pnl_pct = round((last / buy_price - 1) * 100, 2) if buy_price else 0.0
            pos_list.append({
                "symbol": p["symbol"], "qty": int(p["qty"]),
                "sellable_qty": int(p.get("sellable_qty") or 0),
                "buy_price": round(buy_price, 2), "avg_cost": round(buy_price, 4),
                "target_price": round(target, 2), "stop_price": round(stop, 2),
                "last_price": round(last, 2), "value": round(last * int(p["qty"]), 2),
                "pnl": pnl, "pnl_pct": pnl_pct,
                "target_pct": self._signals.TAKE_PROFIT * 100,
                "stop_pct": -self._signals.STOP_LOSS * 100,
                "buy_time": m.get("buy_time", ""), "buy_reason": m.get("buy_reason", ""),
                "source": self._position_source(m),
            })
        pos_list.sort(key=lambda x: -x["value"])

        trade_list = []
        for t in (self.engine.list_trades(self.ACCOUNT_ID) or []):
            trade_list.append({
                "symbol": t["symbol"], "side": str(t["side"]).upper(),
                "price": t["price"], "qty": t["qty"],
                "time": t["created_at"], "reason": self._trade_note(t.get("order_id")),
                "commission": t.get("commission"), "tax": t.get("tax"),
                "pnl_pct": None, "pnl": None,
            })

        total = acc.get("net_asset") or 0.0
        pnl = total - self.INIT_CASH
        pnl_pct = round((total / self.INIT_CASH - 1) * 100, 2) if self.INIT_CASH else 0.0
        st = self.state
        with self.engine._connect() as conn:
            pending = conn.execute("SELECT * FROM paper_intents WHERE account_id=? AND status IN ('pending','awaiting_confirmation') ORDER BY created_at,intent_id LIMIT 500", (self.ACCOUNT_ID,)).fetchall()
            order_history = conn.execute("SELECT * FROM paper_intents WHERE account_id=? AND status NOT IN ('pending','awaiting_confirmation') ORDER BY created_at DESC,rowid DESC LIMIT 50", (self.ACCOUNT_ID,)).fetchall()
        peak = max(float(st.get('equity_peak') or self.INIT_CASH),float(total))
        drawdown = (float(total)/peak-1)*100 if peak else 0.0
        summary = getattr(service, "universe_summary", None) if service is not None else None
        if summary is None:
            summary = {
                "total": st.get("_universe_n", 0), "computable": 0, "tradable": 0,
                "candidate": 0, "excluded_by_reason": {}, "as_of": None, "source": "unknown",
            }
        pending_count = len(pending)
        with self.engine._connect() as conn:
            pending_count = conn.execute("SELECT COUNT(*) FROM paper_intents WHERE account_id=? AND status='pending'",(self.ACCOUNT_ID,)).fetchone()[0]
            approval_count = conn.execute("SELECT COUNT(*) FROM paper_intents WHERE account_id=? AND status='awaiting_confirmation'",(self.ACCOUNT_ID,)).fetchone()[0]
        def describe(row):
            payload = json.loads(row['payload'])
            return {k: row[k] for k in ('intent_id','symbol','side','signal_date','execution_date','status','order_id')} | {
                'reason': payload.get('reason',''), 'source': payload.get('source','策略'),
                'last_error': payload.get('last_error'),
            }
        return {
            "cash": round(acc.get("cash") or 0.0, 2),
            "market_value": round(acc.get("market_value") or 0.0, 2),
            "total": round(total, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": pnl_pct,
            "initial": self.INIT_CASH,
            "positions": pos_list,
            "position_count": len(pos_list),
            "max_positions": self._signals.MAX_POSITIONS,
            "trades": trade_list[:200],
            "equity_hist": self._snapshots_hist(),
            "running": bool(st.get("running", True)),
            "execution_mode": st.get('execution_mode','auto'),
            "execution_modes": [{'mode': k, 'label': v} for k,v in self.EXECUTION_MODES.items()],
            "manual_session_open": st.get('execution_mode')=='manual' and self._manual_session_open(),
            "last_run": st.get("last_run"),
            "last_error": st.get("last_error"),
            "cycle_seconds": self.CYCLE_SECONDS,
            "signal_mode": st.get("signal_mode", "sequoia_oneil"),
            "signal_mode_label": self.SIGNAL_MODES.get(st.get("signal_mode", "sequoia_oneil"), "sequoia_oneil"),
            "signal_modes": [{"mode": k, "label": v} for k, v in self.SIGNAL_MODES.items()],
            "engine_owner": getattr(self.engine_lock, '_fh', None) is not None,
            "execution_phase": st.get('execution_phase'),
            "signal_date": st.get('signal_date'), "execution_date": st.get('execution_date'),
            "pending_orders": [describe(row) for row in pending], "pending_count": pending_count,
            "approval_count": approval_count, "order_history": [describe(row) for row in order_history],
            "research_observations": self._research_status(),
            "max_drawdown_pct": round(drawdown,2),
            "fee_model": {'sell_stamp_tax':0.0005,'commission_rate':0.0003,'minimum_commission':5.0},
            "universe_size": st.get("_universe_n", 0),
            "universe_summary": summary,
            "rules": {
                "pos_ratio": self._signals.POS_RATIO, "take_profit": self._signals.TAKE_PROFIT,
                "stop_loss": self._signals.STOP_LOSS, "cost": self._signals.COST,
            },
            "forward_pool": (st.get("forward_pool") or [])[-50:],
            "risk": {
                "drawdown_pct": round(drawdown,2),
                "equity_peak": round(peak,2),
                "max_positions": self._signals.MAX_POSITIONS,
                "max_new_per_cycle": self.MAX_NEW_PER_CYCLE,
                "loss_pause_pct": self.LOSS_PAUSE_PCT * 100,
                "current_pnl_pct": round(((acc.get("net_asset") or 0) / self.INIT_CASH - 1) * 100, 2) if self.INIT_CASH else 0.0,
                "l0_breadth": st.get("l0_breadth"),
                "l0_gate": st.get("l0_gate", True),
                "l0_breadth_min": self.L0_BREADTH_MIN,
                "max_family_positions": self.MAX_FAMILY_POSITIONS,
                "family_exposure": st.get("family_exposure", {}),
            },
        }

    @serialized
    def toggle(self, service=None) -> dict:
        if not self.engine_lock.acquired():
            raise ValueError('另一实例正在管理模拟盘，本实例只可查看')
        self._load_meta()
        if self.state['execution_mode'] == 'manual':
            raise ValueError('手动模式无需启动策略，请直接提交买卖订单')
        self.state["running"] = not self.state.get("running", True)
        self.state['execution_phase'] = ('策略监测已启动；等待下一轮检查' if self.state['running']
                                         else '策略已暂停；已确认订单等待恢复，名单核对继续观察')
        self._save_meta()
        return self.status(service)

    @serialized
    def set_mode(self, mode: str, service=None) -> dict:
        if mode not in self.SIGNAL_MODES:
            raise ValueError(f"未知信号源: {mode}，可选: {', '.join(self.SIGNAL_MODES)}")
        if not self.engine_lock.acquired():
            raise ValueError('另一实例正在管理模拟盘，本实例只可查看')
        self._load_meta()
        self.state["signal_mode"] = mode
        self.state["_sig_date"] = {}
        with self.engine._connect() as conn:
            conn.execute("UPDATE paper_intents SET status='cancelled' WHERE account_id=? AND status IN ('pending','awaiting_confirmation') AND mode<>'decision'", (self.ACCOUNT_ID,))
        self.state['mode_epoch'] += 1
        self.state.pop('signal_marker',None)
        self.state.update(signal_date=None,execution_date=None)
        self._save_meta()
        return self.status(service)

    @serialized
    def reset(self, service=None) -> dict:
        if not self.engine_lock.acquired():
            raise ValueError('另一实例正在管理模拟盘，本实例只可查看')
        self._load_meta()
        mode = self.state.get("signal_mode", "sequoia_oneil")
        execution_mode = self.state.get('execution_mode','auto')
        epoch = self.state.get('mode_epoch',0)+1
        self.engine.reset_account(self.ACCOUNT_ID, self.INIT_CASH)
        self.state = self._default_meta()
        self.state["signal_mode"] = mode
        self.state['execution_mode'] = execution_mode
        self.state['mode_epoch'] = epoch
        self.state["running"] = False
        self._save_meta()
        return self.status(service)

    def _manual_session_open(self):
        try:
            return self.clock.is_session()
        except ValueError:
            return False

    def _require_owner(self):
        if not self.engine_lock.acquired():
            raise ValueError('另一实例正在管理模拟盘，本实例只可查看')

    @serialized
    def set_execution_mode(self, mode, service=None):
        self._require_owner()
        if mode not in self.EXECUTION_MODES:
            raise ValueError('未知交易模式')
        self._load_meta()
        if self.state['execution_mode'] != mode:
            with self.engine._connect() as conn:
                conn.execute("UPDATE paper_intents SET status='cancelled' WHERE account_id=? AND status IN ('pending','awaiting_confirmation')",(self.ACCOUNT_ID,))
            self.state.update(execution_mode=mode,running=False,last_error=None,
                              mode_epoch=self.state['mode_epoch']+1,
                              execution_phase='已切换交易模式；策略已暂停')
            self.state.pop('signal_marker',None)
            self.state.update(signal_date=None,execution_date=None)
            self._save_meta()
        return self.status(service)

    @serialized
    def manual_trade(self, service, *, symbol, side, qty, request_id):
        self._require_owner()
        self._load_meta()
        if not isinstance(symbol,str) or len(symbol)!=6 or not symbol.isascii() or not symbol.isdigit():
            raise ValueError('请输入6位股票代码')
        if side not in ('buy','sell'):
            raise ValueError('请选择买入或卖出')
        if type(qty) is not int or qty <= 0 or qty > 10000000:
            raise ValueError('数量必须是有效的正整数')
        try:
            request_id = str(uuid.UUID(request_id))
        except (ValueError,TypeError,AttributeError):
            raise ValueError('订单标识无效，请重新提交') from None
        key = hashlib.sha256(f'{self.ACCOUNT_ID}|manual|{request_id}'.encode()).hexdigest()
        with self.engine._connect() as conn:
            old = conn.execute('SELECT o.* FROM execution_keys e JOIN orders o ON e.order_id=o.order_id WHERE e.intent_key=?',(key,)).fetchone()
        if old:
            if (old['symbol'],old['side'],old['qty']) != (symbol,side,qty):
                raise ValueError('同一订单标识不能用于不同的买卖请求')
            return {**self.status(service),'submitted_order':dict(old)}
        if self.state['execution_mode'] != 'manual':
            raise ValueError('请先切换到手动交易模式')
        if not self.clock.is_session():
            raise ValueError('当前为休市或非连续交易时段，手动订单未提交')
        if side == 'buy':
            if qty % 100:
                raise ValueError('买入数量必须为100股的整数倍')
            ok,message = self._buy_gate(service,symbol,'手动买入',allow_add=True,strategy_checks=False)
            if not ok:
                raise ValueError(message)
        note = '手动买入' if side == 'buy' else '手动卖出'
        metadata = {'source':'手动','buy_reason':note} if side=='buy' else None
        try:
            order = self.engine.trade_at_quote(self.ACCOUNT_ID,symbol,side,qty,note,intent_key=key,metadata=metadata)
        except ValueError as exc:
            message = str(exc)
            if 'insufficient available cash' in message:
                message = '可用资金不足，请减少买入数量'
            elif 'sellable' in message:
                message = '可卖数量不足；当日买入的股票须下一交易日才能卖出（T+1）'
            elif 'sell qty' in message:
                message = '卖出数量须为100股的整数倍，或一次卖出全部零股'
            raise ValueError(message) from None
        self._load_meta()
        self.state['last_error'] = None
        self.state['execution_phase'] = '手动订单已成交；策略保持暂停'
        self._risk_gate(self.engine.get_account(self.ACCOUNT_ID))
        self._save_meta()
        self.engine.snapshot_accounts()
        return {**self.status(service),'submitted_order':order}

    @serialized
    def review_intent(self, service, intent_id, approve):
        self._require_owner()
        self._load_meta()
        if self.state['execution_mode'] != 'confirm':
            raise ValueError('请先切换到人工确认模式')
        with self.engine._connect() as conn:
            intent = conn.execute('SELECT * FROM paper_intents WHERE intent_id=? AND account_id=?',(intent_id,self.ACCOUNT_ID)).fetchone()
            if not intent:
                raise ValueError('建议不存在')
            if intent['status'] != 'awaiting_confirmation':
                raise ValueError('该建议已处理或失效，请刷新列表')
            if intent['execution_date'] < self.clock.today() or (intent['execution_date']==self.clock.today() and self.clock.now().time()>=datetime_time(14,57)):
                conn.execute("UPDATE paper_intents SET status='expired' WHERE intent_id=?",(intent_id,))
                expired = True
            else:
                conn.execute('UPDATE paper_intents SET status=? WHERE intent_id=?',('pending' if approve else 'rejected',intent_id))
                expired = False
        if expired:
            raise ValueError('目标交易时段已结束，建议已失效')
        if approve and self.state['running']:
            self._execute_pending(service)
        self._save_meta()
        return self.status(service)

    @serialized
    def cancel_intent(self, service, intent_id):
        self._require_owner()
        with self.engine._connect() as conn:
            changed = conn.execute("UPDATE paper_intents SET status='cancelled' WHERE intent_id=? AND account_id=? AND status IN ('pending','awaiting_confirmation')",(intent_id,self.ACCOUNT_ID)).rowcount
        if not changed:
            raise ValueError('订单已处理或不存在，请刷新列表')
        return self.status(service)

    # ---------- 信号 ----------

    def _sig(self, df, rs_pct=50.0, breadth=1.0):
        self._signals.state["signal_mode"] = self.state.get("signal_mode", "sequoia_oneil")
        return self._signals._signal_for(df, rs_pct=rs_pct, breadth=breadth)

    # ---------- 风控门禁 + 远期验证 ----------

    def _risk_gate(self, acc) -> tuple:
        """硬性风控：总资产回撤超阈值 → 暂停新开仓。返回 (是否通过, 原因)。"""
        total = float(acc.get("net_asset") or 0)
        peak = max(float(self.state.get("equity_peak") or self.INIT_CASH), total)
        self.state["equity_peak"] = peak
        pnl_pct = (total / peak - 1) * 100 if peak else 0.0
        if pnl_pct <= self.LOSS_PAUSE_PCT * 100:
            return False, f"总资产回撤 {pnl_pct:.1f}%，超过暂停新开仓阈值"
        return True, ""

    @staticmethod
    def _factor_family(reason: str) -> str:
        """按买入理由把持仓归入信号族（用于单因子暴露控制）。"""
        r = reason or ""
        if any(k in r for k in ("海龟", "突破", "新高", "攻关", "枢轴")):
            return "breakout"      # 突破/新高
        if any(k in r for k in ("MA5", "MA10", "均线", "金叉", "多头", "趋势")):
            return "trend"         # 均线趋势
        if any(k in r for k in ("RSI", "超卖", "反转", "回撤", "反弹", "低波", "lowvol")):
            return "reversal"      # 反转/低波
        if any(k in r for k in ("涨停", "洗盘", "连板")):
            return "limitup"       # 涨停/情绪
        if any(k in r for k in ("量", "缩量", "放量", "OBV")):
            return "volume"        # 量价
        return "other"

    def _family_counts(self) -> dict:
        from collections import Counter
        cnt = Counter()
        held = {p["symbol"] for p in self.engine.get_positions(self.ACCOUNT_ID)}
        for sym in held:
            meta = self.state["positions_meta"].get(sym, {})
            cnt[self._factor_family(meta.get("buy_reason"))] += 1
        return dict(cnt)

    def _record_forward(self, sym, meta, entry, price, cur_date):
        """把一笔已平仓交易写入远期验证池（五池：V1/5/20/60 由 hold_days 归纳）。"""
        buy_date = meta.get("buy_date") or str(meta.get("buy_time", ""))[:10]
        hold_days = None
        if buy_date and cur_date:
            try:
                hold_days = max(0, (pd.Timestamp(cur_date) - pd.Timestamp(buy_date)).days)
            except Exception:
                hold_days = None
        rec = {
            "symbol": sym,
            "entry_date": buy_date,
            "entry_price": round(float(entry), 2),
            "exit_date": cur_date,
            "exit_price": round(float(price), 2),
            "pnl_pct": round((float(price) / float(entry) - 1) * 100, 2) if entry else 0.0,
            "hold_days": hold_days,
            "horizons": [h for h in (1, 5, 20, 60) if hold_days is not None and hold_days >= h],
        }
        pool = self.state.setdefault("forward_pool", [])
        pool.append(rec)
        self.state["forward_pool"] = pool[-self.MAX_FORWARD_RECORDS:]

    def _context(self, service):
        summary = service.universe_summary
        signal_date = str(summary.get('as_of') or '')
        pipeline = getattr(service, 'active_pipeline', None)
        generation = str(pipeline.manifest['generation']) if pipeline else signal_date
        if summary.get('source') not in {'qtrade_mirror', 'external_sqlite', 'test'}:
            raise ValueError('需要已核验收盘快照，暂停生成交易信号')
        target = self.clock.next_day(signal_date)
        now = self.clock.now()
        if signal_date > self.clock.today() or (signal_date == self.clock.today() and now.time() < datetime_time(15, 30)):
            raise ValueError('收盘数据尚未完成，等待15:30后的已核验快照')
        return signal_date, target, generation

    def _queue(self, symbol, side, payload, context, source='策略'):
        signal_date, target, generation = context
        mode = self.state['signal_mode'] if source == '策略' else 'decision'
        identity = '|'.join((self.ACCOUNT_ID, mode, generation, symbol, side, signal_date))
        if self.state['mode_epoch']:
            identity += f"|{self.state['execution_mode']}|{self.state['mode_epoch']}"
        key = hashlib.sha256(identity.encode()).hexdigest()
        payload = {**payload, 'source': source}
        with self.engine._connect() as conn:
            status = 'awaiting_confirmation' if source=='策略' and self.state['execution_mode']=='confirm' else 'pending'
            conn.execute('INSERT OR IGNORE INTO paper_intents(intent_id,account_id,mode,symbol,side,signal_date,execution_date,generation,payload,created_at,status) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (key,self.ACCOUNT_ID,mode,symbol,side,signal_date,target,generation,json.dumps(payload,ensure_ascii=False),self.engine._now_ts(),status))
        return key

    def _set_intent_status(self, key, status):
        with self.engine._connect() as conn:
            conn.execute('UPDATE paper_intents SET status=? WHERE intent_id=? AND status=\'pending\'', (status,key))

    def _buy_gate(self, service, symbol, reason, *, allow_add=False, strategy_checks=True):
        if not service.is_tradable(symbol):
            return False, '标的暂不可交易'
        positions = self.engine.get_positions(self.ACCOUNT_ID)
        held = any(p['symbol'] == symbol for p in positions)
        if held and not allow_add:
            return False, '已持有该标的'
        if not held and len(positions) >= self._signals.MAX_POSITIONS:
            return False, '持仓已达上限'
        ok, message = self._risk_gate(self.engine.get_account(self.ACCOUNT_ID))
        if not ok:
            return ok, message
        if strategy_checks and not self.state.get('l0_gate', True):
            return False, '市场宽度未达到开仓条件'
        family = self._factor_family(reason)
        if strategy_checks and self._family_counts().get(family, 0) >= self.MAX_FAMILY_POSITIONS:
            return False, '同一因子族持仓已达上限'
        return True, ''

    def _expire_intents(self):
        today = self.clock.today()
        closed = self.clock.now().time() >= datetime_time(14,57)
        with self.engine._connect() as conn:
            conn.execute("UPDATE paper_intents SET status='expired' WHERE account_id=? AND status IN ('pending','awaiting_confirmation') AND (execution_date<? OR (execution_date=? AND ?))",(self.ACCOUNT_ID,today,today,closed))

    def _execute_pending(self, service):
        today = self.clock.today()
        self._expire_intents()
        with self.engine._connect() as conn:
            pending = conn.execute("SELECT * FROM paper_intents WHERE account_id=? AND status='pending' AND execution_date=? ORDER BY CASE side WHEN 'sell' THEN 0 ELSE 1 END,created_at,intent_id", (self.ACCOUNT_ID,today)).fetchall()
        if not self.clock.is_session():
            self.state['execution_phase'] = '等待有效交易时段；仅生成信号和核对结果'
            return
        new_buys = 0
        for intent in pending:
            payload = json.loads(intent['payload'])
            symbol, side = intent['symbol'], intent['side']
            if intent['mode'] not in {'decision', self.state['signal_mode']}:
                self._set_intent_status(intent['intent_id'], 'cancelled')
                continue
            reason = str(payload.get('reason') or '')
            try:
                if side == 'buy':
                    if new_buys >= self.MAX_NEW_PER_CYCLE:
                        break
                    ok, message = self._buy_gate(service, symbol, reason)
                    if not ok:
                        self.state['last_error'] = message
                        # A held stock never needs a second opening order.
                        if message == '已持有该标的':
                            self._set_intent_status(intent['intent_id'], 'skipped')
                        if message in {'持仓已达上限'} or '回撤' in message or '市场宽度' in message:
                            break
                        continue
                    quote = self.engine.market_data.get_quote(symbol)
                    self.clock.validate_quote(quote)
                    account = self.engine.get_account(self.ACCOUNT_ID)
                    cash = float(account['cash']) - float(account['frozen_cash'])
                    budget = min(float(account['net_asset']) * self._signals.POS_RATIO, cash)
                    # Use the execution price, never the signal's historical qfq price.
                    quantity = int(max(0, budget - 6) / (quote.price * 1.001)) // 100 * 100
                    if quantity < 100:
                        self.state['last_error'] = '现金不足一手'
                        continue
                    metadata = dict(buy_reason=reason,source=payload['source'],signal_date=intent['signal_date'],
                                    take_pct=payload.get('take_pct',self._signals.TAKE_PROFIT),stop_pct=payload.get('stop_pct',self._signals.STOP_LOSS),
                                    snapshot_generation=intent['generation'],
                                    target_price=round(quote.price*(1+payload.get('take_pct',self._signals.TAKE_PROFIT)),2),
                                    stop_price=round(quote.price*(1-payload.get('stop_pct',self._signals.STOP_LOSS)),2))
                    self.engine.trade_at_quote(self.ACCOUNT_ID,symbol,side,quantity,reason,intent_key=intent['intent_id'],metadata=metadata)
                    self._load_meta()
                    new_buys += 1
                else:
                    position = next((p for p in self.engine.get_positions(self.ACCOUNT_ID) if p['symbol']==symbol),None)
                    if position is None:
                        self._set_intent_status(intent['intent_id'], 'skipped')
                        continue
                    quantity = int(position.get('sellable_qty') or 0)
                    if quantity <= 0:
                        continue
                    order = self.engine.trade_at_quote(self.ACCOUNT_ID,symbol,side,quantity,reason,intent_key=intent['intent_id'])
                    meta = self.state['positions_meta'].get(symbol,{})
                    self._record_forward(symbol,meta,meta.get('buy_price') or position['avg_cost'],order['avg_fill_price'],today)
                    self._save_meta()
            except ValueError as exc:
                self.state['last_error'] = str(exc)
                payload['last_error'] = str(exc)
                with self.engine._connect() as conn:
                    conn.execute("UPDATE paper_intents SET payload=? WHERE intent_id=? AND status='pending'",(json.dumps(payload,ensure_ascii=False),intent['intent_id']))
        self.state['execution_phase'] = '交易时段：按当前有效实时行情模拟成交'

    def _build_signals(self, service, context):
        signal_date, target, generation = context
        mode = self.state['signal_mode']
        marker = generation + '|' + mode
        if self.state['mode_epoch']:
            marker += f"|{self.state['execution_mode']}|{self.state['mode_epoch']}"
        if self.state.get('signal_marker') == marker:
            return
        if target < self.clock.today():
            self.state['last_error'] = '当前快照对应交易日已结束，请先更新数据'
            return
        self._signals.state['signal_mode'] = mode
        symbols = service.mainboard_symbols()
        self.state['_universe_n'] = len(symbols)
        rs_map, breadth = {}, 1.0
        if mode in self._rs_thresholds:
            rs_map, breadth, candidates = self._signals._mainboard_scan(service,symbols,rs_min=self._rs_thresholds[mode])
        else:
            candidates = symbols
            values = []
            for symbol in symbols:
                history = service.load_history(symbol)
                if history is not None and len(history)>=25 and str(history.index[-1])[:10]==signal_date:
                    close = history['close'].astype(float)
                    values.append(close.iloc[-1]>close.tail(20).mean())
            breadth = sum(values)/len(values) if values else 0.0
        self.state['l0_breadth'] = round(breadth,4)
        self.state['l0_gate'] = breadth >= self.L0_BREADTH_MIN
        positions = {p['symbol']:p for p in self.engine.get_positions(self.ACCOUNT_ID)}
        for symbol in sorted(set(candidates) | set(positions)):
            history = service.load_history(symbol)
            if history is None or history.empty or str(history.index[-1])[:10] != signal_date:
                continue
            signal = self._sig(history,rs_pct=rs_map.get(symbol,50.0),breadth=breadth)
            if not signal or signal.get('date') != signal_date:
                continue
            side = signal.get('action')
            if symbol in positions:
                meta = self.state['positions_meta'].get(symbol,{})
                price = float(signal.get('price') or 0)
                if meta.get('target_price') and price >= meta['target_price']:
                    side, signal = 'sell', {**signal,'reason':'收盘触发止盈，下一交易时段执行'}
                elif meta.get('stop_price') and price <= meta['stop_price']:
                    side, signal = 'sell', {**signal,'reason':'收盘触发止损，下一交易时段执行'}
                if side != 'sell':
                    continue
            elif side != 'buy':
                continue
            self._queue(symbol,side,signal,context)
        service.set_candidate_symbols(candidates)
        self.state['signal_marker'] = marker
        self.state['signal_date'],self.state['execution_date'] = signal_date,target

    @serialized
    def buy_from_decision(self, service, rec):
        self._load_meta()
        if self.state['execution_mode']=='manual' or not self.state['running'] or not self.engine_lock.acquired():
            return self.status(service)
        try:
            context = self._context(service)
            if context[1] < self.clock.today():
                raise ValueError('决策对应目标日已过期，请先更新数据')
            symbol = str(rec.get('code') or '').strip()
            if not symbol.isdigit() or len(symbol)!=6:
                raise ValueError('股票代码无效')
            self._build_signals(service,context)
            ok, reason = self._buy_gate(service,symbol,str(rec.get('reason') or ''))
            if not ok:
                raise ValueError(reason)
            decision_key = self._queue(symbol,'buy',{'reason':str(rec.get('reason') or '')+'；决策买入：'+str(rec.get('name') or '审批')},context,'决策')
            self.state['last_decision_intent'] = decision_key
            self._save_meta()
            self._execute_pending(service)
        except ValueError as exc:
            self.state['last_error'] = str(exc)
        self._save_meta()
        result = self.status(service)
        result['decision_queued'] = any(p['intent_id']==self.state.get('last_decision_intent') for p in result['pending_orders'])
        return result

    @serialized
    def cycle(self, service, *, force=False):
        self._load_meta()
        if service is None or not self.engine_lock.acquired():
            return self.status(service)
        try:
            # Research observation does not require enabling automatic trading.
            self._observe_research(service)
            self._expire_intents()
            if force and self.state['execution_mode']=='manual':
                raise ValueError('手动模式不运行策略，请直接提交买卖订单')
            if (self.state['running'] or force) and self.state['execution_mode']!='manual':
                self.state['last_error'] = None
                self.state['execution_phase'] = '等待已核验快照和下一有效交易时段'
                context = self._context(service)
                self._build_signals(service,context)
                self._save_meta()
                self._execute_pending(service)
                self.engine.snapshot_accounts()
                self._risk_gate(self.engine.get_account(self.ACCOUNT_ID))
                self.state['last_run'] = self.engine._now_ts()
            if not self.state['running']:
                self.state['execution_phase'] = ('手动交易：仅在交易时段提交订单；策略已暂停' if self.state['execution_mode']=='manual'
                                                 else '策略已暂停；已确认订单等待恢复，名单核对继续观察')
            elif self.state['execution_mode']=='confirm':
                self.state['execution_phase'] += '；新建议等待人工确认'
            self._save_meta()
        except ValueError as exc:
            self.state['last_error'] = str(exc)
            self._save_meta()
        return self.status(service)

    def _observe_research(self, service):
        status = self._reports() if self._reports else {}
        report = status.get('report') if status.get('state')=='complete' else None
        if report:
            predictions = report.get('predictions',[])
            signal, target = str(report.get('as_of') or ''), str(report.get('target_date') or '')
            try:
                generated = datetime.fromisoformat(report['generated_at'])
                deadline = datetime.fromisoformat(target+'T09:30:00')
                prospective = datetime.fromisoformat(signal+'T15:30:00') <= generated <= self.clock.now() < deadline
                identity = report['model_id']+'|'+signal+'|'+target
                freeze_id = hashlib.sha256(identity.encode()).hexdigest()
                if len(predictions) != 100 or self.clock.next_day(signal)!=target:
                    raise ValueError('名单不完整或日期不匹配')
                payload = json.dumps(report,ensure_ascii=False,allow_nan=False)
                with self.engine._connect() as conn:
                    conn.execute('INSERT OR IGNORE INTO paper_research_freezes(freeze_id,signal_date,target_date,generated_at,frozen_at,prospective,payload) VALUES(?,?,?,?,?,?,?)',
                                 (freeze_id,signal,target,report['generated_at'],self.engine._now_ts(),int(prospective),payload))
            except (KeyError,TypeError,ValueError):
                pass
        with self.engine._connect() as conn:
            pending = conn.execute('SELECT * FROM paper_research_freezes WHERE outcome IS NULL ORDER BY target_date LIMIT 100').fetchall()
        summary = service.universe_summary
        as_of = str(summary.get('as_of') or '')
        for freeze in pending:
            target = freeze['target_date']
            if as_of < target or target > self.clock.today() or (target==self.clock.today() and self.clock.now().time()<datetime_time(15,30)):
                continue
            predictions = json.loads(freeze['payload'])['predictions']
            results = []
            for prediction in predictions:
                symbol = prediction['symbol']
                frame = service.load_history(symbol)
                result = {'rank':prediction['rank'],'symbol':symbol,'probability':prediction['research_probability'],'status':'missing'}
                if frame is not None:
                    days = frame.index.strftime('%Y-%m-%d')
                    earlier = frame.loc[days==freeze['signal_date'],'close']
                    later = frame.loc[days==target,'close']
                    if len(earlier)==1 and len(later)==1:
                        previous, actual = float(earlier.iloc[0]),float(later.iloc[0])
                        if all(math.isfinite(x) and x>0 for x in (previous,actual)):
                            result.update(status='complete',previous_close=previous,actual_close=actual,
                                          change_pct=(actual/previous-1)*100,
                                          outcome='up' if actual>previous else 'down' if actual<previous else 'flat')
                results.append(result)
            # Missing labels remain pending and can be completed by later snapshots.
            if any(r['status']=='missing' for r in results):
                continue
            def metrics(rows):
                return {k:sum(r['outcome']==k for r in rows) for k in ('up','down','flat')}
            outcome={'top10':metrics(results[:10]),'top100':metrics(results),'rows':results,'verified_at':self.engine._now_ts()}
            with self.engine._connect() as conn:
                conn.execute('UPDATE paper_research_freezes SET outcome=? WHERE freeze_id=? AND outcome IS NULL', (json.dumps(outcome,ensure_ascii=False),freeze['freeze_id']))

    def _research_status(self):
        with self.engine._connect() as conn:
            rows = conn.execute('SELECT freeze_id,signal_date,target_date,generated_at,frozen_at,prospective,outcome FROM paper_research_freezes ORDER BY target_date DESC LIMIT 30').fetchall()
        return [{**dict(row),'prospective':bool(row['prospective']), 'outcome':json.loads(row['outcome']) if row['outcome'] else None} for row in rows]
