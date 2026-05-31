"""
QTrade Web Dashboard

Streamlit-based web dashboard for QTrade framework.
Provides interactive UI for:
- Strategy management
- Backtest execution and visualization
- Live trading monitoring
- Performance reports
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import time
from datetime import datetime, timedelta

# Configuration
API_URL = "http://localhost:8000"

# Page config
st.set_page_config(
    page_title="QTrade Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        color: #1f77b4;
        text-align: center;
        margin-bottom: 2rem;
    }
    .metric-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 0.5rem;
        margin: 0.5rem 0;
    }
</style>
""", unsafe_allow_html=True)


# ============================================================================
# Helper Functions
# ============================================================================

def api_request(method: str, endpoint: str, **kwargs):
    """Make API request with error handling."""
    try:
        url = f"{API_URL}{endpoint}"
        response = requests.request(method, url, timeout=10, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        st.error("无法连接到 API 服务器，请确保服务已启动")
        return None
    except Exception as e:
        st.error(f"API 请求失败: {str(e)}")
        return None


# ============================================================================
# Sidebar Navigation
# ============================================================================

st.sidebar.title("📊 QTrade Dashboard")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "导航",
    ["🏠 概览", "📈 策略管理", "🔬 回测", "💹 实盘交易", "📊 报告", "⚙️ 配置"],
)

st.sidebar.markdown("---")
st.sidebar.info("QTrade v1.0.0\nA股量化交易框架")


# ============================================================================
# Overview Page
# ============================================================================

if page == "🏠 概览":
    st.markdown('<h1 class="main-header">QTrade 量化交易系统</h1>', unsafe_allow_html=True)

    # Check API health
    health = api_request("GET", "/health")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if health:
            st.metric("系统状态", "✅ 运行中", delta="正常")
        else:
            st.metric("系统状态", "❌ 离线", delta="异常")

    with col2:
        strategies = api_request("GET", "/strategies")
        if strategies:
            st.metric("可用策略", len(strategies))
        else:
            st.metric("可用策略", "N/A")

    with col3:
        st.metric("活跃回测", "0")

    with col4:
        st.metric("实盘交易", "0")

    st.markdown("---")

    st.subheader("🚀 快速开始")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
        ### 回测策略
        1. 在左侧选择"回测"
        2. 选择策略和参数
        3. 点击"运行回测"
        4. 查看结果和报告
        """)

    with col2:
        st.markdown("""
        ### 实盘交易
        1. 在左侧选择"实盘交易"
        2. 配置交易参数
        3. 启动交易系统
        4. 实时监控状态
        """)

    st.markdown("---")

    st.subheader("📚 功能特点")

    features = [
        "✅ 多数据源支持（TDX、AkShare）",
        "✅ 丰富的技术指标库",
        "✅ 规则策略与ML策略统一接口",
        "✅ 参数优化（网格搜索、贝叶斯）",
        "✅ 多策略组合与资金分配",
        "✅ 完善的风控中间件",
        "✅ 实盘交易支持",
        "✅ 可视化报告生成",
    ]

    cols = st.columns(2)
    for i, feature in enumerate(features):
        cols[i % 2].markdown(feature)


# ============================================================================
# Strategy Management Page
# ============================================================================

elif page == "📈 策略管理":
    st.title("📈 策略管理")

    strategies = api_request("GET", "/strategies")

    if strategies:
        st.subheader("可用策略列表")

        for strategy in strategies:
            with st.expander(f"📌 {strategy['name']}"):
                st.markdown(f"**描述:** {strategy['description']}")

                if strategy['parameters']:
                    st.markdown("**默认参数:**")
                    params_df = pd.DataFrame([
                        {"参数": k, "值": v}
                        for k, v in strategy['parameters'].items()
                    ])
                    st.dataframe(params_df, use_container_width=True)
    else:
        st.warning("无法加载策略列表")


# ============================================================================
# Backtest Page
# ============================================================================

elif page == "🔬 回测":
    st.title("🔬 策略回测")

    # Backtest form
    with st.form("backtest_form"):
        col1, col2 = st.columns(2)

        with col1:
            strategy_name = st.selectbox(
                "选择策略",
                ["dual_ma", "rsi_bb", "momentum"]
            )

            symbol = st.text_input("股票代码", value="000001.SZ")

            start_date = st.date_input(
                "开始日期",
                value=datetime.now() - timedelta(days=365)
            )

            end_date = st.date_input("结束日期", value=datetime.now())

        with col2:
            initial_capital = st.number_input(
                "初始资金",
                value=100000.0,
                step=10000.0
            )

            commission = st.number_input(
                "手续费率",
                value=0.001,
                step=0.0001,
                format="%.4f"
            )

            slippage = st.number_input(
                "滑点",
                value=0.001,
                step=0.0001,
                format="%.4f"
            )

        st.markdown("---")
        st.subheader("策略参数")

        # Strategy-specific parameters
        if strategy_name == "dual_ma":
            param_col1, param_col2 = st.columns(2)
            with param_col1:
                fast_period = st.number_input("快线周期", value=5, step=1)
            with param_col2:
                slow_period = st.number_input("慢线周期", value=20, step=1)
            params = {"fast_period": int(fast_period), "slow_period": int(slow_period)}

        elif strategy_name == "rsi_bb":
            param_col1, param_col2, param_col3 = st.columns(3)
            with param_col1:
                rsi_period = st.number_input("RSI周期", value=14, step=1)
            with param_col2:
                bb_period = st.number_input("BB周期", value=20, step=1)
            with param_col3:
                bb_std = st.number_input("BB标准差", value=2.0, step=0.1)
            params = {
                "rsi_period": int(rsi_period),
                "bb_period": int(bb_period),
                "bb_std": float(bb_std),
            }

        else:
            params = {}

        submitted = st.form_submit_button("🚀 运行回测", use_container_width=True)

    # Run backtest
    if submitted:
        with st.spinner("正在运行回测..."):
            backtest_request = {
                "strategy_name": strategy_name,
                "symbol": symbol,
                "start_date": start_date.strftime("%Y-%m-%d"),
                "end_date": end_date.strftime("%Y-%m-%d"),
                "initial_capital": initial_capital,
                "commission": commission,
                "slippage": slippage,
                "params": params,
            }

            response = api_request("POST", "/backtest", json=backtest_request)

            if response:
                backtest_id = response["backtest_id"]
                st.success(f"回测已启动! ID: {backtest_id}")

                # Poll for results
                progress_bar = st.progress(0)
                status_text = st.empty()

                for i in range(100):
                    time.sleep(0.5)
                    progress_bar.progress(i + 1)
                    status_text.text(f"运行中... {i+1}%")

                    result = api_request("GET", f"/backtest/{backtest_id}")

                    if result and result["status"] == "completed":
                        progress_bar.progress(100)
                        status_text.text("✅ 回测完成!")
                        st.session_state["backtest_result"] = result
                        break
                    elif result and result["status"] == "failed":
                        st.error("回测失败!")
                        break

    # Display results
    if "backtest_result" in st.session_state:
        result = st.session_state["backtest_result"]

        st.markdown("---")
        st.subheader("📊 回测结果")

        # Metrics
        if "metrics" in result and result["metrics"]:
            metrics = result["metrics"]

            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.metric("总收益率", f"{metrics.get('total_return', 0):.2f}%")
            with col2:
                st.metric("年化收益率", f"{metrics.get('annual_return', 0):.2f}%")
            with col3:
                st.metric("夏普比率", f"{metrics.get('sharpe_ratio', 0):.2f}")
            with col4:
                st.metric("最大回撤", f"{metrics.get('max_drawdown', 0):.2f}%")

            col1, col2, col3, col4 = st.columns(4)

            with col1:
                st.metric("胜率", f"{metrics.get('win_rate', 0):.2f}%")
            with col2:
                st.metric("盈亏比", f"{metrics.get('profit_loss_ratio', 0):.2f}")
            with col3:
                st.metric("交易次数", f"{metrics.get('total_trades', 0)}")
            with col4:
                st.metric("最终资金", f"¥{metrics.get('final_value', 0):,.2f}")

        # Equity curve
        if "equity_curve" in result and result["equity_curve"]:
            st.markdown("---")
            st.subheader("📈 资金曲线")

            equity_df = pd.DataFrame(result["equity_curve"])
            equity_df["date"] = pd.to_datetime(equity_df["date"])

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=equity_df["date"],
                y=equity_df["value"],
                mode="lines",
                name="资金曲线",
                line=dict(color="#1f77b4", width=2),
            ))

            fig.update_layout(
                title="资金曲线",
                xaxis_title="日期",
                yaxis_title="资金 (¥)",
                hovermode="x unified",
            )

            st.plotly_chart(fig, use_container_width=True)

        # Trades
        if "trades" in result and result["trades"]:
            st.markdown("---")
            st.subheader("📋 交易记录")

            trades_df = pd.DataFrame(result["trades"])
            st.dataframe(trades_df, use_container_width=True)


# ============================================================================
# Live Trading Page
# ============================================================================

elif page == "💹 实盘交易":
    st.title("💹 实盘交易")

    st.warning("⚠️ 实盘交易涉及真实资金，请谨慎操作！")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("启动交易")

        with st.form("live_trading_form"):
            strategy_name = st.selectbox("选择策略", ["dual_ma", "rsi_bb"])
            symbols = st.text_input("股票代码（逗号分隔）", value="000001.SZ,000002.SZ")
            broker = st.selectbox("券商", ["mock", "alpaca"])
            config_path = st.text_input("配置文件路径", value="configs/live_trading.yaml")

            submitted = st.form_submit_button("🚀 启动实盘交易")

            if submitted:
                symbols_list = [s.strip() for s in symbols.split(",")]

                live_request = {
                    "strategy_name": strategy_name,
                    "symbols": symbols_list,
                    "broker": broker,
                    "config_path": config_path,
                }

                response = api_request("POST", "/live/start", json=live_request)

                if response:
                    st.success(f"实盘交易已启动! ID: {response['trader_id']}")
                    st.session_state["trader_id"] = response["trader_id"]

    with col2:
        st.subheader("交易状态")

        if "trader_id" in st.session_state:
            trader_id = st.session_state["trader_id"]

            status = api_request("GET", f"/live/{trader_id}/status")

            if status:
                st.json(status)

                if st.button("🛑 停止交易"):
                    response = api_request("POST", f"/live/{trader_id}/stop")
                    if response:
                        st.success("交易已停止")
                        del st.session_state["trader_id"]
                        st.experimental_rerun()

                if st.button("🚨 紧急停止", type="primary"):
                    response = api_request(
                        "POST",
                        f"/live/{trader_id}/emergency-stop",
                        params={"reason": "Manual emergency stop from dashboard"}
                    )
                    if response:
                        st.error("紧急停止已触发!")
        else:
            st.info("暂无活跃的交易")

    # Positions and Orders
    if "trader_id" in st.session_state:
        st.markdown("---")

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("📊 持仓")
            positions = api_request("GET", f"/live/{st.session_state['trader_id']}/positions")
            if positions:
                st.json(positions)

        with col2:
            st.subheader("📋 订单")
            orders = api_request("GET", f"/live/{st.session_state['trader_id']}/orders")
            if orders:
                st.json(orders)


# ============================================================================
# Reports Page
# ============================================================================

elif page == "📊 报告":
    st.title("📊 性能报告")

    st.info("此功能将显示历史回测和实盘交易的详细报告")

    # Placeholder for reports
    st.subheader("最近的回测报告")
    st.write("暂无报告")


# ============================================================================
# Configuration Page
# ============================================================================

elif page == "⚙️ 配置":
    st.title("⚙️ 系统配置")

    # API Configuration
    st.subheader("API 配置")

    col1, col2 = st.columns(2)

    with col1:
        new_api_url = st.text_input("API URL", value=API_URL)

    with col2:
        if st.button("测试连接"):
            health = api_request("GET", "/health")
            if health:
                st.success("连接成功!")
            else:
                st.error("连接失败!")

    st.markdown("---")

    # System Configuration
    st.subheader("系统配置")

    config = api_request("GET", "/config")

    if config:
        st.json(config)

    st.markdown("---")

    # Available Configs
    st.subheader("可用配置文件")

    configs = api_request("GET", "/configs")

    if configs:
        for cfg in configs.get("configs", []):
            st.markdown(f"- **{cfg['name']}**: `{cfg['path']}`")
