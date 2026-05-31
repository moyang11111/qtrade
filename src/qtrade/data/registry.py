"""DataSource registry — auto-discover and fallback chains."""

import logging

from qtrade.data.source import DataSource

logger = logging.getLogger("qtrade.data.registry")

_SOURCES: dict[str, type[DataSource]] = {}


def register_source(name: str):
    """Decorator to register a DataSource."""
    def wrapper(cls):
        _SOURCES[name] = cls
        return cls
    return wrapper


def get_source(name: str) -> DataSource:
    """Get a DataSource instance by name."""
    if name not in _SOURCES:
        available = ", ".join(_SOURCES.keys())
        raise KeyError(f"Unknown source '{name}'. Available: {available}")
    return _SOURCES[name]()


def list_sources() -> list[str]:
    return list(_SOURCES.keys())


def get_fallback_chain(names: list[str]) -> list[DataSource]:
    """Get ordered list of data sources for fallback."""
    chain = []
    for name in names:
        try:
            src = get_source(name)
            if src.available():
                chain.append(src)
        except Exception:
            logger.warning("Source '%s' not available", name)
    return chain


# Register built-in sources
from qtrade.data.sources.pytdx_source import PytdxSource  # noqa: E402
from qtrade.data.sources.akshare_source import AkShareSource  # noqa: E402

register_source("pytdx")(PytdxSource)
register_source("akshare")(AkShareSource)
