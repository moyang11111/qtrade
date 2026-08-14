#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
QTrade Desktop - tkinter native desktop terminal v2
Zero extra deps. matplotlib NavigationToolbar2Tk for zoom/pan.
"""

import sys, json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from server import DataService, DsaSignalReader, AiPaperTrader, TencentLiveSource, find_data_dir

import tkinter as tk
from tkinter import ttk, messagebox
import matplotlib; matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import mplfinance as mpf
import pandas as pd
import numpy as np

import matplotlib.pyplot as plt
for f in ["Microsoft YaHei", "SimHei", "PingFang SC"]:
    try:
        matplotlib.font_manager.findfont(f, fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = [f, "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        break
    except Exception:
        continue

# ---- Design Tokens (UI/UX Pro Max) ----
C = {
    "bg0": "#0d1117", "bg1": "#161b22", "bg2": "#21262d", "bg_card": "#1c2128",
    "border": "#30363d", "text": "#e6edf3", "text2": "#8b949e", "text3": "#6e7681",
    "accent": "#e94560", "accent_h": "#ff6b81", "up": "#f85149", "down": "#2ea043",
    "yellow": "#d2991d", "blue": "#58a6ff", "purple": "#bc8cff", "green_btn": "#238636",
}
F = {
    "ui": ("Segoe UI", 10), "bold": ("Segoe UI", 10, "bold"),
    "mono": ("Cascadia Code", 10), "sm": ("Segoe UI", 9), "xs": ("Segoe UI", 8),
    "title": ("Segoe UI", 13, "bold"), "xxl": ("Segoe UI", 18, "bold"),
}

SERVICE = DSA_READER = AI_PAPER = None


# ---- Dark Theme ----
def apply_dark_theme():
    s = ttk.Style()
    try: s.theme_use("clam")
    except Exception: pass
    bg, fg, sel = C["bg0"], C["text"], C["bg2"]
    s.configure(".", background=bg, foreground=fg, font=F["ui"],
                fieldbackground=C["bg2"], borderwidth=0)
    s.configure("TFrame", background=bg)
    s.configure("TLabel", background=bg, foreground=fg)
    s.configure("TButton", background=C["bg2"], foreground=fg, borderwidth=1,
                relief="flat", padding=(12, 5))
    s.map("TButton", background=[("active", C["border"]), ("pressed", C["bg0"])])
    s.configure("TEntry", fieldbackground=C["bg2"], foreground=fg,
                insertcolor=fg, borderwidth=1, relief="solid", padding=(8, 5))
    s.configure("TCombobox", fieldbackground=C["bg2"], foreground=fg,
                arrowcolor=fg, background=C["bg2"])
    s.map("TCombobox", fieldbackground=[("readonly", C["bg2"])])
    s.configure("Treeview", background=C["bg1"], foreground=fg,
                fieldbackground=C["bg1"], borderwidth=0)
    s.configure("Treeview.Heading", background=C["bg2"], foreground=C["text2"],
                font=F["bold"], borderwidth=1, relief="flat")
    s.map("Treeview", background=[("selected", C["accent"])],
          foreground=[("selected", "#fff")])
    s.configure("TNotebook", background=bg, borderwidth=0)
    s.configure("TNotebook.Tab", background=C["bg1"], foreground=C["text2"],
                padding=(16, 6), borderwidth=0)
    s.map("TNotebook.Tab", background=[("selected", C["bg2"])],
          foreground=[("selected", C["accent"])])
    s.configure("TScrollbar", background=C["bg1"], troughcolor=bg,
                borderwidth=0, arrowsize=0)
    s.configure("TPanedwindow", background=C["border"])
    s.configure("Accent.TButton", background=C["accent"], foreground="#fff",
                font=F["bold"], borderwidth=0, padding=(16, 7))
    s.map("Accent.TButton", background=[("active", C["accent_h"]),
          ("pressed", "#c0392b")])
    s.configure("Small.TButton", background=C["bg2"], foreground=C["text2"],
                font=F["xs"], padding=(6, 2))
    s.map("Small.TButton", background=[("active", C["border"])])


# ---- Stock List ----
class StockListFrame(ttk.Frame):
    def __init__(self, parent, on_select):
        super().__init__(parent)
        self.on_select = on_select
        self._all_stocks = []
        self._init()

    def _init(self):
        sf = ttk.Frame(self); sf.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(sf, text="search", font=F["xs"], fg=C["text3"],
                 bg=C["bg0"]).pack(anchor="w")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._filter())
        self.search_entry = tk.Entry(
            sf, textvariable=self.search_var, font=F["ui"],
            bg=C["bg2"], fg=C["text"], insertbackground=C["text"],
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=C["border"], highlightcolor=C["accent"])
        self.search_entry.pack(fill="x", pady=(2, 0))

        tk.Label(self, text="recent", font=F["xs"], fg=C["text3"],
                 bg=C["bg0"], anchor="w").pack(fill="x", padx=8, pady=(8, 2))
        self.recent_list = tk.Listbox(
            self, font=F["ui"], bg=C["bg1"], fg=C["text"],
            selectbackground=C["bg2"], selectforeground=C["text"],
            relief="flat", bd=0, highlightthickness=0, height=6)
        self.recent_list.pack(fill="x", padx=8, pady=(0, 4))
        self.recent_list.bind("<<ListboxSelect>>", self._on_recent)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=8, pady=4)

        self.count_label = tk.Label(
            self, text="all (0)", font=F["xs"], fg=C["text3"],
            bg=C["bg0"], anchor="w")
        self.count_label.pack(fill="x", padx=8, pady=(4, 2))
        self.stock_list = tk.Listbox(
            self, font=F["ui"], bg=C["bg1"], fg=C["text"],
            selectbackground=C["bg2"], selectforeground=C["text"],
            relief="flat", bd=0, highlightthickness=0)
        self.stock_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.stock_list.bind("<<ListboxSelect>>", self._on_select)
        self._load()

    def _load(self):
        if not SERVICE: return
        try:
            self._all_stocks = SERVICE.scan()
            self.count_label.config(text=f"all ({len(self._all_stocks)})")
            self._populate(self._all_stocks)
        except Exception as e:
            self.count_label.config(text=f"err: {e}")

    def _populate(self, symbols):
        self.stock_list.delete(0, "end")
        for s in symbols:
            code = s.get("code", s) if isinstance(s, dict) else s
            name = s.get("name", "") if isinstance(s, dict) else ""
            self.stock_list.insert("end", f"{code}  {name}" if name else str(code))

    def _filter(self):
        text = self.search_var.get().strip()
        if not text:
            self._populate(self._all_stocks); return
        filtered = [s for s in self._all_stocks
                    if text in (s.get("code", s) if isinstance(s, dict) else s)]
        self._populate(filtered)

    def _on_select(self, ev):
        sel = self.stock_list.curselection()
        if sel:
            self.on_select(self.stock_list.get(sel[0]).split()[0])

    def _on_recent(self, ev):
        sel = self.recent_list.curselection()
        if sel:
            self.on_select(self.recent_list.get(sel[0]).split()[0])

    def add_recent(self, code, name=""):
        display = f"{code}  {name}" if name else code
        items = list(self.recent_list.get(0, "end"))
        if display in items: items.remove(display)
        items.insert(0, display)
        self.recent_list.delete(0, "end")
        for item in items[:10]: self.recent_list.insert("end", item)

    def select_stock(self, code):
        for i in range(self.stock_list.size()):
            if self.stock_list.get(i).startswith(code):
                self.stock_list.selection_clear(0, "end")
                self.stock_list.selection_set(i)
                self.stock_list.see(i)
                break


# ---- K-line Chart ----
class ChartFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self._sym = None
        self._kline = self._ind = None
        self._on = {"ma": True, "boll": False, "macd": True, "rsi": True}
        self._btns = {}
        self._init()

    def _init(self):
        top = tk.Frame(self, bg=C["bg1"], height=36)
        top.pack(fill="x"); top.pack_propagate(False)
        self.sym_label = tk.Label(top, text="--", font=F["title"],
                                  fg=C["text"], bg=C["bg1"])
        self.sym_label.pack(side="left", padx=(12, 20))
        self.price_label = tk.Label(top, text="--", font=F["xxl"],
                                    fg=C["text"], bg=C["bg1"])
        self.price_label.pack(side="left", padx=(0, 8))
        self.chg_label = tk.Label(top, text="--", font=F["bold"],
                                  fg=C["text2"], bg=C["bg1"])
        self.chg_label.pack(side="left", padx=(0, 16))

        bf = tk.Frame(top, bg=C["bg1"]); bf.pack(side="right", padx=8)
        for ind in ["ma", "boll", "macd", "rsi"]:
            btn = tk.Label(bf, text=ind.upper(), font=F["xs"],
                           fg="#fff" if self._on[ind] else C["text2"],
                           bg=C["accent"] if self._on[ind] else C["bg2"],
                           padx=6, pady=1, cursor="hand2")
            btn.pack(side="left", padx=2)
            btn.bind("<Button-1>", lambda e, i=ind: self._toggle(i))
            self._btns[ind] = btn

        self.fig = Figure(figsize=(9, 5), dpi=100)
        self.fig.patch.set_facecolor(C["bg0"])
        self.canvas = FigureCanvasTkAgg(self.fig, self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.toolbar = NavigationToolbar2Tk(self.canvas, self)
        self.toolbar.update()
        for c in self.toolbar.winfo_children():
            try:
                c.config(bg=C["bg1"], fg=C["text2"],
                         activebackground=C["bg2"], bd=0, relief="flat")
            except Exception: pass
        self._empty()

    def _empty(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111); ax.set_facecolor(C["bg0"])
        ax.text(0.5, 0.5, "select stock", transform=ax.transAxes,
                ha="center", va="center", color=C["text3"], fontsize=14)
        ax.set_xticks([]); ax.set_yticks([])
        for s in ax.spines.values(): s.set_visible(False)
        self.canvas.draw()

    def _toggle(self, ind):
        self._on[ind] = not self._on[ind]
        b = self._btns[ind]
        b.config(fg="#fff" if self._on[ind] else C["text2"],
                 bg=C["accent"] if self._on[ind] else C["bg2"])
        if self._kline: self._draw()

    def load(self, symbol):
        self._sym = symbol
        if not SERVICE: return
        try:
            kline = SERVICE.get_kline(symbol, 200)
            info = SERVICE.get_info(symbol)
            ind = SERVICE.get_indicators(symbol)
        except Exception as e:
            print(f"load err: {e}"); return
        if not kline: self._empty(); return
        self._kline = kline; self._ind = ind
        if info:
            name = info.get("name", symbol)
            self.sym_label.config(text=f"{symbol}  {name}")
            price = info.get("price", info.get("close", 0))
            chg = info.get("change_pct", 0)
            self.price_label.config(text=f"{price:.2f}")
            color = C["up"] if chg >= 0 else C["down"]
            self.chg_label.config(
                text=f"{'+' if chg >= 0 else ''}{chg:.2f}%", fg=color)
            self.price_label.config(fg=color)
        self._draw()

    def _draw(self):
        if not self._kline: return
        df = pd.DataFrame(self._kline)
        df["date"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("date", inplace=True)
        df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                           "close": "Close", "volume": "Volume"}, inplace=True)

        hm = self._on.get("macd") and "macd" in (self._ind or {})
        hr = self._on.get("rsi") and "rsi" in (self._ind or {})
        npnl = 1 + int(hm) + int(hr)

        mc = mpf.make_marketcolors(
            up='#f85149', down='#2ea043', edge="inherit", wick="inherit",
            volume={'up': '#f85149', 'down': '#2ea043'})
        sty = mpf.make_mpf_style(
            marketcolors=mc, facecolor=C["bg0"], figcolor=C["bg0"],
            gridcolor=C["border"], gridstyle="--", y_on_right=True)

        def _v(dl, k="value"):
            if not dl: return []
            v = [d[k] for d in dl]
            if len(v) < len(df): v = [None] * (len(df) - len(v)) + v
            elif len(v) > len(df): v = v[-len(df):]
            return v

        ap = []
        if self._on.get("ma") and "mas" in (self._ind or {}):
            for p in [5, 10, 20, 60]:
                kk = f"ma{p}"
                if kk in self._ind["mas"]:
                    vv = _v(self._ind["mas"][kk])
                    if vv and len(vv) == len(df):
                        ap.append(mpf.make_addplot(vv, width=1.0))
        if self._on.get("boll") and "boll" in (self._ind or {}):
            for band, ls in [("upper", "--"), ("middle", "-"), ("lower", "--")]:
                vv = _v(self._ind["boll"], band)
                if vv and len(vv) == len(df):
                    ap.append(mpf.make_addplot(vv, width=0.8, linestyle=ls,
                                                color="#bc8cff"))
        if hm:
            ml = self._ind["macd"]; mpnl = 1
            dif = _v(ml, "macd"); dea = _v(ml, "signal")
            hist = _v(ml, "histogram")
            if dif and len(dif) == len(df):
                ap.append(mpf.make_addplot(dif, panel=mpnl, color=C["blue"],
                                           width=1.0))
                ap.append(mpf.make_addplot(dea, panel=mpnl, color=C["yellow"],
                                           width=1.0))
                if hist and len(hist) == len(df):
                    ch = ['#f85149' if h >= 0 else '#2ea043' for h in hist]
                    ap.append(mpf.make_addplot(hist, type="bar", panel=mpnl,
                                                color=ch, width=0.7))
        if hr:
            rl = self._ind["rsi"]; rpnl = 2 if hm else 1
            rv = _v(rl, "value")
            if rv and len(rv) == len(df):
                ap.append(mpf.make_addplot(rv, panel=rpnl, color=C["purple"],
                                           width=1.2, ylabel="RSI"))

        if npnl == 1: pr = (1,)
        elif npnl == 2: pr = (0.65, 0.35)
        else: pr = (0.5, 0.25, 0.25)

        try:
            plt.close(self.fig)
            fig, _ = mpf.plot(df, type="candle", style=sty,
                              addplot=ap if ap else None,
                              volume=True, panel_ratios=pr,
                              figsize=(9, 5), returnfig=True,
                              warn_too_much_data=200)
            self.fig = fig; self.canvas.figure = fig; self.canvas.draw()
        except Exception as e:
            print(f"draw err: {e}")
            import traceback; traceback.print_exc()
            self._empty()

    def refresh(self):
        if self._sym: self.load(self._sym)


# ---- Right Panel ----
class RightPanel(ttk.Frame):
    def __init__(self, parent, on_backtest):
        super().__init__(parent)
        self.on_backtest = on_backtest
        self._init()

    def _init(self):
        canvas = tk.Canvas(self, bg=C["bg0"], highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.sf = ttk.Frame(canvas)
        self.sf.bind("<Configure>",
                     lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.sf, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        self._quote_labels = {}
        self._build_quote_card()
        self._build_signal_card()
        self._build_ai_card()
        self._build_trend_card()
        self._build_btn()

    def _card(self, title):
        card = tk.Frame(self.sf, bg=C["bg1"], bd=0)
        card.pack(fill="x", padx=8, pady=(6, 0))
        h = tk.Frame(card, bg=C["bg1"]); h.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(h, text=title, font=F["bold"], fg=C["text"],
                 bg=C["bg1"]).pack(anchor="w")
        body = tk.Frame(card, bg=C["bg1"])
        body.pack(fill="x", padx=12, pady=(0, 10))
        return body

    def _row(self, parent, label):
        f = tk.Frame(parent, bg=C["bg1"]); f.pack(fill="x", pady=1)
        tk.Label(f, text=label, font=F["xs"], fg=C["text2"],
                 bg=C["bg1"], width=8, anchor="w").pack(side="left")
        val = tk.Label(f, text="--", font=F["sm"], fg=C["text"],
                       bg=C["bg1"], anchor="e"); val.pack(side="right")
        return val

    def _build_quote_card(self):
        body = self._card("quote")
        for lbl, key in [
            ("name", "name"), ("open", "open"), ("high", "high"),
            ("low", "low"), ("60d_hi", "high_60"), ("60d_lo", "low_60"),
            ("avg_vol", "avg_vol_20"), ("turn", "turnover"), ("time", "time"),
        ]:
            self._quote_labels[key] = self._row(body, lbl)

    def _build_signal_card(self):
        body = self._card("signals")
        self._signal_text = tk.Label(body, text="select stock", font=F["xs"],
                                     fg=C["text3"], bg=C["bg1"],
                                     wraplength=240, justify="left")
        self._signal_text.pack(fill="x")

    def _build_ai_card(self):
        body = self._card("DSA AI")
        self._ai_text = tk.Label(body, text="run DSA first", font=F["xs"],
                                 fg=C["text3"], bg=C["bg1"],
                                 wraplength=240, justify="left")
        self._ai_text.pack(fill="x")

    def _build_trend_card(self):
        body = self._card("DSA trend")
        self._trend_text = tk.Label(body, text="select stock", font=F["xs"],
                                    fg=C["text3"], bg=C["bg1"],
                                    wraplength=240, justify="left")
        self._trend_text.pack(fill="x")

    def _build_btn(self):
        bf = tk.Frame(self.sf, bg=C["bg0"]); bf.pack(fill="x", padx=8, pady=(8, 8))
        btn = tk.Label(bf, text="quick backtest", font=F["bold"], fg="#fff",
                       bg=C["accent"], padx=16, pady=8, cursor="hand2")
        btn.pack(fill="x")
        btn.bind("<Button-1>", lambda e: self.on_backtest())
        btn.bind("<Enter>", lambda e: btn.config(bg=C["accent_h"]))
        btn.bind("<Leave>", lambda e: btn.config(bg=C["accent"]))

    def update_for_symbol(self, symbol):
        try:
            info = SERVICE.get_info(symbol) if SERVICE else None
            if info:
                for key, lbl in self._quote_labels.items():
                    val = info.get(key, "--")
                    if val is None: val = "--"
                    if isinstance(val, float): lbl.config(text=f"{val:.2f}")
                    else: lbl.config(text=str(val))
        except Exception: pass
        try:
            ind = SERVICE.get_indicators(symbol) if SERVICE else None
            if ind:
                lines = []
                if "mas" in ind:
                    ma = ind["mas"]
                    if all(k in ma and ma[k] for k in ["ma5", "ma20"]):
                        m5 = ma["ma5"][-1]["value"]
                        m20 = ma["ma20"][-1]["value"]
                        lines.append(
                            f"MA: {'bull' if m5 > m20 else 'bear'} "
                            f"(MA5={m5:.2f} MA20={m20:.2f})")
                if "rsi" in ind and ind["rsi"]:
                    rv = ind["rsi"][-1]["value"]
                    st = "overbought" if rv > 70 else ("oversold" if rv < 30 else "neutral")
                    lines.append(f"RSI: {rv:.1f} ({st})")
                if "boll" in ind and ind["boll"]:
                    b = ind["boll"][-1]
                    lines.append(
                        f"BOLL: {b['upper']:.2f}/{b['middle']:.2f}/{b['lower']:.2f}")
                self._signal_text.config(
                    text="\n".join(lines) if lines else "no signal", fg=C["text"])
            else:
                self._signal_text.config(text="select stock", fg=C["text3"])
        except Exception: pass
        try:
            if DSA_READER and DSA_READER.available():
                views = DSA_READER.get_views(symbol=symbol, limit=5)
                if views:
                    lines = []
                    for v in views[:5]:
                        a = v.get("action", "?")
                        clr = (C["up"] if a in ("buy", "add") else
                               C["down"] if a in ("sell", "reduce") else C["text"])
                        lines.append(
                            f"{a} (score:{v.get('score',0):.0f}) "
                            f"{v.get('reason','')[:40]}")
                    self._ai_text.config(text="\n".join(lines), fg=C["text"])
                else:
                    self._ai_text.config(text="no AI views", fg=C["text3"])
            else:
                self._ai_text.config(text="DSA not connected", fg=C["text3"])
        except Exception: pass
        self._trend_analysis(symbol)

    def _trend_analysis(self, symbol):
        try:
            kline = SERVICE.get_kline(symbol, 120)
            ind = SERVICE.get_indicators(symbol)
            if not kline or not ind:
                self._trend_text.config(text="insufficient data", fg=C["text3"])
                return
            closes = [k["close"] for k in kline]
            vols = [k["volume"] for k in kline]
            cp = closes[-1]
            score = 50; sigs = []; risks = []
            if "mas" in ind:
                ma = ind["mas"]
                keys_ok = all(k in ma and ma[k] for k in
                              ["ma5", "ma10", "ma20", "ma60"])
                if keys_ok:
                    m5, m10, m20, m60 = [
                        ma[k][-1]["value"] for k in ["ma5", "ma10", "ma20", "ma60"]]
                    if m5 > m10 > m20 > m60 and cp > m5:
                        score += 20; sigs.append("strong bull align")
                    elif m5 > m10 > m20:
                        score += 12; sigs.append("bull align")
                    elif m5 < m10 < m20 < m60 and cp < m5:
                        score -= 20; risks.append("strong bear align")
                    elif m5 < m10 < m20:
                        score -= 12; risks.append("bear align")
                    elif abs(m5 - m20) / m20 < 0.02:
                        sigs.append("MA consolidation")
            if "macd" in ind and ind["macd"]:
                ml = ind["macd"]; l = ml[-1]
                if l["macd"] > 0 and l["histogram"] > 0: score += 5
                elif l["macd"] < 0 and l["histogram"] < 0: score -= 5
            if "rsi" in ind and ind["rsi"]:
                rv = ind["rsi"][-1]["value"]
                if rv > 80:
                    score -= 12; risks.append(f"RSI={rv:.0f} extreme OB")
                elif rv > 70:
                    score -= 5; risks.append(f"RSI={rv:.0f} OB")
                elif rv < 20:
                    score += 12; sigs.append(f"RSI={rv:.0f} extreme OS")
                elif rv < 30:
                    score += 5; sigs.append(f"RSI={rv:.0f} OS")
            if len(vols) >= 20:
                avg_v = np.mean(vols[-20:])
                vr = vols[-1] / avg_v if avg_v > 0 else 1
                chg = ((closes[-1] - closes[-2]) / closes[-2]
                       if len(closes) >= 2 else 0)
                if vr > 1.5 and chg > 0.02:
                    score += 8; sigs.append(f"hi-vol up {vr:.1f}x")
                elif vr > 1.5 and chg < -0.02:
                    score -= 8; risks.append(f"hi-vol down {vr:.1f}x")
                elif vr < 0.5:
                    score += 2; sigs.append(f"lo-vol {vr:.1f}x")
            if "boll" in ind and ind["boll"]:
                b = ind["boll"][-1]
                if cp >= b["upper"] * .98:
                    score -= 3; risks.append("near BB upper")
                elif cp <= b["lower"] * 1.02:
                    score += 3; sigs.append("near BB lower")
            score = max(0, min(100, score))
            if score >= 75: sg, clr = "STRONG BUY", C["up"]
            elif score >= 60: sg, clr = "BUY", C["up"]
            elif score >= 45: sg, clr = "HOLD", C["yellow"]
            elif score >= 30: sg, clr = "SELL", C["down"]
            else: sg, clr = "STRONG SELL", C["down"]
            lines = [f">> {sg}  score {score:.0f}/100"]
            if sigs: lines.append("+ " + " | ".join(sigs))
            if risks: lines.append("- " + " | ".join(risks))
            self._trend_text.config(text="\n".join(lines), fg=clr)
        except Exception:
            self._trend_text.config(text="analysis failed", fg=C["text3"])


# ---- Bottom Panel ----
class BottomPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self._init()

    def _init(self):
        self.nb = ttk.Notebook(self); self.nb.pack(fill="both", expand=True)
        self.trade_text = self._tab("trades")
        self.equity_text = self._tab("equity")
        self.ai_text = self._tab("AI paper")
        self.log_text = self._tab("log")

    def _tab(self, title):
        t = tk.Text(self.nb, font=F["mono"], bg=C["bg1"], fg=C["text"],
                    wrap="none", bd=0, relief="flat", state="disabled", height=6)
        self.nb.add(t, text=f"  {title}  ")
        return t

    def _append(self, w, text):
        w.config(state="normal"); w.insert("end", text); w.see("end")
        w.config(state="disabled")

    def log(self, msg):
        self._append(self.log_text,
                     f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")

    def show_backtest(self, result):
        if not result: return
        s = result.get("summary", {})
        trades = result.get("trades", [])
        self.trade_text.config(state="normal")
        self.trade_text.delete("1.0", "end")
        lines = [
            f"strategy: {s.get('strategy','?')}  "
            f"return: {s.get('total_return_pct',0):.2f}%  "
            f"win: {s.get('win_rate_pct',0):.1f}%  "
            f"trades: {s.get('trade_count',0)}",
            "-" * 50]
        for t in trades[:50]:
            lines.append(
                f"{t.get('date','?')} {t.get('action','?'):4s} "
                f"price={t.get('price',0):.2f} x{t.get('shares',0)} "
                f"pnl={t.get('pnl',0):.2f}")
        self.trade_text.insert("1.0", "\n".join(lines))
        self.trade_text.config(state="disabled")
        eq = result.get("equity_curve", [])
        self.equity_text.config(state="normal")
        self.equity_text.delete("1.0", "end")
        elines = ["date".ljust(14) + "equity", "-" * 30]
        for pt in eq[:100]:
            elines.append(f"{str(pt.get('date','?')):14s} {pt.get('equity',0):.2f}")
        self.equity_text.insert("1.0", "\n".join(elines))
        self.equity_text.config(state="disabled")
        self.nb.select(0)

    def refresh_ai_paper(self):
        if not AI_PAPER:
            self._set(self.ai_text, "AI paper not init"); return
        try:
            self._set(self.ai_text,
                      json.dumps(AI_PAPER.status(), ensure_ascii=False, indent=2))
        except Exception as e:
            self._set(self.ai_text, f"err: {e}")

    def _set(self, w, text):
        w.config(state="normal"); w.delete("1.0", "end")
        w.insert("1.0", text); w.config(state="disabled")


# ---- Backtest Dialog ----
class BacktestDialog(tk.Toplevel):
    def __init__(self, parent, symbol=""):
        super().__init__(parent)
        self.title("backtest")
        self.geometry("440x480")
        self.configure(bg=C["bg1"])
        self.resizable(False, False)
        self.transient(parent); self.grab_set()
        self._sym = symbol
        self._init()

    def _init(self):
        P = {"padx": 12, "pady": 4}
        tk.Label(self, text="backtest", font=F["title"], fg=C["text"],
                 bg=C["bg1"]).pack(anchor="w", **P, pady=(16, 8))
        tk.Label(self, text="stock code", font=F["sm"], fg=C["text2"],
                 bg=C["bg1"]).pack(anchor="w", **P)
        self.sym_entry = tk.Entry(
            self, font=F["ui"], bg=C["bg2"], fg=C["text"],
            insertbackground=C["text"], relief="flat", bd=0,
            highlightthickness=1, highlightbackground=C["border"])
        self.sym_entry.pack(fill="x", ipady=4, **P)
        if self._sym: self.sym_entry.insert(0, self._sym)

        tk.Label(self, text="strategy", font=F["sm"], fg=C["text2"],
                 bg=C["bg1"]).pack(anchor="w", **P)
        self.strategy_var = tk.StringVar(value="dual_ma")
        cb = ttk.Combobox(
            self, textvariable=self.strategy_var, font=F["ui"],
            state="readonly",
            values=["dual_ma", "trend_5d", "bollinger", "bb_rsi",
                    "pullback_20d", "pullback_deep", "breakout"])
        cb.pack(fill="x", ipady=2, **P)

        pf = tk.Frame(self, bg=C["bg1"]); pf.pack(fill="x", **P)
        self._params = {}
        for label, default in [("capital", "100000"), ("commission", "0.0003"),
                                ("stop %", "0.05"), ("take %", "0.15")]:
            tk.Label(pf, text=label, font=F["xs"], fg=C["text2"],
                     bg=C["bg1"]).pack(side="left", padx=2)
            e = tk.Entry(pf, font=F["sm"], bg=C["bg2"], fg=C["text"],
                         insertbackground=C["text"], width=10, relief="flat",
                         bd=0, highlightthickness=1,
                         highlightbackground=C["border"])
            e.insert(0, default); e.pack(side="left", padx=2, ipady=2)
            self._params[label] = e

        bf = tk.Frame(self, bg=C["bg1"]); bf.pack(fill="x", **P, pady=(12, 8))
        cancel = tk.Label(bf, text="cancel", font=F["ui"], fg=C["text2"],
                          bg=C["bg2"], padx=20, pady=6, cursor="hand2")
        cancel.pack(side="left")
        cancel.bind("<Button-1>", lambda e: self.destroy())
        run_btn = tk.Label(bf, text="run", font=F["bold"], fg="#fff",
                           bg=C["green_btn"], padx=20, pady=6, cursor="hand2")
        run_btn.pack(side="right")
        run_btn.bind("<Button-1>", lambda e: self._run())
        run_btn.bind("<Enter>", lambda e: run_btn.config(bg="#2ea043"))
        run_btn.bind("<Leave>", lambda e: run_btn.config(bg=C["green_btn"]))

        self.result_text = tk.Text(
            self, font=F["mono"], bg=C["bg0"], fg=C["text"], wrap="word",
            bd=0, relief="flat", height=8, state="disabled")
        self.result_text.pack(fill="both", expand=True, **P, pady=(4, 12))

    def _run(self):
        symbol = self.sym_entry.get().strip()
        if not symbol:
            messagebox.showwarning("err", "enter stock code", parent=self)
            return
        try:
            result = SERVICE.run_backtest(
                symbol, self.strategy_var.get(),
                float(self._params["capital"].get()),
                float(self._params["commission"].get()),
                float(self._params["stop %"].get()),
                float(self._params["take %"].get()))
        except Exception as e:
            self._set_result(f"backtest failed: {e}"); return
        s = result.get("summary", {})
        lines = [
            f"strategy: {s.get('strategy','?')}",
            f"return: {s.get('total_return_pct',0):.2f}%",
            f"annual: {s.get('annual_return_pct',0):.2f}%",
            f"max dd: {s.get('max_drawdown_pct',0):.2f}%",
            f"sharpe: {s.get('sharpe_ratio',0):.2f}",
            f"win rate: {s.get('win_rate_pct',0):.1f}%",
            f"trades: {s.get('trade_count',0)}",
            f"capital: {s.get('initial_capital',0):,.0f} -> "
            f"{s.get('final_capital',0):,.0f}",
        ]
        self._set_result("\n".join(lines))
        if hasattr(self.master, 'on_backtest_done'):
            self.master.on_backtest_done(result)

    def _set_result(self, text):
        self.result_text.config(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", text)
        self.result_text.config(state="disabled")


# ---- Main Window ----
class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("QTrade")
        self.geometry("1440x920")
        self.minsize(1000, 650)
        self.configure(bg=C["bg0"])
        self._sym = None
        apply_dark_theme()
        self._init_toolbar()
        self._init_main()
        self._init_bottom()
        self._update_clock()
        self.after(5000, self._auto_refresh)
        self._log("started")

    def _init_toolbar(self):
        bar = tk.Frame(self, bg=C["bg1"], height=42)
        bar.pack(fill="x"); bar.pack_propagate(False)
        tk.Label(bar, text="QTrade", font=("Segoe UI", 16, "bold"),
                 fg=C["accent"], bg=C["bg1"]).pack(side="left", padx=(14, 20))
        self.toolbar_search = tk.Entry(
            bar, font=F["ui"], bg=C["bg2"], fg=C["text"],
            insertbackground=C["text"], relief="flat", bd=0, width=18,
            highlightthickness=1, highlightbackground=C["border"])
        self.toolbar_search.pack(side="left", ipady=3, padx=(0, 8))
        self.toolbar_search.bind("<Return>", lambda e: self._toolbar_search())
        self.toolbar_search.config(highlightbackground=C["border"],
                                   highlightcolor=C["accent"])
        for text, cmd, accent in [
            ("refresh", self._refresh, False),
            ("backtest", self._open_backtest, True),
        ]:
            btn = tk.Label(bar, text=text,
                           font=F["bold"] if accent else F["ui"],
                           fg="#fff" if accent else C["text"],
                           bg=C["accent"] if accent else C["bg2"],
                           padx=14, pady=3, cursor="hand2")
            btn.pack(side="left", padx=3)
            btn.bind("<Button-1>", lambda e, c=cmd: c())
            if accent:
                btn.bind("<Enter>", lambda e, b=btn: b.config(bg=C["accent_h"]))
                btn.bind("<Leave>", lambda e, b=btn: b.config(bg=C["accent"]))
        self.clock_label = tk.Label(bar, text="", font=F["sm"],
                                    fg=C["text3"], bg=C["bg1"])
        self.clock_label.pack(side="right", padx=14)

    def _init_main(self):
        pw = tk.PanedWindow(self, orient="horizontal", bg=C["border"],
                            sashwidth=1, sashrelief="flat")
        pw.pack(fill="both", expand=True)
        self.stock_list = StockListFrame(pw, self._on_stock)
        pw.add(self.stock_list, width=260, minsize=180)
        self.chart_frame = ChartFrame(pw)
        pw.add(self.chart_frame, width=800, minsize=400)
        self.right_panel = RightPanel(pw, self._quick_backtest)
        pw.add(self.right_panel, width=290, minsize=220)

    def _init_bottom(self):
        self.bottom_panel = BottomPanel(self)
        self.bottom_panel.pack(fill="x")

    def _update_clock(self):
        self.clock_label.config(text=datetime.now().strftime("%H:%M:%S"))
        self.after(1000, self._update_clock)

    def _auto_refresh(self):
        if self._sym:
            try:
                self.chart_frame.refresh()
                self.right_panel.update_for_symbol(self._sym)
            except Exception: pass
        self.after(5000, self._auto_refresh)

    def _on_stock(self, symbol):
        self._sym = symbol
        self.chart_frame.load(symbol)
        self.right_panel.update_for_symbol(symbol)
        try:
            info = SERVICE.get_info(symbol)
            self.stock_list.add_recent(symbol,
                                       info.get("name", "") if info else "")
        except Exception:
            self.stock_list.add_recent(symbol)
        self._log(f"selected: {symbol}")

    def _toolbar_search(self):
        text = self.toolbar_search.get().strip()
        if len(text) == 6 and text.isdigit():
            self._on_stock(text)
            self.stock_list.select_stock(text)

    def _refresh(self):
        self._log("refresh")
        if self._sym:
            self.chart_frame.load(self._sym)
            self.right_panel.update_for_symbol(self._sym)

    def _open_backtest(self):
        BacktestDialog(self, self._sym or "")

    def _quick_backtest(self):
        if not self._sym:
            messagebox.showinfo("info", "select a stock first")
            return
        BacktestDialog(self, self._sym)

    def on_backtest_done(self, result):
        if result: self.bottom_panel.show_backtest(result)

    def _log(self, msg):
        self.bottom_panel.log(msg)


# ---- Entry ----
def main():
    global SERVICE, DSA_READER, AI_PAPER
    import argparse
    p = argparse.ArgumentParser(description="QTrade Desktop")
    p.add_argument("--data-dir", default=None)
    p.add_argument("--csv-only", action="store_true")
    args = p.parse_args()
    data_dir = find_data_dir(args.data_dir)
    live = not args.csv_only
    SERVICE = DataService(data_dir, live=live)
    syms = SERVICE.scan()
    DSA_READER = DsaSignalReader()
    AI_PAPER = AiPaperTrader()
    if DSA_READER.available():
        print(f"DSA: {DSA_READER.db_path}")
    if live and TencentLiveSource.available():
        print(f"live mode, {len(syms)} stocks")
    else:
        SERVICE.live = False
        print(f"offline mode, {len(syms)} stocks")
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
