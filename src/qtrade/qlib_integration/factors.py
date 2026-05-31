"""Factor management system compatible with Qlib."""
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
import pandas as pd
import numpy as np
from loguru import logger


@dataclass
class Factor:
    """Factor definition."""
    name: str
    expression: str  # Qlib expression language
    description: str = ""
    category: str = "custom"
    lookback: int = 20
    parameters: Dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'expression': self.expression,
            'description': self.description,
            'category': self.category,
            'lookback': self.lookback,
            'parameters': self.parameters,
            'created_at': self.created_at,
            'tags': self.tags,
        }


class FactorManager:
    """Manage and compute factors."""

    def __init__(self):
        self.factors: Dict[str, Factor] = {}
        self._register_builtin_factors()

    def _register_builtin_factors(self):
        """Register common built-in factors."""
        # Price factors
        self.register(Factor(
            name='close',
            expression='$close',
            description='Close price',
            category='price',
            lookback=0,
        ))

        self.register(Factor(
            name='volume',
            expression='$volume',
            description='Volume',
            category='volume',
            lookback=0,
        ))

        # Moving averages
        self.register(Factor(
            name='ma5',
            expression='Mean($close, 5)',
            description='5-day moving average',
            category='technical',
            lookback=5,
        ))

        self.register(Factor(
            name='ma20',
            expression='Mean($close, 20)',
            description='20-day moving average',
            category='technical',
            lookback=20,
        ))

        # Returns
        self.register(Factor(
            name='return_1d',
            expression='$close / Ref($close, 1) - 1',
            description='1-day return',
            category='momentum',
            lookback=1,
        ))

        self.register(Factor(
            name='return_5d',
            expression='$close / Ref($close, 5) - 1',
            description='5-day return',
            category='momentum',
            lookback=5,
        ))

        # Volatility
        self.register(Factor(
            name='volatility_20d',
            expression='Std($close / Ref($close, 1) - 1, 20)',
            description='20-day volatility',
            category='volatility',
            lookback=20,
        ))

        # RSI
        self.register(Factor(
            name='rsi_14',
            expression='RSI($close, 14)',
            description='14-day RSI',
            category='technical',
            lookback=14,
        ))

        logger.info(f"Registered {len(self.factors)} built-in factors")

    def register(self, factor: Factor) -> None:
        """Register a factor."""
        self.factors[factor.name] = factor
        logger.debug(f"Registered factor: {factor.name}")

    def get(self, name: str) -> Optional[Factor]:
        """Get factor by name."""
        return self.factors.get(name)

    def list_factors(self, category: Optional[str] = None) -> List[Factor]:
        """List factors with optional category filter."""
        factors = list(self.factors.values())
        if category:
            factors = [f for f in factors if f.category == category]
        return factors

    def compute(self, df: pd.DataFrame, factor_name: str) -> pd.Series:
        """Compute factor from DataFrame.

        This is a simplified local computation. For full Qlib expressions,
        use QlibAdapter.get_data().
        """
        factor = self.get(factor_name)
        if not factor:
            raise ValueError(f"Factor '{factor_name}' not registered")

        # Simple expression parser for common patterns
        expr = factor.expression

        if expr.startswith('$'):
            # Direct field reference
            field_name = expr[1:]
            return df[field_name]

        elif expr.startswith('Mean('):
            # Moving average
            parts = expr[5:-1].split(',')
            field = parts[0].strip()[1:]  # Remove $
            window = int(parts[1].strip())
            return df[field].rolling(window).mean()

        elif expr.startswith('Std('):
            # Standard deviation
            parts = expr[4:-1].split(',')
            inner_expr = parts[0].strip()
            window = int(parts[1].strip())

            if '/' in inner_expr and 'Ref(' in inner_expr:
                # Return volatility
                return df['close'].pct_change().rolling(window).std()
            else:
                field = inner_expr[1:]
                return df[field].rolling(window).std()

        elif 'Ref(' in expr:
            # Reference to past value
            if '/' in expr and '- 1' in expr:
                # Return calculation
                parts = expr.split('/')
                field = parts[0].strip()[1:]
                lag = int(parts[1].split('(')[1].split(',')[1].split(')')[0].strip())
                return df[field] / df[field].shift(lag) - 1

        elif expr.startswith('RSI('):
            # RSI
            parts = expr[4:-1].split(',')
            field = parts[0].strip()[1:]
            window = int(parts[1].strip())
            delta = df[field].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window).mean()
            rs = gain / loss
            return 100 - (100 / (1 + rs))

        raise NotImplementedError(f"Cannot parse expression: {expr}")

    def compute_multiple(self, df: pd.DataFrame,
                        factor_names: List[str]) -> pd.DataFrame:
        """Compute multiple factors."""
        results = {}
        for name in factor_names:
            try:
                results[name] = self.compute(df, name)
            except Exception as e:
                logger.warning(f"Failed to compute factor '{name}': {e}")

        return pd.DataFrame(results, index=df.index)

    def get_expressions(self, factor_names: List[str]) -> List[str]:
        """Get Qlib expressions for factors."""
        expressions = []
        for name in factor_names:
            factor = self.get(name)
            if factor:
                expressions.append(factor.expression)
            else:
                logger.warning(f"Factor '{name}' not found")
        return expressions

    def validate_expression(self, expression: str) -> bool:
        """Validate Qlib expression syntax."""
        # Basic validation
        try:
            # Check for balanced parentheses
            if expression.count('(') != expression.count(')'):
                return False

            # Check for valid field references
            if '$' in expression:
                parts = expression.split('$')
                for part in parts[1:]:
                    field = part.split(',')[0].split(')')[0].strip()
                    if not field.isalnum() and field not in ['close', 'open', 'high', 'low', 'volume']:
                        return False

            return True
        except:
            return False

    def export_definitions(self, output_path: str) -> None:
        """Export factor definitions to JSON."""
        import json

        data = {
            'factors': {name: factor.to_dict() for name, factor in self.factors.items()},
            'exported_at': datetime.now().isoformat(),
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        logger.info(f"Exported {len(self.factors)} factor definitions to {output_path}")

    def import_definitions(self, input_path: str) -> None:
        """Import factor definitions from JSON."""
        import json

        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        for name, factor_data in data.get('factors', {}).items():
            factor = Factor(**factor_data)
            self.register(factor)

        logger.info(f"Imported {len(data.get('factors', {}))} factor definitions")

    def summary(self) -> Dict:
        """Get factor manager summary."""
        categories = {}
        for factor in self.factors.values():
            cat = factor.category
            categories[cat] = categories.get(cat, 0) + 1

        return {
            'total_factors': len(self.factors),
            'categories': categories,
        }
