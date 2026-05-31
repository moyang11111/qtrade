"""Qlib adapter for seamless integration."""
from typing import Dict, List, Optional, Union
from pathlib import Path
import pandas as pd
import numpy as np
from loguru import logger


class QlibAdapter:
    """Adapter for Microsoft Qlib integration."""

    def __init__(self, qlib_data_path: Optional[str] = None):
        self.qlib_data_path = Path(qlib_data_path) if qlib_data_path else None
        self._initialized = False

    def initialize(self, provider_uri: str = '~/.qlib/qlib_data/cn_data',
                  region: str = 'cn') -> bool:
        """Initialize Qlib.

        Args:
            provider_uri: Path to Qlib data
            region: Market region ('cn' for China, 'us' for US)
        """
        try:
            import qlib
            from qlib.config import REG_CN, REG_US

            region_config = REG_CN if region == 'cn' else REG_US

            qlib.init(
                provider_uri=provider_uri,
                region=region_config,
            )
            self._initialized = True
            logger.info(f"Qlib initialized with region={region}, provider={provider_uri}")
            return True
        except ImportError:
            logger.error("Qlib not installed. Install with: pip install pyqlib")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize Qlib: {e}")
            return False

    def is_initialized(self) -> bool:
        """Check if Qlib is initialized."""
        return self._initialized

    def get_data(self, instruments: Union[str, List[str]],
                fields: List[str],
                start_time: str,
                end_time: str) -> pd.DataFrame:
        """Get data from Qlib.

        Args:
            instruments: Stock symbols or instrument pool
            fields: List of field names (factors)
            start_time: Start date (YYYY-MM-DD)
            end_time: End date (YYYY-MM-DD)
        """
        if not self._initialized:
            raise RuntimeError("Qlib not initialized. Call initialize() first.")

        try:
            from qlib.data import D

            if isinstance(instruments, str):
                instruments = [instruments]

            df = D.features(
                instruments,
                fields,
                start_time=start_time,
                end_time=end_time,
            )

            logger.info(f"Retrieved {len(df)} rows for {len(instruments)} instruments")
            return df

        except Exception as e:
            logger.error(f"Failed to get data from Qlib: {e}")
            raise

    def get_instruments(self, market: str = 'csi300',
                       start_time: Optional[str] = None,
                       end_time: Optional[str] = None) -> List[str]:
        """Get instrument list from Qlib.

        Args:
            market: Market name (csi300, csi500, etc.)
            start_time: Start date for dynamic pools
            end_time: End date for dynamic pools
        """
        if not self._initialized:
            raise RuntimeError("Qlib not initialized.")

        try:
            from qlib.data import D

            instruments = D.instruments(
                market=market,
                as_list=True,
            )

            logger.info(f"Retrieved {len(instruments)} instruments from {market}")
            return instruments

        except Exception as e:
            logger.error(f"Failed to get instruments: {e}")
            raise

    def convert_to_qlib_format(self, df: pd.DataFrame,
                              symbol: str) -> pd.DataFrame:
        """Convert DataFrame to Qlib format.

        Qlib expects MultiIndex (datetime, instrument).
        """
        df_qlib = df.copy()

        # Ensure datetime index
        if not isinstance(df_qlib.index, pd.DatetimeIndex):
            raise ValueError("DataFrame must have DatetimeIndex")

        # Create MultiIndex
        df_qlib['instrument'] = symbol
        df_qlib = df_qlib.set_index('instrument', append=True)
        df_qlib = df_qlib.reorder_levels(['instrument', 'datetime'])

        return df_qlib

    def save_to_qlib(self, df: pd.DataFrame, symbol: str,
                    qlib_data_dir: str) -> None:
        """Save data in Qlib format.

        Args:
            df: DataFrame with OHLCV data
            symbol: Stock symbol
            qlib_data_dir: Directory to save Qlib data
        """
        try:
            from qlib.data.storage import CalendarProvider, InstrumentProvider
            from qlib.data.cache import H

            output_dir = Path(qlib_data_dir) / symbol
            output_dir.mkdir(parents=True, exist_ok=True)

            # Convert to Qlib format
            df_qlib = self.convert_to_qlib_format(df, symbol)

            # Save each column as separate file
            for col in df_qlib.columns:
                if col == 'instrument':
                    continue
                col_path = output_dir / f"{col}.day.bin"
                df_qlib[col].to_pickle(str(col_path))

            logger.info(f"Saved {symbol} data to Qlib format at {output_dir}")

        except Exception as e:
            logger.error(f"Failed to save to Qlib format: {e}")
            raise

    def list_available_factors(self) -> List[str]:
        """List all available factors in Qlib."""
        if not self._initialized:
            raise RuntimeError("Qlib not initialized.")

        try:
            from qlib.data import D

            # Get all fields from a sample instrument
            sample_data = D.features(['SH600000'], ['*'], start_time='2020-01-01', end_time='2020-01-02')
            factors = list(sample_data.columns)

            logger.info(f"Found {len(factors)} available factors")
            return factors

        except Exception as e:
            logger.warning(f"Could not list factors: {e}")
            return []
