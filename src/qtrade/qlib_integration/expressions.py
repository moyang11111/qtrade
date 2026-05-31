"""Expression builder for Qlib factor expressions."""
from typing import List, Optional


class ExpressionBuilder:
    """Builder for Qlib expression language."""

    @staticmethod
    def field(name: str) -> str:
        """Reference a field."""
        return f"${name}"

    @staticmethod
    def ref(expression: str, n: int) -> str:
        """Reference past value (n days ago)."""
        return f"Ref({expression}, {n})"

    @staticmethod
    def mean(expression: str, window: int) -> str:
        """Moving average."""
        return f"Mean({expression}, {window})"

    @staticmethod
    def std(expression: str, window: int) -> str:
        """Moving standard deviation."""
        return f"Std({expression}, {window})"

    @staticmethod
    def max(expression: str, window: int) -> str:
        """Moving maximum."""
        return f"Max({expression}, {window})"

    @staticmethod
    def min(expression: str, window: int) -> str:
        """Moving minimum."""
        return f"Min({expression}, {window})"

    @staticmethod
    def sum(expression: str, window: int) -> str:
        """Moving sum."""
        return f"Sum({expression}, {window})"

    @staticmethod
    def rank(expression: str) -> str:
        """Cross-sectional rank."""
        return f"Rank({expression})"

    @staticmethod
    def corr(expr1: str, expr2: str, window: int) -> str:
        """Rolling correlation."""
        return f"Corr({expr1}, {expr2}, {window})"

    @staticmethod
    def cov(expr1: str, expr2: str, window: int) -> str:
        """Rolling covariance."""
        return f"Cov({expr1}, {expr2}, {window})"

    @staticmethod
    def rsi(expression: str, window: int = 14) -> str:
        """RSI indicator."""
        return f"RSI({expression}, {window})"

    @staticmethod
    def macd(expression: str,
            fast: int = 12,
            slow: int = 26,
            signal: int = 9) -> str:
        """MACD indicator."""
        return f"MACD({expression}, {fast}, {slow}, {signal})"

    @staticmethod
    def slope(expression: str, window: int) -> str:
        """Linear regression slope."""
        return f"Slope({expression}, {window})"

    @staticmethod
    def intercept(expression: str, window: int) -> str:
        """Linear regression intercept."""
        return f"Intercept({expression}, {window})"

    @staticmethod
    def rvalue(expression: str, window: int) -> str:
        """R-squared value."""
        return f"Rvalue({expression}, {window})"

    @staticmethod
    def return_n(expression: str, n: int) -> str:
        """N-day return."""
        return f"{expression} / Ref({expression}, {n}) - 1"

    @staticmethod
    def volatility(expression: str, window: int) -> str:
        """Volatility (std of returns)."""
        ret_expr = ExpressionBuilder.return_n(expression, 1)
        return f"Std({ret_expr}, {window})"

    @staticmethod
    def zscore(expression: str, window: int) -> str:
        """Z-score normalization."""
        mean_expr = ExpressionBuilder.mean(expression, window)
        std_expr = ExpressionBuilder.std(expression, window)
        return f"({expression} - {mean_expr}) / {std_expr}"

    @staticmethod
    def drawdown(expression: str, window: int) -> str:
        """Drawdown from rolling high."""
        max_expr = ExpressionBuilder.max(expression, window)
        return f"({expression} - {max_expr}) / {max_expr}"

    @staticmethod
    def turnover_rate(volume: str, total_shares: str) -> str:
        """Turnover rate."""
        return f"{volume} / {total_shares}"

    @staticmethod
    def vwap(high: str, low: str, close: str, volume: str) -> str:
        """Volume-weighted average price."""
        return f"({high} + {low} + {close}) / 3 * {volume}"

    @staticmethod
    def atr(high: str, low: str, close: str, window: int = 14) -> str:
        """Average True Range."""
        tr = f"Max({high} - {low}, Max(Abs({high} - Ref({close}, 1)), Abs({low} - Ref({close}, 1))))"
        return f"Mean({tr}, {window})"

    @staticmethod
    def bollinger_upper(expression: str, window: int = 20, n_std: float = 2.0) -> str:
        """Bollinger Band upper."""
        mean_expr = ExpressionBuilder.mean(expression, window)
        std_expr = ExpressionBuilder.std(expression, window)
        return f"{mean_expr} + {n_std} * {std_expr}"

    @staticmethod
    def bollinger_lower(expression: str, window: int = 20, n_std: float = 2.0) -> str:
        """Bollinger Band lower."""
        mean_expr = ExpressionBuilder.mean(expression, window)
        std_expr = ExpressionBuilder.std(expression, window)
        return f"{mean_expr} - {n_std} * {std_expr}"

    @staticmethod
    def momentum(expression: str, window: int) -> str:
        """Momentum (price change)."""
        return f"{expression} - Ref({expression}, {window})"

    @staticmethod
    def williams_r(high: str, low: str, close: str, window: int = 14) -> str:
        """Williams %R."""
        hh = ExpressionBuilder.max(high, window)
        ll = ExpressionBuilder.min(low, window)
        return f"-100 * ({hh} - {close}) / ({hh} - {ll})"

    @staticmethod
    def stochastic_k(high: str, low: str, close: str, window: int = 14) -> str:
        """Stochastic %K."""
        hh = ExpressionBuilder.max(high, window)
        ll = ExpressionBuilder.min(low, window)
        return f"100 * ({close} - {ll}) / ({hh} - {ll})"

    @staticmethod
    def combine(*expressions: str, operation: str = '+') -> str:
        """Combine multiple expressions."""
        return f" {operation} ".join(expressions)

    @staticmethod
    def conditional(condition: str, true_expr: str, false_expr: str) -> str:
        """Conditional expression."""
        return f"If({condition}, {true_expr}, {false_expr})"

    @staticmethod
    def abs(expression: str) -> str:
        """Absolute value."""
        return f"Abs({expression})"

    @staticmethod
    def log(expression: str) -> str:
        """Natural logarithm."""
        return f"Log({expression})"

    @staticmethod
    def power(expression: str, n: int) -> str:
        """Power."""
        return f"Power({expression}, {n})"
