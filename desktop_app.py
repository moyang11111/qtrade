#!/usr/bin/env python
"""
QTrade Desktop — PySide6 原生桌面交易终端
===========================================
基于 UI/UX Pro Max 设计规范：
  - 暗黑主题 + 语义颜色 Token
  - 三栏布局（股票列表 | K线图表 | 信息面板）
  - 底部多标签面板 + 回测对话框
  - 复用 server.py 的 DataService 后端逻辑
"""

import sys
import json
from pathlib import Path

# ── 后端导入 ──
sys.path.insert(0, str(Path(__file__).parent))
from server import DataService, DsaSignalReader, AiPaperTrader, TencentLiveSource, find_data_dir

# ── PySide6 / matplotlib ──
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QToolBar,
    QSplitter, QListWidget, QListWidgetItem, QLabel,
    QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
    QLineEdit, QTabWidget, QTextEdit, QDialog,
    QComboBox, QDoubleSpinBox, QSpinBox, QFormLayout,
    QFrame, QScrollArea, QSizePolicy, QMessageBox,
    QDialogButtonBox, QHeaderView,
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QFont, QColor, QPalette, QAction

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
import matplotlib as mpl

# ── 中文字体配置 ──
# Windows 下优先使用微软雅黑，回退 SimHei
for font_name in ["Microsoft YaHei", "SimHei", "PingFang SC", "Noto Sans CJK SC"]:
    try:
        mpl.font_manager.findfont(font_name, fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        break
    except Exception:
        continue

import mplfinance as mpf
import pandas as pd

from desktop.theme import (
    dark_stylesheet,
    BG_PRIMARY, BG_SECONDARY, BG_TERTIARY, BORDER,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_MUTED,
    ACCENT, ACCENT_HOVER, UP_COLOR, DOWN_COLOR, YELLOW, BLUE, PURPLE,
    SPACING_SM, SPACING_MD, SPACING_LG, SPACING_XL,
    RADIUS_MD,
)

# ===========================================================================
# 数据服务（复用 server.py 的后端逻辑）
# ===========================================================================

# 全局单例，由 main() 初始化
SERVICE = None
DSA_READER = None
AI_PAPER = None


# ===========================================================================
# 股票列表组件
# ===========================================================================

class StockListWidget(QWidget):
    """左栏：股票搜索 + 自选股 + 全 A 股列表"""

    stock_selected = Signal(str)  # 发出选中的股票代码

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_stocks = []
        self._setup_ui()
        self._load_stocks()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 搜索框
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索股票代码...")
        self.search_input.textChanged.connect(self._on_search)
        layout.addWidget(self.search_input)

        # 标题：最近浏览
        lbl_recent = QLabel("★ 自选股 · 最近浏览")
        lbl_recent.setProperty("cssClass", "section-title")
        layout.addWidget(lbl_recent)

        self.recent_list = QListWidget()
        self.recent_list.setMaximumHeight(120)
        self.recent_list.itemClicked.connect(self._on_stock_clicked)
        layout.addWidget(self.recent_list)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        layout.addWidget(sep)

        # 标题：全部 A 股
        self.count_label = QLabel("全部 A 股 (--)")
        self.count_label.setProperty("cssClass", "section-title")
        layout.addWidget(self.count_label)

        self.stock_list = QListWidget()
        self.stock_list.itemClicked.connect(self._on_stock_clicked)
        layout.addWidget(self.stock_list)

    def _load_stocks(self):
        """加载股票列表。"""
        if SERVICE is None:
            return
        try:
            symbols = SERVICE.scan()
            self._all_stocks = symbols
            self.count_label.setText(f"全部 A 股 ({len(symbols)})")
            self._populate_list(symbols)
        except Exception as e:
            self.count_label.setText(f"加载失败: {e}")

    def _populate_list(self, symbols):
        self.stock_list.clear()
        for s in symbols:
            code = s.get("code", s) if isinstance(s, dict) else s
            name = s.get("name", "") if isinstance(s, dict) else ""
            display = f"{code}  {name}" if name else str(code)
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, str(code))
            self.stock_list.addItem(item)

    def _on_search(self, text):
        text = text.strip()
        if not text:
            self._populate_list(self._all_stocks)
            return
        filtered = [s for s in self._all_stocks
                    if text in str(s.get("code", s) if isinstance(s, dict) else s)
                    or text in str(s.get("name", "") if isinstance(s, dict) else "")]
        self._populate_list(filtered)

    def _on_stock_clicked(self, item):
        code = item.data(Qt.ItemDataRole.UserRole)
        self.stock_selected.emit(code)

    def add_recent(self, code, name=""):
        """添加最近浏览条目（去重，最前）。"""
        display = f"{code}  {name}" if name else code
        # 去重
        for i in range(self.recent_list.count()):
            if self.recent_list.item(i).data(Qt.ItemDataRole.UserRole) == code:
                self.recent_list.takeItem(i)
                break
        item = QListWidgetItem(display)
        item.setData(Qt.ItemDataRole.UserRole, code)
        self.recent_list.insertItem(0, item)
        # 限制 10 条
        while self.recent_list.count() > 10:
            self.recent_list.takeItem(self.recent_list.count() - 1)


# ===========================================================================
# K 线图表组件
# ===========================================================================

class ChartWidget(QWidget):
    """中间：mplfinance K 线图 + 指标面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_symbol = None
        self._kline_data = None
        self._indicators = None
        self._indicators_on = {"ma": True, "boll": False, "macd": True, "rsi": True}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部信息栏
        info_bar = QWidget()
        info_bar.setStyleSheet(f"background-color: {BG_SECONDARY}; padding: 6px {SPACING_MD}px; border-bottom: 1px solid {BORDER};")
        info_layout = QHBoxLayout(info_bar)
        info_layout.setContentsMargins(SPACING_MD, 4, SPACING_MD, 4)
        info_layout.setSpacing(SPACING_LG)

        self.sym_name_label = QLabel("——")
        self.sym_name_label.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {TEXT_PRIMARY}; background: transparent;")
        info_layout.addWidget(self.sym_name_label)

        self.price_label = QLabel("——")
        self.price_label.setStyleSheet(f"font-size: 18px; font-weight: 700; font-family: \"Cascadia Code\"; background: transparent;")
        info_layout.addWidget(self.price_label)

        self.chg_label = QLabel("——")
        self.chg_label.setStyleSheet(f"font-size: 14px; background: transparent;")
        info_layout.addWidget(self.chg_label)

        info_layout.addStretch()

        # 指标切换按钮
        self._indicator_btns = {}
        for ind in ["ma", "boll", "macd", "rsi"]:
            btn = QPushButton(ind.upper())
            btn.setCheckable(True)
            btn.setChecked(self._indicators_on[ind])
            btn.setFixedSize(44, 24)
            btn.toggled.connect(lambda checked, i=ind: self._toggle_indicator(i, checked))
            self._indicator_btns[ind] = btn
            self._update_indicator_btn_style(ind)
            info_layout.addWidget(btn)

        layout.addWidget(info_bar)

        # matplotlib 图表
        self.figure = Figure(figsize=(10, 6), dpi=100)
        self.figure.patch.set_facecolor(BG_PRIMARY)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.canvas)

        # matplotlib 缩放/平移工具栏
        self.toolbar = NavigationToolbar(self.canvas, self)
        self.toolbar.setStyleSheet(f"""
            NavigationToolbar2QT {{ background: {BG_SECONDARY}; border-top: 1px solid {BORDER}; padding: 2px; }}
            NavigationToolbar2QT QToolButton {{ background: transparent; border: none; color: {TEXT_SECONDARY}; padding: 2px 4px; }}
            NavigationToolbar2QT QToolButton:hover {{ background: {BG_TERTIARY}; border-radius: 3px; }}
            NavigationToolbar2QT QToolButton:checked {{ background: {ACCENT}; color: white; border-radius: 3px; }}
            NavigationToolbar2QT QLabel {{ color: {TEXT_MUTED}; font-size: 10px; background: transparent; }}
        """)
        layout.addWidget(self.toolbar)

        # 初始空图
        self._draw_empty()

    def _draw_empty(self):
        fig = Figure(figsize=(10, 6), dpi=100)
        fig.patch.set_facecolor(BG_PRIMARY)
        ax = fig.add_subplot(111)
        ax.set_facecolor(BG_PRIMARY)
        ax.text(0.5, 0.5, "选择股票查看 K 线", transform=ax.transAxes,
                ha="center", va="center", color=TEXT_MUTED, fontsize=14)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        self._replace_chart_canvas(fig)

    def load_symbol(self, symbol: str):
        """加载并显示某只股票的 K 线数据。"""
        self._current_symbol = symbol
        if SERVICE is None:
            return

        try:
            kline = SERVICE.get_kline(symbol, limit=200)
            info = SERVICE.get_info(symbol)
            indicators = SERVICE.get_indicators(symbol)
        except Exception as e:
            print(f"加载 {symbol} 失败: {e}")
            return

        if not kline:
            self._draw_empty()
            return

        self._kline_data = kline
        self._indicators = indicators

        # 更新信息栏
        if info:
            name = info.get("name", symbol)
            self.sym_name_label.setText(f"{symbol} {name}")
            price = info.get("price", info.get("close", 0))
            chg_pct = info.get("change_pct", 0)
            self.price_label.setText(f"{price:.2f}")
            color = UP_COLOR if chg_pct >= 0 else DOWN_COLOR
            sign = "+" if chg_pct >= 0 else ""
            self.chg_label.setText(f"{sign}{chg_pct:.2f}%")
            self.chg_label.setStyleSheet(f"font-size: 14px; color: {color}; background: transparent;")
            self.price_label.setStyleSheet(
                f"font-size: 18px; font-weight: 700; color: {color}; "
                f"font-family: \"Cascadia Code\"; background: transparent;"
            )

        self._draw_chart()

    def _update_indicator_btn_style(self, ind: str = None):
        """直接用 setStyleSheet，不走 cssClass（避免 Qt property 选择器失效）"""
        for name, btn in self._indicator_btns.items():
            if ind and name != ind:
                continue
            on = self._indicators_on[name]
            if on:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        font-size: 10px; padding: 1px 3px; border-radius: 3px; font-weight: 600;
                        background-color: {ACCENT}; border: 1px solid {ACCENT_HOVER}; color: white;
                    }}
                    QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        font-size: 10px; padding: 1px 3px; border-radius: 3px;
                        background-color: {BG_TERTIARY}; border: 1px solid {BORDER}; color: {TEXT_SECONDARY};
                    }}
                    QPushButton:hover {{ background-color: {BORDER}; }}
                """)

    def _toggle_indicator(self, ind: str, on: bool):
        self._indicators_on[ind] = on
        self._update_indicator_btn_style(ind)
        if self._kline_data:
            self._draw_chart()

    def _replace_chart_canvas(self, fig):
        """替换 canvas 和 toolbar（确保缩放/平移功能绑定到正确的 figure）"""
        layout = self.layout()
        # 移除旧 toolbar 和 canvas
        old_toolbar = self.toolbar
        old_canvas = self.canvas
        layout.removeWidget(old_toolbar)
        layout.removeWidget(old_canvas)
        old_toolbar.deleteLater()
        old_canvas.deleteLater()
        import matplotlib.pyplot as plt
        if hasattr(self, 'figure') and self.figure is not None:
            plt.close(self.figure)

        # 创建新 canvas + toolbar
        self.figure = fig
        self.canvas = FigureCanvas(fig)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.canvas)

        self.toolbar = NavigationToolbar(self.canvas, self)
        self.toolbar.setStyleSheet(f"""
            NavigationToolbar2QT {{ background: {BG_SECONDARY}; border-top: 1px solid {BORDER}; padding: 2px; }}
            NavigationToolbar2QT QToolButton {{ background: transparent; border: none; color: {TEXT_SECONDARY}; padding: 2px 4px; }}
            NavigationToolbar2QT QToolButton:hover {{ background: {BG_TERTIARY}; border-radius: 3px; }}
            NavigationToolbar2QT QToolButton:checked {{ background: {ACCENT}; color: white; border-radius: 3px; }}
            NavigationToolbar2QT QLabel {{ color: {TEXT_MUTED}; font-size: 10px; background: transparent; }}
        """)
        layout.addWidget(self.toolbar)

    def _draw_chart(self):
        if not self._kline_data:
            return

        # K 线 DataFrame
        df = pd.DataFrame(self._kline_data)
        df["date"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("date", inplace=True)
        df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                           "close": "Close", "volume": "Volume"}, inplace=True)

        # 判断面板数
        has_macd = self._indicators_on.get("macd") and "macd" in (self._indicators or {})
        has_rsi = self._indicators_on.get("rsi") and "rsi" in (self._indicators or {})
        n_panels = 1 + int(has_macd) + int(has_rsi)

        # mplfinance 暗黑风格
        mc = mpf.make_marketcolors(
            up='#f85149', down='#2ea043',
            edge="inherit", wick="inherit",
            volume={'up': '#f85149', 'down': '#2ea043'},
        )
        style = mpf.make_mpf_style(
            marketcolors=mc, facecolor=BG_PRIMARY,
            figcolor=BG_PRIMARY, gridcolor=BORDER,
            gridstyle="--", y_on_right=True,
        )

        add_plots = []

        def _extract_values(data_list, key="value"):
            """从 [{time, key}, ...] 提取纯值列表，对齐到 df 长度。"""
            if not data_list:
                return []
            vals = [d[key] for d in data_list]
            # 填充/截断 到与 K 线等长
            if len(vals) < len(df):
                vals = [None] * (len(df) - len(vals)) + vals
            elif len(vals) > len(df):
                vals = vals[-len(df):]
            return vals

        # MA（key 是 "mas"，不是 "ma"）
        if self._indicators_on.get("ma") and "mas" in (self._indicators or {}):
            ma_data = self._indicators["mas"]
            for period in [5, 10, 20, 60]:
                key = f"ma{period}"
                if key in ma_data:
                    vals = _extract_values(ma_data[key])
                    if vals and len(vals) == len(df):
                        add_plots.append(mpf.make_addplot(vals, width=1.0))

        # BOLL
        if self._indicators_on.get("boll") and "boll" in (self._indicators or {}):
            for band, ls in [("upper", "--"), ("middle", "-"), ("lower", "--")]:
                vals = _extract_values(self._indicators["boll"], band)
                if vals and len(vals) == len(df):
                    add_plots.append(mpf.make_addplot(
                        vals, width=0.8, linestyle=ls, color="#bc8cff"))

        # MACD（key: "macd"→DIF, "signal"→DEA, "histogram"→柱）
        if has_macd:
            macd_list = self._indicators["macd"]
            macd_panel = 1
            dif = _extract_values(macd_list, "macd")
            dea = _extract_values(macd_list, "signal")
            hist = _extract_values(macd_list, "histogram")
            if dif and len(dif) == len(df):
                add_plots.append(mpf.make_addplot(dif, panel=macd_panel, color=BLUE, width=1.0))
                add_plots.append(mpf.make_addplot(dea, panel=macd_panel, color=YELLOW, width=1.0))
                if hist and len(hist) == len(df):
                    colors_hist = ['#f85149' if h >= 0 else '#2ea043' for h in hist]
                    add_plots.append(mpf.make_addplot(hist, type="bar", panel=macd_panel,
                                                      color=colors_hist, width=0.7))

        # RSI
        if has_rsi:
            rsi_list = self._indicators["rsi"]
            rsi_panel = 2 if has_macd else 1
            rsi_vals = _extract_values(rsi_list, "value")
            if rsi_vals and len(rsi_vals) == len(df):
                add_plots.append(mpf.make_addplot(rsi_vals, panel=rsi_panel,
                                                  color=PURPLE, width=1.2, ylabel="RSI"))

        # 面板比例
        if n_panels == 1:
            panel_ratios = (1,)
        elif n_panels == 2:
            panel_ratios = (0.65, 0.35)
        else:
            panel_ratios = (0.5, 0.25, 0.25)

        try:
            fig, axlist = mpf.plot(
                df, type="candle", style=style,
                addplot=add_plots if add_plots else None,
                volume=True,
                panel_ratios=panel_ratios,
                figsize=(10, 6),
                returnfig=True,
                warn_too_much_data=200,
            )
            self._replace_chart_canvas(fig)
        except Exception as e:
            print(f"绘图错误: {e}")
            import traceback
            traceback.print_exc()
            self._draw_empty()
            return

        self.canvas.draw()


# ===========================================================================
# 右栏面板
# ===========================================================================

class QuotePanel(QWidget):
    """行情报价卡片"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_SM, SPACING_MD, SPACING_SM)
        layout.setSpacing(4)

        title = QLabel("行情报价")
        title.setProperty("cssClass", "card-title")
        layout.addWidget(title)

        self._rows = {}
        fields = [
            ("名称", "name"), ("今开", "open"), ("最高", "high"), ("最低", "low"),
            ("60日最高", "high_60"), ("60日最低", "low_60"),
            ("20日均量", "avg_vol_20"), ("换手率", "turnover"), ("行情时间", "time"),
        ]
        for label, key in fields:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 2, 0, 2)
            row_layout.setSpacing(SPACING_SM)

            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; background: transparent; min-width: 60px;")
            row_layout.addWidget(lbl)

            val = QLabel("——")
            val.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 11px; font-family: \"Cascadia Code\"; background: transparent;")
            row_layout.addWidget(val)
            row_layout.addStretch()

            layout.addWidget(row)
            self._rows[key] = val

        layout.addStretch()

    def update_info(self, info: dict):
        if not info:
            return
        for key, lbl in self._rows.items():
            val = info.get(key, "——")
            if val is None:
                val = "——"
            if isinstance(val, float):
                lbl.setText(f"{val:.2f}")
            else:
                lbl.setText(str(val))


class SignalPanel(QWidget):
    """策略信号面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_SM, SPACING_MD, SPACING_SM)
        layout.setSpacing(4)

        title = QLabel("策略信号")
        title.setProperty("cssClass", "card-title")
        layout.addWidget(title)

        self.signal_text = QLabel("请选择股票")
        self.signal_text.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;")
        self.signal_text.setWordWrap(True)
        layout.addWidget(self.signal_text)

        layout.addStretch()

    def update_signals(self, indicators: dict):
        if not indicators:
            self.signal_text.setText("请选择股票")
            self.signal_text.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;")
            return

        lines = []
        # MA（key 是 "mas"）
        if "mas" in indicators:
            ma = indicators["mas"]
            if "ma5" in ma and "ma20" in ma:
                ma5_last = ma["ma5"][-1]["value"] if ma["ma5"] else 0
                ma20_last = ma["ma20"][-1]["value"] if ma["ma20"] else 0
                if ma5_last > ma20_last:
                    lines.append(f"<span style='color:{UP_COLOR}'>▲ MA5({ma5_last:.2f}) &gt; MA20({ma20_last:.2f}) 多头</span>")
                else:
                    lines.append(f"<span style='color:{DOWN_COLOR}'>▼ MA5({ma5_last:.2f}) &lt; MA20({ma20_last:.2f}) 空头</span>")

        # RSI
        if "rsi" in indicators:
            rsi_list = indicators["rsi"]
            if rsi_list:
                rsi_val = rsi_list[-1]["value"]
                if rsi_val > 70:
                    lines.append(f"<span style='color:{DOWN_COLOR}'>RSI={rsi_val:.1f}（超买）</span>")
                elif rsi_val < 30:
                    lines.append(f"<span style='color:{UP_COLOR}'>RSI={rsi_val:.1f}（超卖）</span>")
                else:
                    lines.append(f"RSI={rsi_val:.1f}（中性）")

        # BOLL
        if "boll" in indicators:
            boll_list = indicators["boll"]
            if boll_list:
                last = boll_list[-1]
                lines.append(f"BOLL 上={last['upper']:.2f} 中={last['middle']:.2f} 下={last['lower']:.2f}")

        if not lines:
            lines.append("暂无显著信号")

        self.signal_text.setText("<br>".join(lines))
        self.signal_text.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 11px; background: transparent;")


class AIPanel(QWidget):
    """DSA AI 观点面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_SM, SPACING_MD, SPACING_SM)
        layout.setSpacing(4)

        title = QLabel("DSA AI 观点")
        title.setProperty("cssClass", "card-title")
        layout.addWidget(title)

        self.ai_text = QLabel("运行 DSA 桌面版并分析后显示")
        self.ai_text.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;")
        self.ai_text.setWordWrap(True)
        layout.addWidget(self.ai_text)

        layout.addStretch()

    def update_for_symbol(self, symbol: str):
        if DSA_READER is None or not DSA_READER.available():
            self.ai_text.setText("DSA 未连接")
            return

        try:
            views = DSA_READER.get_views(symbol=symbol, limit=5)
        except Exception:
            self.ai_text.setText("获取 DSA 观点失败")
            return

        if not views:
            self.ai_text.setText("该股票暂无 AI 观点")
            return

        lines = []
        for v in views[:5]:
            action = v.get("action", "?")
            score = v.get("score", 0)
            reason = v.get("reason", "")[:60]
            action_color = UP_COLOR if action in ("buy", "add") else DOWN_COLOR if action in ("sell", "reduce") else TEXT_PRIMARY
            lines.append(f"<span style='color:{action_color};font-weight:600'>{action}</span> "
                         f"(score:{score:.0f}) {reason}")
        self.ai_text.setText("<br>".join(lines))
        self.ai_text.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 11px; background: transparent;")


class DSATrendPanel(QWidget):
    """DSA 综合趋势分析面板（内嵌轻量引擎，不依赖外部 DSA 项目）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_SM, SPACING_MD, SPACING_SM)
        layout.setSpacing(4)

        title = QLabel("DSA 趋势分析")
        title.setProperty("cssClass", "card-title")
        layout.addWidget(title)

        self.trend_text = QLabel("请选择股票")
        self.trend_text.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;")
        self.trend_text.setWordWrap(True)
        layout.addWidget(self.trend_text)

        layout.addStretch()

    def analyze(self, kline_data: list, indicators: dict, info: dict):
        """
        综合技术分析：趋势 + MACD + RSI + 量价 + 评分
        参考 src/stock_analyzer.py 的分析逻辑
        """
        import numpy as np

        if not kline_data or not indicators:
            self.trend_text.setText("数据不足，无法分析")
            return

        closes = [k["close"] for k in kline_data]
        volumes = [k["volume"] for k in kline_data]
        current_price = closes[-1] if closes else 0
        score = 50  # 起始中性分
        signals = []
        risks = []

        # ── 1. 均线趋势分析 ──
        ma_status = ""
        if "mas" in indicators:
            ma = indicators["mas"]
            ma5_vals = [m["value"] for m in ma.get("ma5", [])] if "ma5" in ma else []
            ma10_vals = [m["value"] for m in ma.get("ma10", [])] if "ma10" in ma else []
            ma20_vals = [m["value"] for m in ma.get("ma20", [])] if "ma20" in ma else []
            ma60_vals = [m["value"] for m in ma.get("ma60", [])] if "ma60" in ma else []

            if ma5_vals and ma10_vals and ma20_vals and ma60_vals:
                m5, m10, m20, m60 = ma5_vals[-1], ma10_vals[-1], ma20_vals[-1], ma60_vals[-1]

                if m5 > m10 > m20 > m60 and current_price > m5:
                    ma_status = "强势多头排列"
                    score += 20
                    signals.append("均线多头排列，趋势强劲")
                elif m5 > m10 > m20:
                    ma_status = "多头排列"
                    score += 12
                    signals.append("均线多头排列")
                elif m5 < m10 < m20 < m60 and current_price < m5:
                    ma_status = "强势空头排列"
                    score -= 20
                    risks.append("均线空头排列，趋势偏弱")
                elif m5 < m10 < m20:
                    ma_status = "空头排列"
                    score -= 12
                    risks.append("均线空头排列")
                elif abs(m5 - m20) / m20 < 0.02:
                    ma_status = "均线粘合·盘整"
                    score += 0
                    signals.append("均线粘合，等待方向选择")
                else:
                    ma_status = "均线交叉·震荡"
                    score += 3

                # 乖离率
                if m20 > 0:
                    bias = (current_price - m20) / m20 * 100
                    if bias > 15:
                        risks.append(f"偏离20日均线 {bias:.1f}%，高位风险")
                        score -= 8
                    elif bias < -15:
                        signals.append(f"低于20日均线 {abs(bias):.1f}%，超跌反弹机会")
                        score += 5

        # ── 2. MACD 分析 ──
        macd_status = ""
        if "macd" in indicators and indicators["macd"]:
            macd_list = indicators["macd"]
            last = macd_list[-1]
            prev = macd_list[-2] if len(macd_list) > 1 else None
            dif, dea, hist = last["macd"], last["signal"], last["histogram"]

            if dif > 0 and hist > 0:
                macd_status = "MACD 多头"
                score += 5
            elif dif < 0 and hist < 0:
                macd_status = "MACD 空头"
                score -= 5
            elif dif > dea and prev and prev["macd"] <= prev["signal"]:
                macd_status = "MACD 金叉 ↑"
                score += 8
                signals.append("MACD 金叉，看涨信号")
            elif dif < dea and prev and prev["macd"] >= prev["signal"]:
                macd_status = "MACD 死叉 ↓"
                score -= 8
                risks.append("MACD 死叉，看跌信号")
            else:
                macd_status = f"MACD DIF={dif:.2f}"

        # ── 3. RSI 分析 ──
        rsi_status = ""
        if "rsi" in indicators and indicators["rsi"]:
            rsi_list = indicators["rsi"]
            rsi_val = rsi_list[-1]["value"]
            if rsi_val > 80:
                rsi_status = f"RSI={rsi_val:.0f} 严重超买"
                score -= 12
                risks.append("RSI 严重超买，回调风险大")
            elif rsi_val > 70:
                rsi_status = f"RSI={rsi_val:.0f} 超买"
                score -= 5
                risks.append("RSI 超买区域")
            elif rsi_val < 20:
                rsi_status = f"RSI={rsi_val:.0f} 严重超卖"
                score += 12
                signals.append("RSI 严重超卖，反弹机会")
            elif rsi_val < 30:
                rsi_status = f"RSI={rsi_val:.0f} 超卖"
                score += 5
                signals.append("RSI 超卖区域")
            else:
                rsi_status = f"RSI={rsi_val:.0f} 中性"

        # ── 4. 量价分析 ──
        if len(volumes) >= 20:
            avg_vol_20 = np.mean(volumes[-20:])
            latest_vol = volumes[-1]
            vol_ratio = latest_vol / avg_vol_20 if avg_vol_20 > 0 else 1

            price_chg = (closes[-1] - closes[-2]) / closes[-2] if len(closes) >= 2 else 0

            if vol_ratio > 1.5 and price_chg > 0.02:
                signals.append(f"放量上涨（量比 {vol_ratio:.1f}x）")
                score += 8
            elif vol_ratio > 1.5 and price_chg < -0.02:
                risks.append(f"放量下跌（量比 {vol_ratio:.1f}x）")
                score -= 8
            elif vol_ratio < 0.5:
                signals.append(f"缩量（量比 {vol_ratio:.1f}x），变盘前兆")
                score += 2

        # ── 5. 布林带位置 ──
        if "boll" in indicators and indicators["boll"]:
            boll_list = indicators["boll"]
            last_boll = boll_list[-1]
            upper, middle, lower = last_boll["upper"], last_boll["middle"], last_boll["lower"]
            boll_width = (upper - lower) / middle if middle > 0 else 0

            if current_price >= upper * 0.98:
                risks.append("价格触及布林上轨，压力位")
                score -= 3
            elif current_price <= lower * 1.02:
                signals.append("价格触及布林下轨，支撑位")
                score += 3
            if boll_width < 0.05:
                signals.append("布林带收窄，即将变盘")
                score += 2

        # ── 6. 综合评分与建议 ──
        score = max(0, min(100, score))
        if score >= 75:
            signal = "强烈买入"
            signal_color = UP_COLOR
            position = "可积极建仓/加仓"
        elif score >= 60:
            signal = "买入"
            signal_color = UP_COLOR
            position = "可适量建仓"
        elif score >= 45:
            signal = "持有/观望"
            signal_color = YELLOW
            position = "持有观望，等待信号"
        elif score >= 30:
            signal = "卖出/减仓"
            signal_color = DOWN_COLOR
            position = "建议减仓或清仓"
        else:
            signal = "强烈卖出"
            signal_color = DOWN_COLOR
            position = "建议清仓回避"

        # ── 渲染 ──
        lines = []
        lines.append(f"<span style='font-size:14px;font-weight:700;color:{signal_color}'>◆ {signal}</span> "
                     f"<span style='color:{TEXT_SECONDARY}'>综合评分 {score:.0f}/100</span>")
        lines.append("")

        if ma_status:
            lines.append(f"趋势：{ma_status}")
        if macd_status:
            lines.append(f"MACD：{macd_status}")
        if rsi_status:
            lines.append(f"RSI：{rsi_status}")
        if position:
            lines.append(f"建议：{position}")
        lines.append("")

        if signals:
            lines.append(f"<span style='color:{UP_COLOR}'>▲ 做多信号：</span>")
            for s in signals:
                lines.append(f"  · {s}")
        if risks:
            lines.append(f"<span style='color:{DOWN_COLOR}'>▼ 风险提示：</span>")
            for r in risks:
                lines.append(f"  · {r}")

        self.trend_text.setText("<br>".join(lines))
        self.trend_text.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 11px; background: transparent;")


class RightPanel(QWidget):
    """右栏：行情报价 + 策略信号 + DSA AI + 快速回测"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 可滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border: none; background: {BG_PRIMARY}; }}")

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(SPACING_SM)

        self.quote_panel = QuotePanel()
        content_layout.addWidget(self.quote_panel)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet(f"color: {BORDER};")
        content_layout.addWidget(sep1)

        self.signal_panel = SignalPanel()
        content_layout.addWidget(self.signal_panel)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {BORDER};")
        content_layout.addWidget(sep2)

        self.ai_panel = AIPanel()
        content_layout.addWidget(self.ai_panel)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet(f"color: {BORDER};")
        content_layout.addWidget(sep3)

        self.dsa_trend_panel = DSATrendPanel()
        content_layout.addWidget(self.dsa_trend_panel)

        content_layout.addStretch()

        # 快速回测按钮
        self.quick_bt_btn = QPushButton("快速回测当前股票")
        self.quick_bt_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT}; border: 1px solid {ACCENT_HOVER};
                border-radius: {RADIUS_MD}px; color: #ffffff;
                font-weight: 600; padding: 8px 16px; min-height: 36px;
            }}
            QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
            QPushButton:pressed {{ background-color: #c0392b; }}
        """)
        content_layout.addWidget(self.quick_bt_btn)

        scroll.setWidget(content)
        layout.addWidget(scroll)

    def update_for_symbol(self, symbol: str):
        try:
            info = SERVICE.get_info(symbol) if SERVICE else None
            self.quote_panel.update_info(info)
        except Exception:
            pass
        try:
            indicators = SERVICE.get_indicators(symbol) if SERVICE else None
            self.signal_panel.update_signals(indicators)
        except Exception:
            pass
        self.ai_panel.update_for_symbol(symbol)

        # DSA 趋势分析
        try:
            kline = SERVICE.get_kline(symbol, limit=120) if SERVICE else None
            indicators = SERVICE.get_indicators(symbol) if SERVICE else None
            info = SERVICE.get_info(symbol) if SERVICE else None
            self.dsa_trend_panel.analyze(kline, indicators, info)
        except Exception:
            pass


# ===========================================================================
# 底部面板
# ===========================================================================

class BottomPanel(QWidget):
    """底部多标签面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.setMaximumHeight(220)

        # 标签 1：交易记录
        self.trade_log = QTextEdit()
        self.trade_log.setReadOnly(True)
        self.trade_log.setPlaceholderText("运行回测后将在此显示交易记录...")
        self.tabs.addTab(self.trade_log, "交易记录")

        # 标签 2：资金曲线
        self.equity_log = QTextEdit()
        self.equity_log.setReadOnly(True)
        self.equity_log.setPlaceholderText("回测资金曲线将在此显示...")
        self.tabs.addTab(self.equity_log, "资金曲线")

        # 标签 3：AI 模拟盘
        self.ai_paper_log = QTextEdit()
        self.ai_paper_log.setReadOnly(True)
        self.ai_paper_log.setPlaceholderText("AI 模拟盘状态将在此显示...")
        self.tabs.addTab(self.ai_paper_log, "AI 模拟盘")

        # 标签 4：系统日志
        self.sys_log = QTextEdit()
        self.sys_log.setReadOnly(True)
        self.tabs.addTab(self.sys_log, "系统日志")

        layout.addWidget(self.tabs)

    def log(self, msg: str):
        self.sys_log.append(msg)

    def show_backtest_result(self, result: dict):
        """在交易记录和资金曲线标签显示回测结果。"""
        if not result:
            return
        summary = result.get("summary", {})
        trades = result.get("trades", [])

        # 交易记录
        lines = []
        lines.append(f"策略: {summary.get('strategy', '?')}  |  "
                      f"总收益: {summary.get('total_return_pct', 0):.2f}%  |  "
                      f"胜率: {summary.get('win_rate_pct', 0):.1f}%  |  "
                      f"交易次数: {summary.get('trade_count', 0)}")
        lines.append("─" * 60)
        for t in trades[:50]:
            lines.append(f"{t.get('date','?')}  {t.get('action','?'):4s}  "
                         f"价格={t.get('price',0):.2f}  数量={t.get('shares',0)}  "
                         f"盈亏={t.get('pnl',0):.2f}  {t.get('reason','')}")
        self.trade_log.setHtml("<br>".join(lines))

        # 资金曲线
        equity = result.get("equity_curve", [])
        if equity:
            eq_lines = ["日期       资金", "─" * 30]
            for pt in equity[:100]:
                eq_lines.append(f"{pt.get('date','?')}  {pt.get('equity',0):.2f}")
            self.equity_log.setText("\n".join(eq_lines))

    def refresh_ai_paper(self):
        """刷新 AI 模拟盘状态。"""
        if AI_PAPER is None:
            self.ai_paper_log.setText("AI 模拟盘未初始化")
            return
        try:
            status = AI_PAPER.status()
            self.ai_paper_log.setText(json.dumps(status, ensure_ascii=False, indent=2))
        except Exception as e:
            self.ai_paper_log.setText(f"获取状态失败: {e}")


# ===========================================================================
# 回测对话框
# ===========================================================================

class BacktestDialog(QDialog):
    """策略回测对话框"""

    def __init__(self, symbol="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("策略回测")
        self.setMinimumSize(440, 400)
        self._setup_ui()
        if symbol:
            self.symbol_input.setText(symbol)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_MD)

        form = QFormLayout()
        form.setSpacing(SPACING_SM)

        self.symbol_input = QLineEdit()
        self.symbol_input.setPlaceholderText("如 000001")
        form.addRow("股票代码", self.symbol_input)

        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems([
            "dual_ma", "trend_5d", "bollinger", "bb_rsi",
            "pullback_20d", "pullback_deep", "breakout",
        ])
        form.addRow("策略", self.strategy_combo)

        self.capital_spin = QDoubleSpinBox()
        self.capital_spin.setRange(1000, 10000000)
        self.capital_spin.setValue(100000)
        self.capital_spin.setDecimals(0)
        form.addRow("初始资金", self.capital_spin)

        self.commission_spin = QDoubleSpinBox()
        self.commission_spin.setRange(0, 0.1)
        self.commission_spin.setValue(0.0003)
        self.commission_spin.setDecimals(4)
        self.commission_spin.setSingleStep(0.0001)
        form.addRow("手续费", self.commission_spin)

        self.stop_loss_spin = QDoubleSpinBox()
        self.stop_loss_spin.setRange(0, 0.5)
        self.stop_loss_spin.setValue(0.05)
        self.stop_loss_spin.setDecimals(2)
        self.stop_loss_spin.setSingleStep(0.01)
        form.addRow("止损 (%)", self.stop_loss_spin)

        self.take_profit_spin = QDoubleSpinBox()
        self.take_profit_spin.setRange(0, 1.0)
        self.take_profit_spin.setValue(0.15)
        self.take_profit_spin.setDecimals(2)
        self.take_profit_spin.setSingleStep(0.01)
        form.addRow("止盈 (%)", self.take_profit_spin)

        layout.addLayout(form)

        # 结果区域
        self.result_area = QTextEdit()
        self.result_area.setReadOnly(True)
        self.result_area.setMaximumHeight(160)
        self.result_area.setPlaceholderText("回测结果将在此显示...")
        layout.addWidget(self.result_area)

        # 按钮
        btn_layout = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        run_btn = QPushButton("运行回测")
        run_btn.setProperty("cssClass", "primary")
        run_btn.clicked.connect(self._run_backtest)
        btn_layout.addWidget(run_btn)
        layout.addLayout(btn_layout)

    def _run_backtest(self):
        symbol = self.symbol_input.text().strip()
        if not symbol:
            QMessageBox.warning(self, "输入错误", "请输入股票代码")
            return

        try:
            result = SERVICE.run_backtest(
                symbol,
                self.strategy_combo.currentText(),
                self.capital_spin.value(),
                self.commission_spin.value(),
                self.stop_loss_spin.value(),
                self.take_profit_spin.value(),
            )
        except Exception as e:
            self.result_area.setText(f"回测失败: {e}")
            return

        summary = result.get("summary", {})
        lines = [
            f"策略: {summary.get('strategy', '?')}",
            f"初始资金: {summary.get('initial_capital', 0):,.0f}",
            f"最终资金: {summary.get('final_capital', 0):,.0f}",
            f"总收益率: {summary.get('total_return_pct', 0):.2f}%",
            f"年化收益: {summary.get('annual_return_pct', 0):.2f}%",
            f"最大回撤: {summary.get('max_drawdown_pct', 0):.2f}%",
            f"夏普比率: {summary.get('sharpe_ratio', 0):.2f}",
            f"胜率: {summary.get('win_rate_pct', 0):.1f}%",
            f"交易次数: {summary.get('trade_count', 0)}",
        ]
        self.result_area.setText("\n".join(lines))

        # 通知主窗口
        parent = self.parent()
        if parent and hasattr(parent, 'on_backtest_done'):
            parent.on_backtest_done(result)


# ===========================================================================
# 主窗口
# ===========================================================================

class MainWindow(QMainWindow):
    """QTrade Desktop 主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("QTrade — A股量化交易终端")
        self.resize(1400, 900)
        self.setMinimumSize(1000, 600)

        self._setup_toolbar()
        self._setup_central()
        self._setup_bottom()
        self._load_data()

        # 定时刷新
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._auto_refresh)
        self._refresh_timer.start(5000)

        # 时钟
        self._clock_timer = QTimer()
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

        self._log("QTrade Desktop 已启动")

    def _setup_toolbar(self):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Logo
        logo = QLabel("QTrade")
        toolbar.addWidget(logo)

        toolbar.addSeparator()

        # 搜索
        self.toolbar_search = QLineEdit()
        self.toolbar_search.setPlaceholderText("搜索股票代码...")
        self.toolbar_search.returnPressed.connect(self._on_toolbar_search)
        toolbar.addWidget(self.toolbar_search)

        # 刷新
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._manual_refresh)
        toolbar.addWidget(refresh_btn)

        # 回测
        bt_btn = QPushButton("回测")
        bt_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT}; border: 1px solid {ACCENT_HOVER};
                border-radius: {RADIUS_MD}px; color: #ffffff;
                font-weight: 600; padding: 6px 16px;
            }}
            QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
            QPushButton:pressed {{ background-color: #c0392b; }}
        """)
        bt_btn.clicked.connect(self._open_backtest)
        toolbar.addWidget(bt_btn)

        # 居中
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        # 时钟
        self.clock_label = QLabel("--")
        self.clock_label.setStyleSheet("font-size: 12px; background: transparent; padding-right: 8px;")
        toolbar.addWidget(self.clock_label)

    def _setup_central(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 三栏水平分隔
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)

        # 左栏：股票列表
        self.stock_list = StockListWidget()
        self.stock_list.setMinimumWidth(200)
        self.stock_list.setMaximumWidth(320)
        self.stock_list.stock_selected.connect(self._on_stock_selected)
        splitter.addWidget(self.stock_list)

        # 中间：图表
        self.chart_widget = ChartWidget()
        splitter.addWidget(self.chart_widget)

        # 右栏：信息面板
        self.right_panel = RightPanel()
        self.right_panel.setMinimumWidth(260)
        self.right_panel.setMaximumWidth(340)
        self.right_panel.quick_bt_btn.clicked.connect(self._quick_backtest)
        splitter.addWidget(self.right_panel)

        # 比例
        splitter.setSizes([260, 800, 280])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)

        layout.addWidget(splitter)

    def _setup_bottom(self):
        self.bottom_panel = BottomPanel()
        self.bottom_panel.setMinimumHeight(60)
        # 底部区域作为中央 widget 的一部分
        central = self.centralWidget()
        central.layout().addWidget(self.bottom_panel)

    def _load_data(self):
        """加载股票列表等初始数据。"""
        pass  # StockListWidget 在构造时自行加载

    # ── 槽函数 ──

    def _on_stock_selected(self, symbol: str):
        self._log(f"选中: {symbol}")
        self.chart_widget.load_symbol(symbol)
        self.right_panel.update_for_symbol(symbol)
        # 添加到最近浏览
        try:
            info = SERVICE.get_info(symbol)
            name = info.get("name", "") if info else ""
            self.stock_list.add_recent(symbol, name)
        except Exception:
            self.stock_list.add_recent(symbol)

    def _on_toolbar_search(self):
        text = self.toolbar_search.text().strip()
        if len(text) == 6 and text.isdigit():
            self._on_stock_selected(text)

    def _manual_refresh(self):
        self._log("手动刷新")
        if self.chart_widget._current_symbol:
            self.chart_widget.load_symbol(self.chart_widget._current_symbol)

    def _auto_refresh(self):
        if self.chart_widget._current_symbol:
            try:
                self.chart_widget.load_symbol(self.chart_widget._current_symbol)
                self.right_panel.update_for_symbol(self.chart_widget._current_symbol)
            except Exception:
                pass

    def _open_backtest(self):
        sym = self.chart_widget._current_symbol or ""
        dlg = BacktestDialog(sym, self)
        dlg.finished.connect(lambda: self.on_backtest_done(None))
        dlg.exec()

    def _quick_backtest(self):
        sym = self.chart_widget._current_symbol
        if not sym:
            QMessageBox.information(self, "提示", "请先选择一只股票")
            return
        dlg = BacktestDialog(sym, self)
        dlg.exec()

    def on_backtest_done(self, result):
        if result:
            self.bottom_panel.show_backtest_result(result)
            self.bottom_panel.tabs.setCurrentIndex(0)

    def _update_clock(self):
        from datetime import datetime
        self.clock_label.setText(datetime.now().strftime("%H:%M:%S"))

    def _log(self, msg: str):
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        self.bottom_panel.log(f"[{ts}] {msg}")


# ===========================================================================
# 入口
# ===========================================================================

def main():
    global SERVICE, DSA_READER, AI_PAPER

    import argparse
    parser = argparse.ArgumentParser(description="QTrade Desktop — PySide6 原生桌面版")
    parser.add_argument("--data-dir", default=None, help="股票数据 CSV 缓存目录")
    parser.add_argument("--csv-only", action="store_true", help="只用本地 CSV，不连实时接口")
    args = parser.parse_args()

    data_dir = find_data_dir(args.data_dir)
    live = not args.csv_only

    SERVICE = DataService(data_dir, live=live)
    symbols = SERVICE.scan()

    DSA_READER = DsaSignalReader()
    AI_PAPER = AiPaperTrader()

    if DSA_READER.available():
        print(f"DSA 集成: 已连接 ({DSA_READER.db_path})")
    else:
        print("DSA 集成: 未找到数据库")

    if live and TencentLiveSource.available():
        print(f"数据模式: 实时（腾讯接口），股票池 {len(symbols)} 只")
    else:
        SERVICE.live = False
        print(f"数据模式: 本地 CSV，股票池 {len(symbols)} 只")

    # Qt 应用
    app = QApplication(sys.argv)
    app.setStyleSheet(dark_stylesheet())

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # pythonw.exe 下看不到控制台输出，用消息框显示错误
        import traceback
        tb = traceback.format_exc()
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication([])
            QMessageBox.critical(None, "QTrade 启动失败",
                f"错误: {e}\n\n{tb[-500:]}")
        except Exception:
            # Qt 都起不来，写文件
            with open("qtrade_error.log", "w", encoding="utf-8") as f:
                f.write(tb)
            print(tb)
        sys.exit(1)
