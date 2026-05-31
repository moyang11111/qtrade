"""
Simple backtest test - verify qtrade core functionality
"""

def test_imports():
    """Test importing all core modules"""
    print("=" * 60)
    print("Test 1: Import core modules")
    print("=" * 60)

    try:
        from qtrade import load_config, BacktestEngine, DataFetcher
        print("[OK] Imported load_config, BacktestEngine, DataFetcher")
    except Exception as e:
        print(f"[FAIL] Import failed: {e}")
        return False

    try:
        from qtrade.strategy.rule.dual_ma import DualMASignal
        print("[OK] Imported DualMASignal")
    except Exception as e:
        print(f"[FAIL] Import strategy failed: {e}")
        return False

    try:
        from qtrade.features.engine import FeatureEngine
        print("[OK] Imported FeatureEngine")
    except Exception as e:
        print(f"[FAIL] Import features failed: {e}")
        return False

    print("\nAll modules imported successfully!\n")
    return True


def test_data_fetch():
    """Test data fetching"""
    print("=" * 60)
    print("Test 2: Fetch stock data")
    print("=" * 60)

    try:
        from qtrade import DataFetcher

        fetcher = DataFetcher({})
        data = fetcher.fetch(
            symbol="600519",  # Kweichow Moutai
            start="20230101",
            end="20230331"
        )

        print(f"[OK] Fetched {len(data)} rows")
        print(f"  Columns: {list(data.columns)}")
        print(f"  Date range: {data.index[0]} ~ {data.index[-1]}")
        print()

        return data
    except Exception as e:
        print(f"[FAIL] Data fetch failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_strategy(data):
    """Test strategy signal generation"""
    print("=" * 60)
    print("Test 3: Generate trading signals")
    print("=" * 60)

    try:
        from qtrade.strategy.rule.dual_ma import DualMASignal

        strategy = DualMASignal({"fast_period": 5, "slow_period": 20, "name": "dual_ma"})
        signals = strategy.generate_signals(data)

        buy_signals = (signals['signal_action'] == 1).sum()
        sell_signals = (signals['signal_action'] == -1).sum()

        print(f"[OK] Generated signals")
        print(f"  Buy signals: {buy_signals}")
        print(f"  Sell signals: {sell_signals}")
        print()

        return signals
    except Exception as e:
        print(f"[FAIL] Signal generation failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def test_backtest(data):
    """Test backtest engine"""
    print("=" * 60)
    print("Test 4: Run backtest")
    print("=" * 60)

    try:
        from qtrade import BacktestEngine
        from qtrade.strategy.rule.dual_ma import DualMASignal

        # Create simple config
        config = {
            "backtest": {
                "initial_capital": 100000,
                "commission": 0.001,
                "slippage": 0.001,
                "lot_size": 100,
                "stop_loss_pct": 0.15,
                "trail_stop_pct": 0.10,
            }
        }

        # Create strategy
        strategy = DualMASignal({"fast_period": 5, "slow_period": 20, "name": "dual_ma"})

        # Generate signals
        data_with_signals = strategy.generate_signals(data)

        # Run backtest
        engine = BacktestEngine(config)
        result = engine.run(data_with_signals)

        print(f"[OK] Backtest completed")
        print(f"\nPerformance metrics:")
        print(f"  Total return: {result.metrics.get('total_return', 'N/A')}")
        print(f"  Annual return: {result.metrics.get('annual_return', 'N/A')}")
        print(f"  Sharpe ratio: {result.metrics.get('sharpe_ratio', 'N/A')}")
        print(f"  Max drawdown: {result.metrics.get('max_drawdown', 'N/A')}")
        print(f"  Total trades: {result.metrics.get('total_trades', 'N/A')}")
        print(f"  Win rate: {result.metrics.get('win_rate', 'N/A')}")
        print()

        return result
    except Exception as e:
        print(f"[FAIL] Backtest failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("QTrade Framework Verification")
    print("=" * 60 + "\n")

    # Test 1: Imports
    if not test_imports():
        print("\n[ERROR] Import test failed, please check dependencies")
        return False

    # Test 2: Data fetch
    data = test_data_fetch()
    if data is None:
        print("\n[ERROR] Data fetch failed")
        return False

    # Test 3: Signal generation
    signals = test_strategy(data)
    if signals is None:
        print("\n[ERROR] Signal generation failed")
        return False

    # Test 4: Backtest
    result = test_backtest(data)
    if result is None:
        print("\n[ERROR] Backtest failed")
        return False

    # All tests passed
    print("=" * 60)
    print("[SUCCESS] All tests passed! QTrade is working correctly")
    print("=" * 60)

    return True


if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)
