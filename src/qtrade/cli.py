"""CLI entry point â€?python -m qtrade."""

import argparse
import sys

import pandas as pd

from qtrade.config import load_config
from qtrade.logging_setup import setup_logging
from qtrade.backtest.performance import (
    print_report, calc_extended_metrics
)
from qtrade.backtest.trade_log import save_trade_log, print_trade_summary


def cmd_backtest(args):
    """Run backtest pipeline with extended metrics and visualization."""
    cfg = load_config(args.config)
    setup_logging(cfg)

    from qtrade.data.fetcher import DataFetcher
    from qtrade.backtest.engine import BacktestEngine

    # 1. Fetch data
    data_cfg = cfg["data"]
    fetcher = DataFetcher(cfg)

    # For ML, fetch more history (training data + backtest data)
    if cfg["strategy"].get("type") == "ml":
        train_start = cfg.get("ml", {}).get("train_start", "20200101")
        df = fetcher.fetch(data_cfg["symbol"], train_start, data_cfg.get("end_date"))
    else:
        df = fetcher.fetch(data_cfg["symbol"], data_cfg["start_date"], data_cfg.get("end_date"))

    print(f"[DATA] {data_cfg['symbol']}: {len(df)} rows "
          f"[{df.index[0].strftime('%Y-%m-%d')} ~ {df.index[-1].strftime('%Y-%m-%d')}]")

    # 2. Generate signals
    strat_cfg = cfg["strategy"]
    if strat_cfg.get("type") == "ml":
        df_with_signals = _generate_ml_signals(cfg, df, fetcher)
    else:
        from qtrade.strategy.registry import get_signal_generator
        strat_cls = get_signal_generator(strat_cfg["name"])
        params = {**strat_cfg.get("params", {}), "name": strat_cfg["name"]}
        generator = strat_cls(params)
        df_with_signals = generator.generate_signals(df)

    buy_count = (df_with_signals["signal_action"] == 1).sum()
    sell_count = (df_with_signals["signal_action"] == -1).sum()
    print(f"[SIGNAL] {strat_cfg['name']}: {buy_count} buys, {sell_count} sells")

    # 3. For ML, filter to backtest period only
    if strat_cfg.get("type") == "ml":
        bt_start = data_cfg["start_date"]
        df_bt = df_with_signals[df_with_signals.index >= pd.to_datetime(bt_start)]
        print(f"[BACKTEST] ML backtest period: {df_bt.index[0].strftime('%Y-%m-%d')} ~ "
              f"{df_bt.index[-1].strftime('%Y-%m-%d')} ({len(df_bt)} bars)")
    else:
        df_bt = df_with_signals

    # 4. Run backtest
    engine = BacktestEngine(cfg)
    result = engine.run(df_bt)

    # 5. Extended metrics
    ext_metrics = calc_extended_metrics(result.equity_curve, result.trade_log)
    result.metrics["_extended"] = ext_metrics

    # 6. Output
    print_report(result)
    print_trade_summary(result.trade_log)

    # 7. Save results
    output_cfg = cfg.get("output", {})
    results_dir = output_cfg.get("results_dir", "results")

    if output_cfg.get("save_results", False):
        save_trade_log(result.trade_log, results_dir)

    # 8. Visualization
    if args.plot or output_cfg.get("plot", False):
        _generate_plots(result, results_dir, cfg)

    # 9. QuantStats report
    if args.report or output_cfg.get("quantstats_report", False):
        _generate_quantstats_report(result, results_dir, cfg)


def _generate_plots(result, results_dir: str, cfg: dict):
    """Generate visualization charts."""
    from qtrade.visualization.charts import (
        plot_equity_curve, plot_drawdown, plot_annual_returns
    )

    symbol = cfg["data"]["symbol"]
    strat_name = cfg["strategy"]["name"]
    title = f"{symbol} â€?{strat_name}"

    if not result.equity_curve.empty:
        plot_equity_curve(
            result.equity_curve,
            title=title,
            save_path=f"{results_dir}/equity_curve.png"
        )
        plot_drawdown(
            result.equity_curve,
            save_path=f"{results_dir}/drawdown.png"
        )

    if result.trade_log:
        plot_annual_returns(
            result.equity_curve,
            title=f"{title} â€?Annual Returns",
            save_path=f"{results_dir}/annual_returns.png"
        )

    print(f"[VIZ] Charts saved to {results_dir}/")


def _generate_quantstats_report(result, results_dir: str, cfg: dict):
    """Generate QuantStats HTML report."""
    from qtrade.backtest.report import generate_quantstats_report

    if result.equity_curve.empty:
        print("[WARN] No equity curve for QuantStats report")
        return

    symbol = cfg["data"]["symbol"]
    strat_name = cfg["strategy"]["name"]
    title = f"qtrade: {symbol} â€?{strat_name}"

    try:
        path = generate_quantstats_report(
            result.equity_curve,
            output_path=f"{results_dir}/quantstats_report.html",
            title=title,
        )
        print(f"[REPORT] QuantStats report: {path}")
    except ImportError:
        print("[WARN] quantstats not installed. Run: pip install quantstats")
    except Exception as e:
        print(f"[WARN] QuantStats report failed: {e}")


def _generate_ml_signals(cfg, df, fetcher):
    """Load trained model and generate ML signals."""
    from qtrade.features.engine import FeatureEngine
    from qtrade.ml.registry import ModelRegistry
    from qtrade.strategy.ml.ml_signal import MLSignalGenerator

    ml_cfg = cfg["ml"]
    registry = ModelRegistry(ml_cfg.get("registry", {}).get("dir", "models"))

    # Load latest model
    models = registry.list_models()
    if not models:
        print("[ERROR] No trained models found. Run 'qtrade train' first.")
        sys.exit(1)
    latest = models[-1]
    model_id = latest["model_id"]
    print(f"[ML] Loading model: {model_id}")

    # Create model instance and load
    from qtrade.ml.pipeline import _create_model
    model = _create_model(ml_cfg)
    registry.load(model_id, model)
    model.freeze()

    # Create feature engine and signal generator
    feature_engine = FeatureEngine(ml_cfg.get("features", {}))
    strat_params = {**cfg["strategy"].get("params", {}), "name": cfg["strategy"]["name"]}
    generator = MLSignalGenerator(strat_params, model, feature_engine)

    return generator.generate_signals(df)


def cmd_train(args):
    """Train ML model."""
    cfg = load_config(args.config)
    logger = setup_logging(cfg)

    if cfg["strategy"].get("type") != "ml":
        print("[ERROR] strategy.type must be 'ml' for training")
        sys.exit(1)

    from qtrade.data.fetcher import DataFetcher
    from qtrade.ml.pipeline import MLPipeline

    data_cfg = cfg["data"]
    fetcher = DataFetcher(cfg)
    df = fetcher.fetch(data_cfg["symbol"], "20180101", data_cfg.get("end_date"))
    print(f"[DATA] {data_cfg['symbol']}: {len(df)} rows")

    pipeline = MLPipeline(cfg)
    result = pipeline.run(df, backtest_start=cfg["ml"]["train_end"])
    print(f"[ML] Model trained: {result['model_id']}")
    print(f"[ML] CV accuracy: {result['cv_results']['mean_accuracy']:.3f} "
          f"Â± {result['cv_results']['std_accuracy']:.3f}")


def cmd_compare(args):
    """Compare multiple strategies on the same data."""
    cfg = load_config(args.config)
    setup_logging(cfg)

    from qtrade.data.fetcher import DataFetcher
    from qtrade.backtest.engine import BacktestEngine
    from qtrade.strategy.registry import get_signal_generator, list_strategies
    from qtrade.visualization.comparison import plot_strategy_comparison, comparison_table

    # Fetch data
    data_cfg = cfg["data"]
    fetcher = DataFetcher(cfg)
    df = fetcher.fetch(data_cfg["symbol"], data_cfg["start_date"], data_cfg.get("end_date"))
    print(f"[DATA] {data_cfg['symbol']}: {len(df)} rows")

    # Get strategies to compare
    strategies = args.strategies if args.strategies else list_strategies()
    print(f"[COMPARE] Strategies: {strategies}")

    results = {}
    for strat_name in strategies:
        # Generate signals
        strat_cls = get_signal_generator(strat_name)
        params = {"name": strat_name}
        generator = strat_cls(params)
        df_signals = generator.generate_signals(df)

        # Run backtest
        engine = BacktestEngine(cfg)
        result = engine.run(df_signals)
        results[strat_name] = result

        m = result.metrics
        print(f"  {strat_name:<20} return={m['total_return']:>+7.2f}%  "
              f"sharpe={str(m.get('sharpe_ratio', 'N/A')):>7}  "
              f"trades={m['total_trades']:>3}")

    # Print comparison table
    print("\n" + str(comparison_table(results).to_string()))

    # Plot comparison
    if args.plot:
        plot_strategy_comparison(results, output_path="results/strategy_comparison.png")
        print("[VIZ] Comparison chart saved: results/strategy_comparison.png")


def cmd_optimize(args):
    """Run parameter optimization (grid or bayesian)."""
    cfg = load_config(args.config)
    setup_logging(cfg)

    from qtrade.data.fetcher import DataFetcher
    from qtrade.strategy.registry import get_signal_generator
    from qtrade.backtest.engine import BacktestEngine
    from qtrade.optimization.grid_search import GridSearchOptimizer
    from qtrade.optimization.bayesian import BayesianOptimizer

    opt_cfg = cfg.get("optimization", {})
    method = opt_cfg.get("method", args.method or "grid")

    # Fetch data
    data_cfg = cfg["data"]
    fetcher = DataFetcher(cfg)
    df = fetcher.fetch(data_cfg["symbol"], data_cfg["start_date"], data_cfg.get("end_date"))
    print(f"[DATA] {data_cfg['symbol']}: {len(df)} rows")

    # Build objective function: runs backtest and returns total_return
    def objective(strategy_cls, params):
        gen = strategy_cls({"name": cfg["strategy"]["name"], **params})
        df_sig = gen.generate_signals(df)
        engine = BacktestEngine(cfg)
        result = engine.run(df_sig)
        return result.metrics.get("total_return", -999)

    param_space = opt_cfg.get("param_grid", opt_cfg.get("param_space", {}))
    if not param_space:
        print("[ERROR] No param_grid or param_space in optimization config.")
        return

    strat_cls = get_signal_generator(cfg["strategy"]["name"])

    if method == "bayesian":
        opt = BayesianOptimizer(strat_cls, param_space, objective, direction="maximize")
    else:
        opt = GridSearchOptimizer(strat_cls, param_space, objective)

    print(f"[OPT] Running {method} optimization...")
    best = opt.optimize(df)
    print(f"[OPT] Best params: {best.get('best_params', best)}")
    print(f"[OPT] Best score: {best.get('best_score', 'N/A')}")


def cmd_live(args):
    """Start live / paper trading."""
    cfg = load_config(args.config)
    setup_logging(cfg)

    from qtrade.live_trading.broker import MockBroker
    from qtrade.live_trading.data_feed import RealtimeDataFeed
    from qtrade.live_trading.live_trader import LiveTrader
    from qtrade.live_trading.risk_monitor import RiskMonitor
    from qtrade.strategy.registry import get_signal_generator

    data_cfg = cfg["data"]
    symbols = args.symbols or [data_cfg.get("symbol", "000001")]
    if isinstance(symbols, str):
        symbols = symbols.split(",")

    strat_cfg = cfg["strategy"]
    strat_cls = get_signal_generator(strat_cfg["name"])
    strategy = strat_cls({**strat_cfg.get("params", {}), "name": strat_cfg["name"]})

    broker = MockBroker(initial_capital=cfg.get("backtest", {}).get("initial_capital", 1000000))
    data_feed = RealtimeDataFeed(symbols)
    risk_monitor = RiskMonitor()

    trader = LiveTrader(
        broker=broker,
        data_feed=data_feed,
        strategy=strategy,
        risk_monitor=risk_monitor,
    )
    print(f"[LIVE] Starting paper trading for {symbols}...")
    trader.start(symbols)


def cmd_report(args):
    """Generate an HTML report from a results equity curve CSV or backtest-id."""
    from pathlib import Path
    import pandas as pd

    output = args.output or "results/quantstats_report.html"

    if args.equity_csv:
        equity = pd.read_csv(args.equity_csv, index_col=0, parse_dates=True)
        if equity.shape[1] >= 1:
            equity = equity.iloc[:, 0]
    else:
        print("[ERROR] Specify --equity-csv <path> or an equity curve file.")
        return

    from qtrade.backtest.report import generate_quantstats_report
    title = args.title or "qtrade Strategy Report"
    path = generate_quantstats_report(equity, output_path=output, title=title)
    print(f"[REPORT] Saved: {path}")


def main():
    parser = argparse.ArgumentParser(description="qtrade â€?A-share quant framework")
    sub = parser.add_subparsers(dest="command")

    # backtest
    bt_parser = sub.add_parser("backtest", help="Run backtest")
    bt_parser.add_argument("--config", "-c", default="configs/quick.yaml")
    bt_parser.add_argument("--plot", action="store_true", help="Generate charts")
    bt_parser.add_argument("--report", action="store_true", help="Generate QuantStats report")

    # train
    tr_parser = sub.add_parser("train", help="Train ML model")
    tr_parser.add_argument("--config", "-c", default="configs/ml_xgboost.yaml")

    # compare
    cmp_parser = sub.add_parser("compare", help="Compare multiple strategies")
    cmp_parser.add_argument("--config", "-c", default="configs/quick.yaml")
    cmp_parser.add_argument("--strategies", "-s", nargs="+",
                           help="Strategy names (default: all registered)")
    cmp_parser.add_argument("--plot", action="store_true", help="Generate comparison chart")

    # optimize
    opt_parser = sub.add_parser("optimize", help="Run parameter optimization")
    opt_parser.add_argument("--config", "-c", default="configs/optimization_example.yaml")
    opt_parser.add_argument("--method", "-m", choices=["grid", "bayesian"],
                           help="Optimization method (default: from config)")

    # live
    live_parser = sub.add_parser("live", help="Start live/paper trading")
    live_parser.add_argument("--config", "-c", default="configs/live_trading_example.yaml")
    live_parser.add_argument("--symbols", "-s", nargs="+",
                           help="Symbols to trade (e.g. 600519 000001)")

    # report
    rpt_parser = sub.add_parser("report", help="Generate HTML report from equity curve")
    rpt_parser.add_argument("--equity-csv", help="Path to equity curve CSV")
    rpt_parser.add_argument("--output", "-o", default="results/quantstats_report.html",
                           help="Output HTML path")
    rpt_parser.add_argument("--title", "-t", help="Report title")

    args = parser.parse_args()

    if args.command == "backtest":
        cmd_backtest(args)
    elif args.command == "train":
        cmd_train(args)
    elif args.command == "compare":
        cmd_compare(args)
    elif args.command == "optimize":
        cmd_optimize(args)
    elif args.command == "live":
        cmd_live(args)
    elif args.command == "report":
        cmd_report(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
