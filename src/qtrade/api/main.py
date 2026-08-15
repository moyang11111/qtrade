"""
QTrade REST API Server

FastAPI-based REST API for QTrade framework.
Provides endpoints for:
- Strategy management
- Backtest execution
- Live trading control
- Data management
- Configuration management
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from qtrade.backtest import BacktestEngine
from qtrade.data import DataFetcher
from qtrade.strategy.registry import get_signal_generator, list_strategies as list_strategy_names

# Create FastAPI app
app = FastAPI(
    title="QTrade API",
    description="REST API for QTrade quantitative trading framework",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
active_backtests: Dict[str, dict] = {}
live_traders: Dict[str, dict] = {}


# ============================================================================
# Request/Response Models
# ============================================================================

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str


class BacktestRequest(BaseModel):
    strategy_name: str = Field(..., description="Name of the strategy to backtest")
    symbol: str = Field(..., description="Stock symbol (e.g., '000001')")
    start_date: str = Field(..., description="Start date (YYYYMMDD)")
    end_date: Optional[str] = Field(None, description="End date (YYYYMMDD)")
    initial_capital: float = Field(100000.0, description="Initial capital")
    commission: float = Field(0.0003, description="Commission rate")
    slippage: float = Field(0.001, description="Slippage rate")
    params: Optional[Dict[str, Any]] = Field(None, description="Strategy parameters")


class BacktestResponse(BaseModel):
    backtest_id: str
    status: str
    message: str


class BacktestResultResponse(BaseModel):
    backtest_id: str
    status: str
    metrics: Optional[Dict[str, Any]] = None
    trades: Optional[List[Dict[str, Any]]] = None
    equity_curve: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None


class StrategyInfo(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]


class LiveTradingRequest(BaseModel):
    strategy_name: str
    symbols: List[str]
    config_path: Optional[str] = None
    broker: str = Field("mock", description="Broker type: mock, alpaca")
    initial_cash: float = Field(100000.0, description="Initial cash")


class LiveTradingResponse(BaseModel):
    trader_id: str
    status: str
    message: str


# ============================================================================
# Helpers
# ============================================================================

def _build_backtest_cfg(request: BacktestRequest) -> dict:
    return {
        "data": {
            "symbol": request.symbol,
            "start_date": request.start_date,
            "end_date": request.end_date,
        },
        "backtest": {
            "initial_capital": request.initial_capital,
            "commission": request.commission,
            "slippage": request.slippage,
        },
        "strategy": {
            "name": request.strategy_name,
            "type": "rule",
            "params": request.params or {},
        },
    }


def _run_backtest_for_request(request: BacktestRequest) -> dict:
    cfg = _build_backtest_cfg(request)
    fetcher = DataFetcher(cfg)
    df = fetcher.fetch(request.symbol, request.start_date, request.end_date or "")
    strategy_cls = get_signal_generator(request.strategy_name)
    strategy = strategy_cls({"name": request.strategy_name, **(request.params or {})})
    df_signals = strategy.generate_signals(df)
    engine = BacktestEngine(cfg)
    result = engine.run(df_signals)

    equity_curve: List[Dict[str, Any]] = []
    if not result.equity_curve.empty:
        equity_curve = [
            {"date": str(d), "value": float(v)}
            for d, v in result.equity_curve.items()
        ]

    return {
        "metrics": result.metrics,
        "trades": result.trade_log,
        "equity_curve": equity_curve,
    }


# ============================================================================
# Root
# ============================================================================

@app.get("/", response_model=None)
async def root():
    """Root endpoint."""
    return {
        "message": "QTrade API Server",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", timestamp=datetime.now(), version="1.0.0")


# ============================================================================
# Strategy Management
# ============================================================================

@app.get("/strategies", response_model=List[StrategyInfo])
async def list_strategies():
    """List all available strategies."""
    result = []
    for name in list_strategy_names():
        try:
            strategy_cls = get_signal_generator(name)
        except KeyError:
            continue
        result.append(StrategyInfo(
            name=name,
            description=(strategy_cls.__doc__ or "No description").strip(),
            parameters={},
        ))
    return result


@app.get("/strategies/{strategy_name}", response_model=StrategyInfo)
async def get_strategy(strategy_name: str):
    """Get strategy details."""
    try:
        strategy_cls = get_signal_generator(strategy_name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_name}' not found")

    return StrategyInfo(
        name=strategy_name,
        description=(strategy_cls.__doc__ or "No description").strip(),
        parameters={},
    )


# ============================================================================
# Backtest Management
# ============================================================================

@app.post("/backtest", response_model=BacktestResponse)
async def start_backtest(request: BacktestRequest, background_tasks: BackgroundTasks):
    """Start a backtest job."""
    import uuid

    backtest_id = str(uuid.uuid4())
    active_backtests[backtest_id] = {
        "status": "running",
        "started_at": datetime.now(),
        "request": request,
    }
    background_tasks.add_task(run_backtest_task, backtest_id, request)
    return BacktestResponse(backtest_id=backtest_id, status="running", message="Backtest started successfully")


def run_backtest_task(backtest_id: str, request: BacktestRequest):
    """Background task to run backtest."""
    try:
        result = _run_backtest_for_request(request)
        active_backtests[backtest_id].update({
            "status": "completed",
            "completed_at": datetime.now(),
            **result,
        })
    except Exception as e:
        active_backtests[backtest_id].update({
            "status": "failed",
            "completed_at": datetime.now(),
            "error": str(e),
        })


@app.get("/backtest/{backtest_id}", response_model=BacktestResultResponse)
async def get_backtest_result(backtest_id: str):
    """Get backtest results."""
    if backtest_id not in active_backtests:
        raise HTTPException(status_code=404, detail="Backtest not found")

    backtest = active_backtests[backtest_id]
    if backtest["status"] == "running":
        return BacktestResultResponse(backtest_id=backtest_id, status="running")
    if backtest["status"] == "failed":
        return BacktestResultResponse(backtest_id=backtest_id, status="failed", error=backtest.get("error"))

    return BacktestResultResponse(
        backtest_id=backtest_id,
        status="completed",
        metrics=backtest.get("metrics"),
        trades=backtest.get("trades"),
        equity_curve=backtest.get("equity_curve"),
    )


# ============================================================================
# Live Trading Management
# ============================================================================

def _create_live_trader(strategy_name: str, symbols: List[str], initial_cash: float):
    from qtrade.live_trading.broker import MockBroker
    from qtrade.live_trading.live_trader import LiveTrader
    from qtrade.live_trading.risk_monitor import RiskMonitor

    strategy_cls = get_signal_generator(strategy_name)
    strategy = strategy_cls({"name": strategy_name})

    broker = MockBroker(initial_cash=initial_cash)

    try:
        from qtrade.live_trading.tdx_feed import TdxQuoteFeed
        feed = TdxQuoteFeed(poll_interval=3.0)
        if not feed.connect():
            feed = None
    except Exception:
        feed = None

    if feed is None:
        from qtrade.live_trading.baidu_feed import BaiduQuoteFeed
        feed = BaiduQuoteFeed(poll_interval=5.0)

    trader = LiveTrader(
        broker=broker,
        data_feed=feed,
        strategy=strategy,
        risk_monitor=RiskMonitor(),
        signal_interval=30.0,
    )
    return trader


@app.post("/live/start", response_model=LiveTradingResponse)
async def start_live_trading(request: LiveTradingRequest):
    """Start live trading."""
    import uuid

    trader_id = str(uuid.uuid4())
    try:
        trader = _create_live_trader(request.strategy_name, request.symbols, request.initial_cash)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    thread = threading.Thread(target=trader.start, args=(request.symbols,), daemon=True)
    thread.start()

    live_traders[trader_id] = {
        "trader": trader,
        "status": "running",
        "started_at": datetime.now(),
        "symbols": request.symbols,
        "strategy": request.strategy_name,
    }
    return LiveTradingResponse(trader_id=trader_id, status="running", message="Live trading started successfully")


@app.post("/live/{trader_id}/stop")
async def stop_live_trading(trader_id: str):
    """Stop live trading."""
    if trader_id not in live_traders:
        raise HTTPException(status_code=404, detail="Trader not found")

    trader = live_traders[trader_id]["trader"]
    trader.stop()
    live_traders[trader_id]["status"] = "stopped"
    live_traders[trader_id]["stopped_at"] = datetime.now()
    return {"message": "Live trading stopped successfully"}


@app.get("/live/{trader_id}/status")
async def get_live_trading_status(trader_id: str):
    """Get live trading status."""
    if trader_id not in live_traders:
        raise HTTPException(status_code=404, detail="Trader not found")

    trader_info = live_traders[trader_id]
    trader = trader_info["trader"]
    return {
        "trader_id": trader_id,
        "status": trader_info["status"],
        "started_at": trader_info["started_at"],
        "symbols": trader_info["symbols"],
        "strategy": trader_info["strategy"],
        "metrics": trader.get_status(),
    }


@app.get("/live/{trader_id}/positions")
async def get_live_positions(trader_id: str):
    """Get live trading positions."""
    if trader_id not in live_traders:
        raise HTTPException(status_code=404, detail="Trader not found")

    trader = live_traders[trader_id]["trader"]
    positions = trader.position_sync.get_position_summary()
    return {"positions": positions.to_dict(orient="records") if not positions.empty else []}


@app.get("/live/{trader_id}/orders")
async def get_live_orders(trader_id: str):
    """Get live trading orders."""
    if trader_id not in live_traders:
        raise HTTPException(status_code=404, detail="Trader not found")

    trader = live_traders[trader_id]["trader"]
    return {"orders": trader.get_orders()}


@app.post("/live/{trader_id}/emergency-stop")
async def emergency_stop(trader_id: str, reason: str = "Manual emergency stop"):
    """Trigger emergency stop."""
    if trader_id not in live_traders:
        raise HTTPException(status_code=404, detail="Trader not found")

    trader = live_traders[trader_id]["trader"]
    trader.emergency_stop(reason)
    return {"message": f"Emergency stop triggered: {reason}"}


# ============================================================================
# Data Management
# ============================================================================

@app.get("/data/symbols")
async def list_symbols():
    """List symbols available in the local CSV cache."""
    cache_dir = Path("data/cache")
    if not cache_dir.exists():
        return {"symbols": []}
    symbols = sorted(p.stem for p in cache_dir.glob("*.csv") if p.stat().st_size > 500)
    return {"symbols": symbols}


@app.get("/data/{symbol}/info")
async def get_symbol_info(symbol: str):
    """Get a small info payload for one symbol from cached data."""
    cache_path = Path("data/cache") / f"{symbol}.csv"
    if not cache_path.exists():
        raise HTTPException(status_code=404, detail=f"No cached data for {symbol}")
    df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
    return {
        "symbol": symbol,
        "rows": len(df),
        "start": str(df.index[0].date()),
        "end": str(df.index[-1].date()),
        "last_close": float(df["close"].iloc[-1]),
    }


# ============================================================================
# Configuration Management
# ============================================================================

@app.get("/config")
async def get_config():
    """Get current default configuration."""
    config_path = Path("configs/default.yaml")
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Config not found")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@app.get("/configs")
async def list_configs():
    """List available configuration files."""
    config_dir = Path("configs")
    configs = []
    for config_file in config_dir.glob("*.yaml"):
        configs.append({"name": config_file.stem, "path": str(config_file)})
    return {"configs": configs}


# ============================================================================
# Error Handlers
# ============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler."""
    return JSONResponse(
        status_code=500,
        content={"error": str(exc), "type": type(exc).__name__},
    )


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
