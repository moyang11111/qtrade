"""
Interactive dashboard for strategy monitoring and analysis.

Provides Streamlit-based dashboard with:
- Real-time strategy performance
- Interactive charts
- Portfolio analysis
- Trade history
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional, Dict, Any
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .charts import (
    plot_equity_curve,
    plot_drawdown,
    plot_benchmark_comparison,
    plot_signal_overlay,
    plot_annual_returns,
    plot_position_exposure,
    plot_sector_exposure,
)


def create_dashboard(
    equity: pd.Series,
    signals: Optional[pd.DataFrame] = None,
    price: Optional[pd.Series] = None,
    benchmark: Optional[pd.Series] = None,
    positions: Optional[pd.DataFrame] = None,
    sector_exposure: Optional[pd.DataFrame] = None,
    metrics: Optional[Dict[str, Any]] = None,
    strategy_name: str = "Strategy",
):
    """
    Create interactive Streamlit dashboard.

    Args:
        equity: Strategy equity values
        signals: Trading signals DataFrame
        price: Price series
        benchmark: Benchmark equity
        positions: Position DataFrame
        sector_exposure: Sector exposure DataFrame
        metrics: Performance metrics
        strategy_name: Strategy name
    """
    st.set_page_config(
        page_title=f"{strategy_name} Dashboard",
        page_icon="📊",
        layout="wide",
    )

    st.title(f"📊 {strategy_name} Dashboard")

    # Sidebar
    st.sidebar.header("Controls")

    # Date range filter
    min_date = equity.index[0].date()
    max_date = equity.index[-1].date()

    date_range = st.sidebar.date_input(
        "Date Range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )

    if len(date_range) == 2:
        start_date, end_date = date_range
        equity_filtered = equity.loc[start_date:end_date]
    else:
        equity_filtered = equity

    # Calculate metrics if not provided
    if metrics is None:
        metrics = _calculate_dashboard_metrics(equity_filtered, benchmark)

    # Main dashboard
    _render_metrics_section(metrics)

    # Tabs
    tab1, tab2, tab3, tab4 = st.tabs([
        "📈 Performance",
        "📊 Analysis",
        "💼 Portfolio",
        "📋 Details"
    ])

    with tab1:
        _render_performance_tab(equity_filtered, benchmark)

    with tab2:
        _render_analysis_tab(equity_filtered, signals, price)

    with tab3:
        _render_portfolio_tab(positions, sector_exposure)

    with tab4:
        _render_details_tab(equity_filtered, signals, metrics)


def _render_metrics_section(metrics: Dict[str, Any]):
    """Render key metrics cards."""
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "Total Return",
            metrics.get('Total Return', 'N/A'),
            delta=metrics.get('Return Delta', None)
        )

    with col2:
        st.metric(
            "Annual Return",
            metrics.get('Annual Return', 'N/A')
        )

    with col3:
        st.metric(
            "Sharpe Ratio",
            metrics.get('Sharpe Ratio', 'N/A')
        )

    with col4:
        st.metric(
            "Max Drawdown",
            metrics.get('Max Drawdown', 'N/A')
        )


def _render_performance_tab(equity: pd.Series, benchmark: Optional[pd.Series]):
    """Render performance charts tab."""
    st.subheader("Equity Curve")
    equity_chart = plot_equity_curve(
        equity, benchmark=benchmark,
        title="Strategy Equity Curve",
        interactive=True
    )
    st.plotly_chart(equity_chart, use_container_width=True)

    st.subheader("Drawdown Analysis")
    drawdown_chart = plot_drawdown(
        equity,
        title="Drawdown",
        interactive=True
    )
    st.plotly_chart(drawdown_chart, use_container_width=True)

    if benchmark is not None:
        st.subheader("Benchmark Comparison")
        benchmark_chart = plot_benchmark_comparison(
            equity, benchmark,
            title="Strategy vs Benchmark",
            interactive=True
        )
        st.plotly_chart(benchmark_chart, use_container_width=True)


def _render_analysis_tab(
    equity: pd.Series,
    signals: Optional[pd.DataFrame],
    price: Optional[pd.Series]
):
    """Render analysis charts tab."""
    st.subheader("Annual Returns")
    annual_chart = plot_annual_returns(
        equity,
        title="Annual Returns",
        interactive=True
    )
    st.plotly_chart(annual_chart, use_container_width=True)

    if signals is not None and price is not None:
        st.subheader("Signal Overlay")
        signal_chart = plot_signal_overlay(
            price, signals,
            title="Price with Trading Signals",
            interactive=True
        )
        st.plotly_chart(signal_chart, use_container_width=True)

    # Rolling metrics
    st.subheader("Rolling Metrics")
    col1, col2 = st.columns(2)

    with col1:
        window = st.selectbox("Rolling Window", [20, 60, 120, 252], index=1)

    returns = equity.pct_change().dropna()

    with col2:
        metric_type = st.selectbox("Metric", ["Returns", "Volatility", "Sharpe"])

    if metric_type == "Returns":
        rolling_metric = returns.rolling(window).mean() * 252 * 100
        y_label = "Annualized Return (%)"
    elif metric_type == "Volatility":
        rolling_metric = returns.rolling(window).std() * np.sqrt(252) * 100
        y_label = "Annualized Volatility (%)"
    else:
        rolling_returns = returns.rolling(window).mean() * 252
        rolling_vol = returns.rolling(window).std() * np.sqrt(252)
        rolling_metric = (rolling_returns - 0.03) / rolling_vol
        y_label = "Sharpe Ratio"

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=rolling_metric.index,
        y=rolling_metric.values,
        mode='lines',
        name=f'{window}-day Rolling {metric_type}',
    ))
    fig.update_layout(
        title=f'{window}-Day Rolling {metric_type}',
        xaxis_title='Date',
        yaxis_title=y_label,
        hovermode='x unified',
        template='plotly_white',
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_portfolio_tab(
    positions: Optional[pd.DataFrame],
    sector_exposure: Optional[pd.DataFrame]
):
    """Render portfolio analysis tab."""
    if positions is not None:
        st.subheader("Position Exposure")
        position_chart = plot_position_exposure(
            positions,
            title="Position Exposure Over Time",
            interactive=True
        )
        st.plotly_chart(position_chart, use_container_width=True)

        # Current positions
        st.subheader("Current Positions")
        current_positions = positions.iloc[-1].sort_values(ascending=False)
        current_positions = current_positions[current_positions > 0]

        if not current_positions.empty:
            fig = go.Figure(data=[
                go.Bar(
                    x=current_positions.index,
                    y=current_positions.values,
                    marker_color='steelblue',
                )
            ])
            fig.update_layout(
                title="Current Position Values",
                xaxis_title="Symbol",
                yaxis_title="Value",
                template='plotly_white',
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No current positions")

    if sector_exposure is not None:
        st.subheader("Sector Exposure")
        sector_chart = plot_sector_exposure(
            sector_exposure,
            title="Sector Exposure Over Time",
            interactive=True
        )
        st.plotly_chart(sector_chart, use_container_width=True)

        # Current sector allocation
        st.subheader("Current Sector Allocation")
        current_sectors = sector_exposure.iloc[-1]
        current_sectors = current_sectors[current_sectors > 0]

        if not current_sectors.empty:
            fig = go.Figure(data=[
                go.Pie(
                    labels=current_sectors.index,
                    values=current_sectors.values,
                    hole=0.3,
                )
            ])
            fig.update_layout(
                title="Current Sector Allocation",
                template='plotly_white',
            )
            st.plotly_chart(fig, use_container_width=True)

    if positions is None and sector_exposure is None:
        st.info("No portfolio data available")


def _render_details_tab(
    equity: pd.Series,
    signals: Optional[pd.DataFrame],
    metrics: Dict[str, Any]
):
    """Render details tab."""
    st.subheader("Performance Metrics")

    # Display metrics in table
    metrics_df = pd.DataFrame([
        {"Metric": k, "Value": v} for k, v in metrics.items()
    ])
    st.dataframe(metrics_df, use_container_width=True, hide_index=True)

    if signals is not None:
        st.subheader("Trading Signals")

        # Signal statistics
        buy_count = (signals['signal_action'] == 1).sum()
        sell_count = (signals['signal_action'] == -1).sum()
        hold_count = (signals['signal_action'] == 0).sum()

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Buy Signals", buy_count)
        with col2:
            st.metric("Sell Signals", sell_count)
        with col3:
            st.metric("Hold Signals", hold_count)

        # Recent signals
        st.subheader("Recent Signals")
        recent_signals = signals[signals['signal_action'] != 0].tail(20)
        if not recent_signals.empty:
            display_df = recent_signals.copy()
            display_df['Date'] = display_df.index
            display_df = display_df[['Date', 'signal_action', 'signal_strength']]
            display_df.columns = ['Date', 'Action', 'Strength']
            display_df['Action'] = display_df['Action'].map({1: 'BUY', -1: 'SELL'})
            st.dataframe(display_df, use_container_width=True, hide_index=True)
        else:
            st.info("No recent trading signals")

    # Equity data table
    st.subheader("Equity Data")
    equity_df = pd.DataFrame({
        'Date': equity.index,
        'Equity': equity.values,
    })
    st.dataframe(equity_df.tail(50), use_container_width=True, hide_index=True)


def _calculate_dashboard_metrics(
    equity: pd.Series,
    benchmark: Optional[pd.Series] = None
) -> Dict[str, Any]:
    """Calculate metrics for dashboard display."""
    returns = equity.pct_change().dropna()

    # Basic metrics
    total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    annual_return = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100

    # Volatility
    annual_vol = returns.std() * np.sqrt(252) * 100

    # Sharpe ratio
    risk_free = 0.03
    sharpe = (annual_return / 100 - risk_free) / (annual_vol / 100)

    # Max drawdown
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax
    max_drawdown = drawdown.min() * 100

    metrics = {
        'Total Return': f"{total_return:.2f}%",
        'Annual Return': f"{annual_return:.2f}%",
        'Annual Volatility': f"{annual_vol:.2f}%",
        'Sharpe Ratio': f"{sharpe:.2f}",
        'Max Drawdown': f"{max_drawdown:.2f}%",
        'Start Date': equity.index[0].strftime('%Y-%m-%d'),
        'End Date': equity.index[-1].strftime('%Y-%m-%d'),
        'Trading Days': len(equity),
        'Return Delta': f"{total_return:.2f}%",
    }

    return metrics


def run_dashboard_app():
    """
    Run Streamlit dashboard as standalone app.

    Usage:
        streamlit run dashboard.py
    """
    st.set_page_config(page_title="QTrade Dashboard", page_icon="📊", layout="wide")

    st.title("📊 QTrade Strategy Dashboard")

    # File upload
    st.sidebar.header("Data Input")

    equity_file = st.sidebar.file_uploader("Upload Equity CSV", type=['csv'])
    benchmark_file = st.sidebar.file_uploader("Upload Benchmark CSV (optional)", type=['csv'])

    if equity_file is not None:
        equity = pd.read_csv(equity_file, index_col=0, parse_dates=True)
        equity = equity.iloc[:, 0]  # First column

        benchmark = None
        if benchmark_file is not None:
            benchmark = pd.read_csv(benchmark_file, index_col=0, parse_dates=True)
            benchmark = benchmark.iloc[:, 0]

        # Create dashboard
        create_dashboard(
            equity=equity,
            benchmark=benchmark,
            strategy_name="Uploaded Strategy"
        )
    else:
        st.info("👈 Upload equity data CSV to get started")

        # Demo data
        if st.button("Load Demo Data"):
            dates = pd.date_range('2020-01-01', '2024-01-01', freq='D')
            equity = pd.Series(
                np.random.randn(len(dates)).cumsum() * 0.01 + 1,
                index=dates
            ) * 100000

            create_dashboard(
                equity=equity,
                strategy_name="Demo Strategy"
            )


if __name__ == "__main__":
    run_dashboard_app()
