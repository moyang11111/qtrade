"""
Visualizations and reports for quantitative trading results.

This module provides comprehensive visualization tools including:
- Equity curves and drawdown analysis
- Benchmark comparisons
- Signal overlays on price charts
- Annual return decomposition
- Position and sector exposure analysis
- Interactive dashboards
"""

from .charts import (
    plot_equity_curve,
    plot_drawdown,
    plot_benchmark_comparison,
    plot_signal_overlay,
    plot_annual_returns,
    plot_position_exposure,
    plot_sector_exposure,
)
from .report import generate_report

# Dashboard requires streamlit (optional dependency)
try:
    from .dashboard import create_dashboard
    _DASHBOARD_AVAILABLE = True
except ImportError:
    _DASHBOARD_AVAILABLE = False
    create_dashboard = None

__all__ = [
    'plot_equity_curve',
    'plot_drawdown',
    'plot_benchmark_comparison',
    'plot_signal_overlay',
    'plot_annual_returns',
    'plot_position_exposure',
    'plot_sector_exposure',
    'generate_report',
]

if _DASHBOARD_AVAILABLE:
    __all__.append('create_dashboard')
