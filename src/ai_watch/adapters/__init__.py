from .base import Adapter, FetchContext, TimeWindow
from .rss import RssAdapter
from .github_releases import GithubReleasesAdapter
from .github_sections import GithubFileSectionsAdapter
from .hn_algolia import HnAlgoliaAdapter
from .note_search import NoteSearchAdapter
from .html_diff import HtmlDiffAdapter

ADAPTERS: dict[str, Adapter] = {
    "rss": RssAdapter(),
    "github_releases": GithubReleasesAdapter(),
    "github_file_sections": GithubFileSectionsAdapter(),
    "hn_algolia": HnAlgoliaAdapter(),
    "note_search": NoteSearchAdapter(),
    "html_diff": HtmlDiffAdapter(),
}

__all__ = ["ADAPTERS", "Adapter", "FetchContext", "TimeWindow"]
