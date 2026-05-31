"""
QTrade REST API Server

FastAPI-based REST API for QTrade framework.
Provides endpoints for:
- Strategy management
- Backtest execution
- Live trading control
- Performance reports
- Data management
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path
import asyncio
import yaml

from qtrade.backtest import BacktestEngine
from qtrade.data import DataFetcher
from qtrade.strategies import StrategyRegistry
from qtrade.config import Config

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
active_backtests = {}
live_traders = {}


# ============================================================================
# Request/Response Models
# ============================================================================

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str


class BacktestRequest(BaseModel):
    strategy_name: str = Field(..., description="Name of the strategy to backtest")
    symbol: str = Field(..., description="Stock symbol (e.g., '000001.SZ')")
    start_date: str = Field(..., description="Start date (YYYY-MM-DD)")
    end_date: str = Field(..., description="End date (YYYY-MM-DD)")
    initial_capital: float = Field(100000.0, description="Initial capital")
    commission: float = Field(0.001, description="Commission rate")
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


class StrategyInfo(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]


class LiveTradingRequest(BaseModel):
    strategy_name: str
    symbols: List[str]
    config_path: str
    broker: str = Field("mock", description="Broker type: mock, alpaca")


class LiveTradingResponse(BaseModel):
    trader_id: str
    status: str
    message: str


# ============================================================================
# Health Check
# ============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(),
        version="1.0.0",
    )


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "QTrade API Server",
        "version": "1.0.0",
        "docs": "/docs",
    }


# ============================================================================
# Strategy Management
# ============================================================================

@app.get("/strategies", response_model=List[StrategyInfo])
async def list_strategies():
    """List all available strategies."""
    strategies = StrategyRegistry.list_strategies()

    result = []
    for name in strategies:
        strategy_class = StrategyRegistry.get(name)
        if strategy_class:
            # Get default parameters
            params = {}
            if hasattr(strategy_class, 'default_params'):
                params = strategy_class.default_params()

            result.append(StrategyInfo(
                name=name,
                description=strategy_class.__doc__ or "No description",
                parameters=params,
            ))

    return result


@app.get("/strategies/{strategy_name}", response_model=StrategyInfo)
async def get_strategy(strategy_name: str):
    """Get strategy details."""
    strategy_class = StrategyRegistry.get(strategy_name)
    if not strategy_class:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_name}' not found")

    params = {}
    if hasattr(strategy_class, 'default_params'):
        params = strategy_class.default_params()

    return StrategyInfo(
        name=strategy_name,
        description=strategy_class.__doc__ or "No description",
        parameters=params,
    )


# ============================================================================
# Backtest Management
# ============================================================================

@app.post("/backtest", response_model=BacktestResponse)
async def start_backtest(request: BacktestRequest, background_tasks: BackgroundTasks):
    """Start a backtest job."""
    import uuid

    backtest_id = str(uuid.uuid4())

    # Store initial state
    active_backtests[backtest_id] = {
        "status": "running",
        "started_at": datetime.now(),
        "request": request.dict(),
    }

    # Run backtest in background
    background_tasks.add_task(run_backtest_task, backtest_id, request)

    return BacktestResponse(
        backtest_id=backtest_id,
        status="running",
        message="Backtest started successfully",
    )


async def run_backtest_task(backtest_id: str, request: BacktestRequest):
    """Background task to run backtest."""
    try:
        # Load configuration
        config = Config()
        config.data.symbol = request.symbol
        config.data.start_date = request.start_date
        config.data.end_date = request.end_date
        config.backtest.initial_capital = request.initial_capital
        config.backtest.commission = request.commission
        config.backtest.slippage = request.slippage
        config.strategy.name = request.strategy_name

        if request.params:
            config.strategy.params = request.params

        # Fetch data
        fetcher = DataFetcher()
        data = fetcher.fetch_history(
            symbol=request.symbol,
            start_date=request.start_date,
            end_date=request.end_date,
        )

        # Create strategy
        strategy_class = StrategyRegistry.get(request.strategy_name)
        if not strategy_class:
            raise ValueError(f"Strategy '{request.strategy_name}' not found")

        strategy = strategy_class(**(request.params or {}))

        # Run backtest
        engine = BacktestEngine(config)
        result = engine.run(strategy, data)

        # Store results
        active_backtests[backtest_id]["status"] = "completed"
        active_backtests[backtest_id]["completed_at"] = datetime.now()
        active_backtests[backtest_id]["metrics"] = result.metrics.to_dict()
        active_backtests[backtest_id]["trades"] = result.trades.to_dict('records')
        active_backtests[backtest_id]["equity_curve"] = result.equity_curve.to_dict('records')

    except Exception as e:
        active_backtests[backtest_id]["status"] = "failed"
        active_backtests[backtest_id]["error"] = str(e)


@app.get("/backtest/{backtest_id}", response_model=BacktestResultResponse)
async def get_backtest_result(backtest_id: str):
    """Get backtest results."""
    if backtest_id not in active_backtests:
        raise HTTPException(status_code=404, detail="Backtest not found")

    backtest = active_backtests[backtest_id]

    if backtest["status"] == "running":
        return BacktestResultResponse(
            backtest_id=backtest_id,
            status="running",
        )
    elif backtest["status"] == "failed":
        return BacktestResultResponse(
            backtest_id=backtest_id,
            status="failed",
        )
    else:
        return BacktestResultResponse(
            backtest_id=backtest_id,
            status="completed",
            metrics=backtest.get("metrics"),
            trades=backtest.get("trades"),
            equity_curve=backtest.get("equity_curve"),
        )


@app.get("/backtest/{backtest_id}/report")
async def get_backtest_report(backtest_id: str):
    """Get backtest HTML report."""
    if backtest_id not in active_backtests:
        raise HTTPException(status_code=404, detail="Backtest not found")

    backtest = active_backtests[backtest_id]
    if backtest["status"] != "completed":
        raise HTTPException(status_code=400, detail="Backtest not completed yet")

    report_path = Path("reports") / f"backtest_{backtest_id}.html"

    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")

    return FileResponse(
        path=str(report_path),
        media_type="text/html",
        filename=f"backtest_{backtest_id}.html",
    )


# ============================================================================
# Live Trading Management
# ============================================================================

@app.post("/live/start", response_model=LiveTradingResponse)
async def start_live_trading(request: LiveTradingRequest):
    """Start live trading."""
    import uuid

    trader_id = str(uuid.uuid4())

    try:
        # Load configuration
        config_path = Path(request.config_path)
        if not config_path.exists():
            raise ValueError(f"Config file not found: {request.config_path}")

        with open(config_path, 'r') as f:
            config_dict = yaml.safe_load(f)

        # Create broker
        if request.broker == "mock":
            from qtrade.live_trading.broker import MockBroker
            broker = MockBroker()
        elif request.broker == "alpaca":
            from qtrade.live_trading.broker import AlpacaBroker
            broker = AlpacaBroker()
        else:
            raise ValueError(f"Unknown broker: {request.broker}")

        # Create data feed
        from qtrade.live_trading.data_feed import RealtimeDataFeed
        data_feed = RealtimeDataFeed(config_dict)

        # Create strategy
        strategy_class = StrategyRegistry.get(request.strategy_name)
        if not strategy_class:
            raise ValueError(f"Strategy '{request.strategy_name}' not found")

        strategy = strategy_class()

        # Create live trader
        from qtrade.live_trading import LiveTrader
        trader = LiveTrader(
            broker=broker,
            data_feed=data_feed,
            strategy=strategy,
            symbols=request.symbols,
        )

        # Start trader
        asyncio.create_task(trader.start())

        # Store trader
        live_traders[trader_id] = {
            "trader": trader,
            "status": "running",
            "started_at": datetime.now(),
            "symbols": request.symbols,
            "strategy": request.strategy_name,
        }

        return LiveTradingResponse(
            trader_id=trader_id,
            status="running",
            message="Live trading started successfully",
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/live/{trader_id}/stop")
async def stop_live_trading(trader_id: str):
    """Stop live trading."""
    if trader_id not in live_traders:
        raise HTTPException(status_code=404, detail="Trader not found")

    trader_info = live_traders[trader_id]
    trader = trader_info["trader"]

    await trader.stop()

    trader_info["status"] = "stopped"
    trader_info["stopped_at"] = datetime.now()

    return {"message": "Live trading stopped successfully"}


@app.get("/live/{trader_id}/status")
async def get_live_trading_status(trader_id: str):
    """Get live trading status."""
    if trader_id not in live_traders:
        raise HTTPException(status_code=404, detail="Trader not found")

    trader_info = live_traders[trader_id]
    trader = trader_info["trader"]

    status = trader.get_status()

    return {
        "trader_id": trader_id,
        "status": trader_info["status"],
        "started_at": trader_info["started_at"],
        "symbols": trader_info["symbols"],
        "strategy": trader_info["strategy"],
        "metrics": status,
    }


@app.get("/live/{trader_id}/positions")
async def get_live_positions(trader_id: str):
    """Get live trading positions."""
    if trader_id not in live_traders:
        raise HTTPException(status_code=404, detail="Trader not found")

    trader = live_traders[trader_id]["trader"]
    positions = trader.position_sync.get_position_summary()

    return {"positions": positions}


@app.get("/live/{trader_id}/orders")
async def get_live_orders(trader_id: str):
    """Get live trading orders."""
    if trader_id not in live_traders:
        raise HTTPException(status_code=404, detail="Trader not found")

    trader = live_traders[trader_id]["trader"]
    orders = trader.get_orders()

    return {"orders": orders}


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
    """List available symbols."""
    # This would query the data source
    return {"symbols": []}


@app.get("/data/{symbol}/info")
async def get_symbol_info(symbol: str):
    """Get symbol information."""
    return {"symbol": symbol, "info": {}}


# ============================================================================
# Configuration Management
# ============================================================================

@app.get("/config")
async def get_config():
    """Get current configuration."""
    config_path = Path("configs/default.yaml")
    if not config_path.exists():
        raise HTTPException(status_code=404, detail="Config not found")

    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    return config


@app.get("/configs")
async def list_configs():
    """List available configuration files."""
    config_dir = Path("configs")
    configs = []

    for config_file in config_dir.glob("*.yaml"):
        configs.append({
            "name": config_file.stem,
            "path": str(config_file),
        })

    return {"configs": configs}


# ============================================================================
# Error Handlers
# ============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler."""
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "type": type(exc).__name__,
        },
    )


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
