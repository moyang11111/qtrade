"""Broker configuration — parameterized broker settings."""

from dataclasses import dataclass, field


@dataclass
class BrokerConfig:
    """Comprehensive broker configuration for A-share markets."""

    # Capital
    initial_capital: float = 100000.0

    # Commission
    commission: float = 0.0003  # 万三 (0.03%)
    min_commission: float = 5.0  # 最低佣金 5 元
    commission_type: str = "percentage"  # percentage, fixed

    # Stamp duty (印花税，仅卖出时收取)
    stamp_duty: float = 0.001  # 千分之一 (0.1%)
    stamp_duty_side: str = "sell"  # sell (仅卖出), both (买卖都收)

    # Slippage (滑点)
    slippage: float = 0.001  # 0.1%
    slippage_type: str = "percentage"  # percentage, fixed

    # A-share specific
    lot_size: int = 100  # 最小交易单位（手）
    t_plus_n: int = 1  # T+1 交易制度

    # Risk management
    stop_loss_pct: float = 0.15  # 15% 止损
    trail_stop_pct: float = 0.10  # 10% 移动止损
    max_position_pct: float = 0.95  # 最大仓位比例

    @classmethod
    def from_dict(cls, d: dict) -> "BrokerConfig":
        """Create BrokerConfig from dict, ignoring unknown keys."""
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)

    def to_dict(self) -> dict:
        """Convert to dict for serialization."""
        from dataclasses import asdict
        return asdict(self)


@dataclass
class PositionSizingConfig:
    """Position sizing configuration."""

    method: str = "strength"  # fixed, strength, atr, kelly

    # Fixed sizing
    fixed_pct: float = 0.95  # 固定仓位比例

    # Strength-based sizing
    min_strength: float = 0.3  # 信号强度最低阈值
    scale_with_strength: bool = True

    # ATR-based sizing (risk-based)
    risk_per_trade: float = 0.02  # 每笔交易风险 2%
    atr_period: int = 14
    atr_stop_mult: float = 2.0

    # Kelly criterion
    kelly_fraction: float = 0.5  # 半 Kelly

    @classmethod
    def from_dict(cls, d: dict) -> "PositionSizingConfig":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)
