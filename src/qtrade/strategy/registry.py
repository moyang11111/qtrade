"""Strategy registry — name to class mapping."""

from qtrade.strategy.base import SignalGenerator

_REGISTRY: dict[str, type[SignalGenerator]] = {}


def register(name: str):
    """Decorator to register a signal generator."""
    def wrapper(cls):
        _REGISTRY[name] = cls
        return cls
    return wrapper


def get_signal_generator(name: str) -> type[SignalGenerator]:
    if name not in _REGISTRY:
        available = ", ".join(_REGISTRY.keys())
        raise KeyError(f"Unknown strategy '{name}'. Available: {available}")
    return _REGISTRY[name]


def list_strategies() -> list[str]:
    return list(_REGISTRY.keys())
