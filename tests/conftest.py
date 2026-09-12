from pathlib import Path

import httpx
import pytest

from ai_watch.adapters.base import FetchContext


@pytest.fixture(autouse=True)
def _isolate_ai_watch_env(monkeypatch):
    for k in ("AI_WATCH_VAULT_DIR", "AI_WATCH_DATA_DIR", "AI_WATCH_CONFIG",
              "AI_WATCH_WATCHLIST_FILE"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def make_ctx(tmp_path: Path):
    """responder(request) -> httpx.Response を渡すと、ネットワークに出ない FetchContext を返す。"""
    def _make(responder, runner=None) -> FetchContext:
        client = httpx.Client(transport=httpx.MockTransport(responder), follow_redirects=True)
        return FetchContext(http=client, data_dir=tmp_path / "data", root=tmp_path, runner=runner)
    return _make
