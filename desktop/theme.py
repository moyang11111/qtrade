"""
QTrade Desktop — 暗黑主题定义
参考 UI/UX Pro Max 设计规范：
  - 语义颜色 token（非硬编码 hex）
  - 4.5:1 最低对比度
  - 4pt 基准间距系统
"""

# ── 颜色 Token ──────────────────────────────────────────
# 背景层级
BG_PRIMARY = "#0d1117"      # 主背景（最底层）
BG_SECONDARY = "#161b22"    # 次级背景（卡片/面板）
BG_TERTIARY = "#21262d"     # 三级背景（输入框/列表项悬停）
BG_ELEVATED = "#1c2128"     # 浮层背景
BORDER = "#30363d"          # 边框

# 文字层级
TEXT_PRIMARY = "#e6edf3"    # 主文字（对比度 ~14:1 on #0d1117）
TEXT_SECONDARY = "#8b949e"  # 次级文字（对比度 ~5.5:1）
TEXT_MUTED = "#6e7681"      # 弱化文字

# 语义色（A 股：红涨绿跌）
ACCENT = "#e94560"          # 品牌强调色
ACCENT_HOVER = "#ff6b81"
GREEN = "#3fb950"           # 上涨（A 股红涨，但用绿做辅助强调）
RED = "#f85149"             # 下跌（A 股绿跌，但用红做辅助强调）
UP_COLOR = "#f85149"        # A 股惯例：红色=上涨
DOWN_COLOR = "#2ea043"      # A 股惯例：绿色=下跌
YELLOW = "#d2991d"          # 警告/中性
BLUE = "#58a6ff"            # 信息
PURPLE = "#bc8cff"          # AI/智能

# ── 字体 ──────────────────────────────────────────────
FONT_UI = "Segoe UI, Microsoft YaHei, PingFang SC, sans-serif"
FONT_MONO = "Cascadia Code, Consolas, Fira Code, monospace"
FONT_SIZE_SM = 11
FONT_SIZE = 12
FONT_SIZE_LG = 14
FONT_SIZE_XL = 16
FONT_SIZE_XXL = 20

# ── 间距系统（4pt 基准）────────────────────────────────
SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24
SPACING_XXL = 32

# ── 圆角 ───────────────────────────────────────────────
RADIUS_SM = 4
RADIUS_MD = 6
RADIUS_LG = 8


def dark_stylesheet() -> str:
    """生成 Qt 暗黑主题样式表。"""
    return f"""
    /* ── 全局 ── */
    QMainWindow {{
        background-color: {BG_PRIMARY};
    }}
    QWidget {{
        background-color: {BG_PRIMARY};
        color: {TEXT_PRIMARY};
        font-family: "{FONT_UI}";
        font-size: {FONT_SIZE}px;
    }}

    /* ── 工具栏 ── */
    QToolBar {{
        background-color: {BG_SECONDARY};
        border-bottom: 1px solid {BORDER};
        spacing: {SPACING_SM}px;
        padding: 2px {SPACING_MD}px;
        min-height: 40px;
    }}
    QToolBar QLabel {{
        font-size: {FONT_SIZE_XL}px;
        font-weight: 700;
        color: {ACCENT};
        background: transparent;
    }}
    QToolBar QLineEdit {{
        background-color: {BG_TERTIARY};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_MD}px;
        padding: 4px {SPACING_SM}px;
        color: {TEXT_PRIMARY};
        min-width: 160px;
        max-width: 200px;
    }}
    QToolBar QLineEdit:focus {{
        border-color: {ACCENT};
    }}

    /* ── 按钮 ── */
    QPushButton {{
        background-color: {BG_TERTIARY};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_MD}px;
        padding: 4px {SPACING_MD}px;
        color: {TEXT_PRIMARY};
        min-height: 28px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {BORDER};
        border-color: {TEXT_MUTED};
    }}
    QPushButton:pressed {{
        background-color: {BG_PRIMARY};
    }}

    /* ── 主要操作按钮（品牌强调，克制不刺眼）── */
    QPushButton[cssClass="primary"] {{
        background-color: {ACCENT};
        border: 1px solid {ACCENT_HOVER};
        border-radius: {RADIUS_MD}px;
        color: #ffffff;
        font-weight: 600;
        padding: 6px {SPACING_LG}px;
    }}
    QPushButton[cssClass="primary"]:hover {{
        background-color: {ACCENT_HOVER};
    }}
    QPushButton[cssClass="primary"]:pressed {{
        background-color: #c0392b;
    }}

    /* ── 次级强调按钮（描边风格，低调但有存在感）── */
    QPushButton[cssClass="accent"] {{
        background-color: transparent;
        border: 1.5px solid {ACCENT};
        border-radius: {RADIUS_MD}px;
        color: {ACCENT};
        font-weight: 600;
    }}
    QPushButton[cssClass="accent"]:hover {{
        background-color: rgba(233, 69, 96, 0.12);
        border-color: {ACCENT_HOVER};
        color: {ACCENT_HOVER};
    }}
    QPushButton[cssClass="accent"]:pressed {{
        background-color: rgba(233, 69, 96, 0.22);
    }}

    /* ── 信息/分析按钮（蓝紫调）── */
    QPushButton[cssClass="info"] {{
        background-color: transparent;
        border: 1.5px solid {BLUE};
        border-radius: {RADIUS_MD}px;
        color: {BLUE};
        font-weight: 600;
    }}
    QPushButton[cssClass="info"]:hover {{
        background-color: rgba(88, 166, 255, 0.12);
    }}

    /* ── 危险/卖出按钮 ── */
    QPushButton[cssClass="danger"] {{
        background-color: transparent;
        border: 1.5px solid {DOWN_COLOR};
        border-radius: {RADIUS_MD}px;
        color: {DOWN_COLOR};
        font-weight: 600;
    }}
    QPushButton[cssClass="danger"]:hover {{
        background-color: rgba(46, 160, 67, 0.12);
    }}

    /* ── 列表 ── */
    QListWidget {{
        background-color: {BG_PRIMARY};
        border: none;
        outline: none;
    }}
    QListWidget::item {{
        padding: {SPACING_SM}px {SPACING_MD}px;
        border-bottom: 1px solid transparent;
        min-height: 32px;
    }}
    QListWidget::item:hover {{
        background-color: {BG_TERTIARY};
    }}
    QListWidget::item:selected {{
        background-color: {BG_TERTIARY};
        border-left: 3px solid {ACCENT};
        color: {TEXT_PRIMARY};
    }}

    /* ── 分隔条 ── */
    QSplitter::handle {{
        background-color: {BORDER};
        width: 1px;
    }}
    QSplitter::handle:horizontal {{
        width: 1px;
    }}
    QSplitter::handle:vertical {{
        height: 1px;
    }}

    /* ── 标签页 ── */
    QTabWidget::pane {{
        background-color: {BG_SECONDARY};
        border: none;
        border-top: 1px solid {BORDER};
    }}
    QTabBar::tab {{
        background-color: {BG_PRIMARY};
        color: {TEXT_SECONDARY};
        padding: {SPACING_SM}px {SPACING_LG}px;
        border: none;
        border-bottom: 2px solid transparent;
        min-width: 80px;
    }}
    QTabBar::tab:hover {{
        color: {TEXT_PRIMARY};
        background-color: {BG_TERTIARY};
    }}
    QTabBar::tab:selected {{
        color: {ACCENT};
        border-bottom: 2px solid {ACCENT};
        background-color: {BG_SECONDARY};
    }}

    /* ── 滚动条 ── */
    QScrollBar:vertical {{
        background: {BG_PRIMARY};
        width: 8px;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER};
        border-radius: 4px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {TEXT_MUTED};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}

    /* ── 对话框 ── */
    QDialog {{
        background-color: {BG_SECONDARY};
    }}
    QComboBox {{
        background-color: {BG_TERTIARY};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_SM}px;
        padding: 4px {SPACING_SM}px;
        color: {TEXT_PRIMARY};
        min-height: 28px;
    }}
    QComboBox::drop-down {{
        border: none;
    }}
    QComboBox QAbstractItemView {{
        background-color: {BG_TERTIARY};
        border: 1px solid {BORDER};
    }}

    /* ── SpinBox ── */
    QSpinBox, QDoubleSpinBox {{
        background-color: {BG_TERTIARY};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_SM}px;
        padding: 4px;
        color: {TEXT_PRIMARY};
        min-height: 24px;
    }}

    /* ── 文本编辑 ── */
    QTextEdit, QPlainTextEdit {{
        background-color: {BG_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: {RADIUS_SM}px;
        padding: {SPACING_SM}px;
        color: {TEXT_PRIMARY};
        font-family: "{FONT_MONO}";
        font-size: {FONT_SIZE_SM}px;
    }}

    /* ── 标签 ── */
    QLabel[cssClass="section-title"] {{
        font-size: {FONT_SIZE_SM}px;
        font-weight: 600;
        color: {TEXT_SECONDARY};
        text-transform: uppercase;
        letter-spacing: 0.5px;
        padding: {SPACING_MD}px {SPACING_MD}px {SPACING_XS}px;
    }}
    QLabel[cssClass="card-title"] {{
        font-size: {FONT_SIZE}px;
        font-weight: 600;
        color: {TEXT_PRIMARY};
        padding-bottom: {SPACING_SM}px;
        border-bottom: 1px solid {BORDER};
    }}
    """
