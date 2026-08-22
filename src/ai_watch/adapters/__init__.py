from .base import Adapter, FetchContext, TimeWindow
from .rss import RssAdapter
from .github_releases import GithubReleasesAdapter
from .github_sections import GithubFileSectionsAdapter

ADAPTERS: dict[str, Adapter] = {
    "rss": RssAdapter(),
    "github_releases": GithubReleasesAdapter(),
    "github_file_sections": GithubFileSectionsAdapter(),
}

__all__ = ["ADAPTERS", "Adapter", "FetchContext", "TimeWindow"]
