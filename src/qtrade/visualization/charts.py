"""
Core visualization functions for trading results.

Provides matplotlib and plotly-based charts for:
- Equity curves and drawdowns
- Benchmark comparisons
- Signal overlays
- Return decomposition
- Position and sector exposure
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from typing import Optional, Dict, List, Union
from pathlib import Path
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px

# Set style
plt.style.use('seaborn-v0_8-darkgrid')


def plot_equity_curve(
    equity: pd.Series,
    benchmark: Optional[pd.Series] = None,
    title: str = "Strategy Equity Curve",
    save_path: Optional[str] = None,
    interactive: bool = True,
) -> Union[go.Figure, plt.Figure]:
    """
    Plot equity curve with optional benchmark.

    Args:
        equity: Strategy equity values over time
        benchmark: Optional benchmark equity for comparison
        title: Chart title
        save_path: Path to save figure (HTML for interactive, PNG for static)
        interactive: Use plotly (True) or matplotlib (False)

    Returns:
        Plotly or matplotlib figure
    """
    if interactive:
        fig = go.Figure()

        # Strategy equity
        fig.add_trace(go.Scatter(
            x=equity.index,
            y=equity.values,
            mode='lines',
            name='Strategy',
            line=dict(color='#1f77b4', width=2),
        ))

        # Benchmark
        if benchmark is not None:
            fig.add_trace(go.Scatter(
                x=benchmark.index,
                y=benchmark.values,
                mode='lines',
                name='Benchmark',
                line=dict(color='#ff7f0e', width=2, dash='dash'),
            ))

        fig.update_layout(
            title=title,
            xaxis_title='Date',
            yaxis_title='Equity Value',
            hovermode='x unified',
            template='plotly_white',
            height=500,
        )

        if save_path:
            fig.write_html(save_path)

        return fig

    else:
        fig, ax = plt.subplots(figsize=(12, 6))

        ax.plot(equity.index, equity.values, label='Strategy', linewidth=2)

        if benchmark is not None:
            ax.plot(benchmark.index, benchmark.values,
                   label='Benchmark', linewidth=2, linestyle='--', alpha=0.7)

        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_xlabel('Date')
        ax.set_ylabel('Equity Value')
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        plt.xticks(rotation=45)
        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=150, bbox_inches='tight')

        return fig


def plot_drawdown(
    equity: pd.Series,
    title: str = "Drawdown Analysis",
    save_path: Optional[str] = None,
    interactive: bool = True,
) -> Union[go.Figure, plt.Figure]:
    """
    Plot drawdown chart.

    Args:
        equity: Equity values over time
        title: Chart title
        save_path: Path to save figure
        interactive: Use plotly (True) or matplotlib (False)

    Returns:
        Plotly or matplotlib figure
    """
    # Calculate drawdown
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax * 100  # Percentage

    if interactive:
        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=drawdown.index,
            y=drawdown.values,
            mode='lines',
            name='Drawdown',
            line=dict(color='#d62728', width=1),
            fill='tozeroy',
            fillcolor='rgba(214, 39, 40, 0.3)',
        ))

        fig.update_layout(
            title=title,
            xaxis_title='Date',
            yaxis_title='Drawdown (%)',
            hovermode='x unified',
            template='plotly_white',
            height=400,
        )

        if save_path:
            fig.write_html(save_path)

        return fig

    else:
        fig, ax = plt.subplots(figsize=(12, 5))

        ax.fill_between(drawdown.index, 0, drawdown.values,
                       color='#d62728', alpha=0.3, label='Drawdown')
        ax.plot(drawdown.index, drawdown.values, color='#d62728', linewidth=1)

        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_xlabel('Date')
        ax.set_ylabel('Drawdown (%)')
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        plt.xticks(rotation=45)
        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=150, bbox_inches='tight')

        return fig


def plot_benchmark_comparison(
    equity: pd.Series,
    benchmark: pd.Series,
    title: str = "Strategy vs Benchmark",
    save_path: Optional[str] = None,
    interactive: bool = True,
) -> Union[go.Figure, plt.Figure]:
    """
    Plot normalized comparison between strategy and benchmark.

    Args:
        equity: Strategy equity values
        benchmark: Benchmark equity values
        title: Chart title
        save_path: Path to save figure
        interactive: Use plotly (True) or matplotlib (False)

    Returns:
        Plotly or matplotlib figure
    """
    # Normalize to 100
    equity_norm = equity / equity.iloc[0] * 100
    benchmark_norm = benchmark / benchmark.iloc[0] * 100

    if interactive:
        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=equity_norm.index,
            y=equity_norm.values,
            mode='lines',
            name='Strategy',
            line=dict(color='#1f77b4', width=2),
        ))

        fig.add_trace(go.Scatter(
            x=benchmark_norm.index,
            y=benchmark_norm.values,
            mode='lines',
            name='Benchmark',
            line=dict(color='#ff7f0e', width=2),
        ))

        fig.add_hline(y=100, line_dash="dash", line_color="gray", opacity=0.5)

        fig.update_layout(
            title=title,
            xaxis_title='Date',
            yaxis_title='Normalized Value (Start = 100)',
            hovermode='x unified',
            template='plotly_white',
            height=500,
        )

        if save_path:
            fig.write_html(save_path)

        return fig

    else:
        fig, ax = plt.subplots(figsize=(12, 6))

        ax.plot(equity_norm.index, equity_norm.values,
               label='Strategy', linewidth=2)
        ax.plot(benchmark_norm.index, benchmark_norm.values,
               label='Benchmark', linewidth=2)
        ax.axhline(y=100, color='gray', linestyle='--', alpha=0.5)

        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_xlabel('Date')
        ax.set_ylabel('Normalized Value (Start = 100)')
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        plt.xticks(rotation=45)
        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=150, bbox_inches='tight')

        return fig


def plot_signal_overlay(
    price: pd.Series,
    signals: pd.DataFrame,
    title: str = "Price with Trading Signals",
    save_path: Optional[str] = None,
    interactive: bool = True,
) -> Union[go.Figure, plt.Figure]:
    """
    Plot price chart with buy/sell signals overlay.

    Args:
        price: Price series (close prices)
        signals: DataFrame with 'signal_action' column (1=buy, -1=sell, 0=hold)
        title: Chart title
        save_path: Path to save figure
        interactive: Use plotly (True) or matplotlib (False)

    Returns:
        Plotly or matplotlib figure
    """
    buy_signals = signals[signals['signal_action'] == 1]
    sell_signals = signals[signals['signal_action'] == -1]

    if interactive:
        fig = go.Figure()

        # Price line
        fig.add_trace(go.Scatter(
            x=price.index,
            y=price.values,
            mode='lines',
            name='Price',
            line=dict(color='black', width=1),
        ))

        # Buy signals
        if not buy_signals.empty:
            fig.add_trace(go.Scatter(
                x=buy_signals.index,
                y=price.loc[buy_signals.index],
                mode='markers',
                name='Buy',
                marker=dict(symbol='triangle-up', size=12, color='green'),
            ))

        # Sell signals
        if not sell_signals.empty:
            fig.add_trace(go.Scatter(
                x=sell_signals.index,
                y=price.loc[sell_signals.index],
                mode='markers',
                name='Sell',
                marker=dict(symbol='triangle-down', size=12, color='red'),
            ))

        fig.update_layout(
            title=title,
            xaxis_title='Date',
            yaxis_title='Price',
            hovermode='x unified',
            template='plotly_white',
            height=500,
        )

        if save_path:
            fig.write_html(save_path)

        return fig

    else:
        fig, ax = plt.subplots(figsize=(14, 7))

        ax.plot(price.index, price.values, color='black', linewidth=1, label='Price')

        # Buy signals
        if not buy_signals.empty:
            ax.scatter(buy_signals.index, price.loc[buy_signals.index],
                      color='green', marker='^', s=100, label='Buy', zorder=5)

        # Sell signals
        if not sell_signals.empty:
            ax.scatter(sell_signals.index, price.loc[sell_signals.index],
                      color='red', marker='v', s=100, label='Sell', zorder=5)

        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_xlabel('Date')
        ax.set_ylabel('Price')
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        plt.xticks(rotation=45)
        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=150, bbox_inches='tight')

        return fig


def plot_annual_returns(
    equity: pd.Series,
    title: str = "Annual Returns",
    save_path: Optional[str] = None,
    interactive: bool = True,
) -> Union[go.Figure, plt.Figure]:
    """
    Plot annual returns bar chart.

    Args:
        equity: Equity values over time
        title: Chart title
        save_path: Path to save figure
        interactive: Use plotly (True) or matplotlib (False)

    Returns:
        Plotly or matplotlib figure
    """
    # Calculate annual returns
    equity_yearly = equity.resample('YE').last()
    returns = equity_yearly.pct_change() * 100
    returns = returns.dropna()

    # Extract years
    years = [d.year for d in returns.index]
    values = returns.values

    if interactive:
        colors = ['green' if v > 0 else 'red' for v in values]

        fig = go.Figure(data=[
            go.Bar(
                x=years,
                y=values,
                marker_color=colors,
                text=[f'{v:.1f}%' for v in values],
                textposition='auto',
            )
        ])

        fig.update_layout(
            title=title,
            xaxis_title='Year',
            yaxis_title='Return (%)',
            template='plotly_white',
            height=400,
        )

        if save_path:
            fig.write_html(save_path)

        return fig

    else:
        fig, ax = plt.subplots(figsize=(10, 6))

        colors = ['green' if v > 0 else 'red' for v in values]
        bars = ax.bar(years, values, color=colors, alpha=0.7, edgecolor='black')

        # Add value labels
        for bar, val in zip(bars, values):
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height,
                   f'{val:.1f}%',
                   ha='center', va='bottom' if height > 0 else 'top')

        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_xlabel('Year')
        ax.set_ylabel('Return (%)')
        ax.axhline(y=0, color='black', linewidth=1)
        ax.grid(True, alpha=0.3, axis='y')
        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=150, bbox_inches='tight')

        return fig


def plot_position_exposure(
    positions: pd.DataFrame,
    title: str = "Position Exposure Over Time",
    save_path: Optional[str] = None,
    interactive: bool = True,
) -> Union[go.Figure, plt.Figure]:
    """
    Plot position sizes over time.

    Args:
        positions: DataFrame with symbols as columns and position values
        title: Chart title
        save_path: Path to save figure
        interactive: Use plotly (True) or matplotlib (False)

    Returns:
        Plotly or matplotlib figure
    """
    if interactive:
        fig = go.Figure()

        for symbol in positions.columns:
            fig.add_trace(go.Scatter(
                x=positions.index,
                y=positions[symbol].values,
                mode='lines',
                name=symbol,
                stackgroup='one',
            ))

        fig.update_layout(
            title=title,
            xaxis_title='Date',
            yaxis_title='Position Value',
            hovermode='x unified',
            template='plotly_white',
            height=500,
        )

        if save_path:
            fig.write_html(save_path)

        return fig

    else:
        fig, ax = plt.subplots(figsize=(12, 6))

        ax.stackplot(positions.index,
                    *[positions[col].values for col in positions.columns],
                    labels=positions.columns, alpha=0.7)

        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_xlabel('Date')
        ax.set_ylabel('Position Value')
        ax.legend(loc='upper left')
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        plt.xticks(rotation=45)
        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=150, bbox_inches='tight')

        return fig


def plot_sector_exposure(
    sector_exposure: pd.DataFrame,
    title: str = "Sector Exposure Over Time",
    save_path: Optional[str] = None,
    interactive: bool = True,
) -> Union[go.Figure, plt.Figure]:
    """
    Plot sector allocation over time.

    Args:
        sector_exposure: DataFrame with sectors as columns and exposure percentages
        title: Chart title
        save_path: Path to save figure
        interactive: Use plotly (True) or matplotlib (False)

    Returns:
        Plotly or matplotlib figure
    """
    if interactive:
        fig = go.Figure()

        for sector in sector_exposure.columns:
            fig.add_trace(go.Scatter(
                x=sector_exposure.index,
                y=sector_exposure[sector].values,
                mode='lines',
                name=sector,
                stackgroup='one',
            ))

        fig.update_layout(
            title=title,
            xaxis_title='Date',
            yaxis_title='Exposure (%)',
            hovermode='x unified',
            template='plotly_white',
            height=500,
        )

        if save_path:
            fig.write_html(save_path)

        return fig

    else:
        fig, ax = plt.subplots(figsize=(12, 6))

        ax.stackplot(sector_exposure.index,
                    *[sector_exposure[col].values for col in sector_exposure.columns],
                    labels=sector_exposure.columns, alpha=0.7)

        ax.set_title(title, fontsize=14, fontweight='bold')
        ax.set_xlabel('Date')
        ax.set_ylabel('Exposure (%)')
        ax.legend(loc='upper left', bbox_to_anchor=(1.05, 1))
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        plt.xticks(rotation=45)
        plt.tight_layout()

        if save_path:
            fig.savefig(save_path, dpi=150, bbox_inches='tight')

        return fig
