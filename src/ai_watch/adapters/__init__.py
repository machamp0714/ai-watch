from .base import Adapter, FetchContext, TimeWindow
from .rss import RssAdapter

ADAPTERS: dict[str, Adapter] = {
    "rss": RssAdapter(),
}

__all__ = ["ADAPTERS", "Adapter", "FetchContext", "TimeWindow"]
