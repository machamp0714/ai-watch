from pathlib import Path

import httpx
import pytest

from ai_watch.adapters.base import FetchContext


@pytest.fixture
def make_ctx(tmp_path: Path):
    """responder(request) -> httpx.Response を渡すと、ネットワークに出ない FetchContext を返す。"""
    def _make(responder, runner=None) -> FetchContext:
        client = httpx.Client(transport=httpx.MockTransport(responder), follow_redirects=True)
        return FetchContext(http=client, data_dir=tmp_path / "data", root=tmp_path, runner=runner)
    return _make
