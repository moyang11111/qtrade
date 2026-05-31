"""
Comprehensive report generation for trading strategies.

Generates HTML reports with:
- Performance metrics summary
- Interactive charts
- Trade analysis
- Risk metrics
- Benchmark comparisons
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime
import json

from .charts import (
    plot_equity_curve,
    plot_drawdown,
    plot_benchmark_comparison,
    plot_signal_overlay,
    plot_annual_returns,
)


def generate_report(
    equity: pd.Series,
    signals: Optional[pd.DataFrame] = None,
    price: Optional[pd.Series] = None,
    benchmark: Optional[pd.Series] = None,
    metrics: Optional[Dict[str, Any]] = None,
    output_dir: str = "reports",
    strategy_name: str = "Strategy",
    interactive: bool = True,
) -> str:
    """
    Generate comprehensive HTML report.

    Args:
        equity: Strategy equity values
        signals: Trading signals DataFrame
        price: Price series for signal overlay
        benchmark: Benchmark equity for comparison
        metrics: Performance metrics dictionary
        output_dir: Directory to save report
        strategy_name: Strategy name for title
        interactive: Use interactive charts

    Returns:
        Path to generated HTML report
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = output_path / f"{strategy_name}_report_{timestamp}.html"

    # Generate charts
    charts_html = []

    # 1. Equity curve
    equity_chart = plot_equity_curve(
        equity, benchmark=benchmark,
        title=f"{strategy_name} Equity Curve",
        interactive=interactive
    )
    charts_html.append(("Equity Curve", equity_chart.to_html(full_html=False)))

    # 2. Drawdown
    drawdown_chart = plot_drawdown(
        equity,
        title="Drawdown Analysis",
        interactive=interactive
    )
    charts_html.append(("Drawdown", drawdown_chart.to_html(full_html=False)))

    # 3. Benchmark comparison
    if benchmark is not None:
        benchmark_chart = plot_benchmark_comparison(
            equity, benchmark,
            title="Strategy vs Benchmark",
            interactive=interactive
        )
        charts_html.append(("Benchmark Comparison", benchmark_chart.to_html(full_html=False)))

    # 4. Signal overlay
    if signals is not None and price is not None:
        signal_chart = plot_signal_overlay(
            price, signals,
            title="Price with Trading Signals",
            interactive=interactive
        )
        charts_html.append(("Signal Overlay", signal_chart.to_html(full_html=False)))

    # 5. Annual returns
    annual_chart = plot_annual_returns(
        equity,
        title="Annual Returns",
        interactive=interactive
    )
    charts_html.append(("Annual Returns", annual_chart.to_html(full_html=False)))

    # Calculate metrics if not provided
    if metrics is None:
        metrics = _calculate_metrics(equity, benchmark)

    # Generate HTML report
    html_content = _generate_html_report(
        strategy_name=strategy_name,
        metrics=metrics,
        charts=charts_html,
        timestamp=timestamp,
    )

    # Save report
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"Report generated: {report_file}")
    return str(report_file)


def _calculate_metrics(equity: pd.Series, benchmark: Optional[pd.Series] = None) -> Dict[str, Any]:
    """Calculate performance metrics."""
    returns = equity.pct_change().dropna()

    # Basic metrics
    total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    annual_return = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100

    # Volatility
    annual_vol = returns.std() * np.sqrt(252) * 100

    # Sharpe ratio (assuming risk-free rate = 3%)
    risk_free = 0.03
    sharpe = (annual_return / 100 - risk_free) / (annual_vol / 100)

    # Max drawdown
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax
    max_drawdown = drawdown.min() * 100

    # Calmar ratio
    calmar = (annual_return / 100) / abs(max_drawdown / 100) if max_drawdown != 0 else 0

    metrics = {
        'Total Return': f"{total_return:.2f}%",
        'Annual Return': f"{annual_return:.2f}%",
        'Annual Volatility': f"{annual_vol:.2f}%",
        'Sharpe Ratio': f"{sharpe:.2f}",
        'Max Drawdown': f"{max_drawdown:.2f}%",
        'Calmar Ratio': f"{calmar:.2f}",
        'Start Date': equity.index[0].strftime('%Y-%m-%d'),
        'End Date': equity.index[-1].strftime('%Y-%m-%d'),
        'Trading Days': len(equity),
    }

    # Benchmark metrics
    if benchmark is not None:
        bench_return = (benchmark.iloc[-1] / benchmark.iloc[0] - 1) * 100
        bench_years = (benchmark.index[-1] - benchmark.index[0]).days / 365.25
        bench_annual = ((benchmark.iloc[-1] / benchmark.iloc[0]) ** (1 / bench_years) - 1) * 100

        metrics['Benchmark Total Return'] = f"{bench_return:.2f}%"
        metrics['Benchmark Annual Return'] = f"{bench_annual:.2f}%"
        metrics['Alpha'] = f"{annual_return - bench_annual:.2f}%"

    return metrics


def _generate_html_report(
    strategy_name: str,
    metrics: Dict[str, Any],
    charts: list,
    timestamp: str,
) -> str:
    """Generate HTML report content."""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{strategy_name} - Performance Report</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background-color: white;
            padding: 30px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #34495e;
            margin-top: 40px;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin: 30px 0;
        }}
        .metric-card {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }}
        .metric-label {{
            font-size: 14px;
            opacity: 0.9;
            margin-bottom: 5px;
        }}
        .metric-value {{
            font-size: 28px;
            font-weight: bold;
        }}
        .chart-container {{
            margin: 40px 0;
            padding: 20px;
            background-color: #fafafa;
            border-radius: 8px;
        }}
        .timestamp {{
            color: #7f8c8d;
            font-size: 12px;
            text-align: right;
            margin-top: 40px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 {strategy_name} - Performance Report</h1>

        <h2>Key Metrics</h2>
        <div class="metrics-grid">
"""

    # Add metrics cards
    for label, value in metrics.items():
        html += f"""
            <div class="metric-card">
                <div class="metric-label">{label}</div>
                <div class="metric-value">{value}</div>
            </div>
"""

    html += """
        </div>

        <h2>Performance Charts</h2>
"""

    # Add charts
    for chart_title, chart_html in charts:
        html += f"""
        <div class="chart-container">
            <h3>{chart_title}</h3>
            {chart_html}
        </div>
"""

    html += f"""
        <div class="timestamp">
            Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        </div>
    </div>
</body>
</html>
"""

    return html
