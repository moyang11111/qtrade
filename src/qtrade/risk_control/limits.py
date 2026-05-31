"""Position limit controls."""
from typing import Dict, Optional
from loguru import logger


class PositionLimits:
    """Position limit controls for single stocks."""

    def __init__(self, max_position_pct: float = 0.1,
                 max_position_value: Optional[float] = None,
                 max_shares: Optional[int] = None):
        """Initialize position limits.

        Args:
            max_position_pct: Maximum position as fraction of portfolio (0-1)
            max_position_value: Maximum position value in currency
            max_shares: Maximum number of shares
        """
        self.max_position_pct = max_position_pct
        self.max_position_value = max_position_value
        self.max_shares = max_shares

    def check_limit(self, symbol: str, current_shares: int,
                   proposed_shares: int, current_price: float,
                   portfolio_value: float) -> Dict:
        """Check if proposed position violates limits.

        Args:
            symbol: Stock symbol
            current_shares: Current shares held
            proposed_shares: Proposed shares to buy/sell
            current_price: Current stock price
            portfolio_value: Total portfolio value

        Returns:
            Dictionary with limit check results
        """
        new_shares = current_shares + proposed_shares
        new_value = new_shares * current_price

        # Check percentage limit
        pct_limit_ok = True
        if self.max_position_pct is not None:
            new_pct = new_value / portfolio_value if portfolio_value > 0 else 0
            pct_limit_ok = new_pct <= self.max_position_pct

        # Check value limit
        value_limit_ok = True
        if self.max_position_value is not None:
            value_limit_ok = new_value <= self.max_position_value

        # Check shares limit
        shares_limit_ok = True
        if self.max_shares is not None:
            shares_limit_ok = new_shares <= self.max_shares

        # Overall check
        all_ok = pct_limit_ok and value_limit_ok and shares_limit_ok

        result = {
            'symbol': symbol,
            'proposed_shares': proposed_shares,
            'new_shares': new_shares,
            'new_value': new_value,
            'pct_limit_ok': pct_limit_ok,
            'value_limit_ok': value_limit_ok,
            'shares_limit_ok': shares_limit_ok,
            'approved': all_ok,
        }

        if not all_ok:
            logger.warning(f"Position limit violated for {symbol}: {result}")

        return result

    def calculate_max_shares(self, current_price: float,
                            portfolio_value: float,
                            current_shares: int = 0) -> int:
        """Calculate maximum shares allowed.

        Args:
            current_price: Current stock price
            portfolio_value: Total portfolio value
            current_shares: Current shares held

        Returns:
            Maximum additional shares allowed
        """
        max_shares_list = []

        # Percentage limit
        if self.max_position_pct is not None:
            max_value = portfolio_value * self.max_position_pct
            max_shares_from_pct = int(max_value / current_price)
            max_shares_list.append(max_shares_from_pct - current_shares)

        # Value limit
        if self.max_position_value is not None:
            max_shares_from_value = int(self.max_position_value / current_price)
            max_shares_list.append(max_shares_from_value - current_shares)

        # Shares limit
        if self.max_shares is not None:
            max_shares_list.append(self.max_shares - current_shares)

        if not max_shares_list:
            return float('inf')

        return max(0, min(max_shares_list))


class PortfolioLimits:
    """Portfolio-level position limits."""

    def __init__(self, max_total_position_pct: float = 1.0,
                 max_single_position_pct: float = 0.1,
                 max_sector_exposure_pct: float = 0.3,
                 max_correlation: float = 0.8):
        """Initialize portfolio limits.

        Args:
            max_total_position_pct: Maximum total position as fraction of portfolio
            max_single_position_pct: Maximum single position as fraction of portfolio
            max_sector_exposure_pct: Maximum sector exposure as fraction of portfolio
            max_correlation: Maximum correlation between positions
        """
        self.max_total_position_pct = max_total_position_pct
        self.max_single_position_pct = max_single_position_pct
        self.max_sector_exposure_pct = max_sector_exposure_pct
        self.max_correlation = max_correlation

    def check_total_exposure(self, positions: Dict[str, float],
                            portfolio_value: float) -> Dict:
        """Check total portfolio exposure.

        Args:
            positions: Dictionary of symbol -> position value
            portfolio_value: Total portfolio value

        Returns:
            Dictionary with exposure check results
        """
        total_exposure = sum(positions.values())
        exposure_pct = total_exposure / portfolio_value if portfolio_value > 0 else 0

        approved = exposure_pct <= self.max_total_position_pct

        result = {
            'total_exposure': total_exposure,
            'exposure_pct': exposure_pct,
            'max_exposure_pct': self.max_total_position_pct,
            'approved': approved,
        }

        if not approved:
            logger.warning(f"Total exposure limit violated: {exposure_pct:.2%} > {self.max_total_position_pct:.2%}")

        return result

    def check_sector_exposure(self, sector_exposures: Dict[str, float],
                             portfolio_value: float) -> Dict:
        """Check sector exposure limits.

        Args:
            sector_exposures: Dictionary of sector -> exposure value
            portfolio_value: Total portfolio value

        Returns:
            Dictionary with sector exposure check results
        """
        violations = []

        for sector, exposure in sector_exposures.items():
            exposure_pct = exposure / portfolio_value if portfolio_value > 0 else 0
            if exposure_pct > self.max_sector_exposure_pct:
                violations.append({
                    'sector': sector,
                    'exposure_pct': exposure_pct,
                    'limit': self.max_sector_exposure_pct,
                })

        approved = len(violations) == 0

        result = {
            'sector_exposures': sector_exposures,
            'violations': violations,
            'approved': approved,
        }

        if not approved:
            logger.warning(f"Sector exposure limit violated: {len(violations)} sectors")

        return result

    def check_concentration(self, positions: Dict[str, float],
                           portfolio_value: float) -> Dict:
        """Check position concentration.

        Args:
            positions: Dictionary of symbol -> position value
            portfolio_value: Total portfolio value

        Returns:
            Dictionary with concentration check results
        """
        violations = []

        for symbol, value in positions.items():
            pct = value / portfolio_value if portfolio_value > 0 else 0
            if pct > self.max_single_position_pct:
                violations.append({
                    'symbol': symbol,
                    'position_pct': pct,
                    'limit': self.max_single_position_pct,
                })

        approved = len(violations) == 0

        result = {
            'positions': positions,
            'violations': violations,
            'approved': approved,
        }

        if not approved:
            logger.warning(f"Position concentration limit violated: {len(violations)} positions")

        return result
