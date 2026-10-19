import json

import httpx
import pytest

from receipts.search import DiskCache, TavilySearcher, http_status


def _searcher(tmp_path, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return TavilySearcher("k", DiskCache(tmp_path), client=client)


def test_diskcache_roundtrip(tmp_path):
    c = DiskCache(tmp_path / "c")
    assert c.get("a") is None
    c.set("a", {"x": [1]})
    c.set("n", None)
    assert c.get("a") == {"x": [1]} and c.get("n", "miss") is None


def test_extract_is_batched_cached_and_authorised(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        body = json.loads(req.content)
        assert req.headers["authorization"] == "Bearer k"
        res = [{"url": u, "raw_content": f"text of {u}"} for u in body["urls"] if "bad" not in u]
        return httpx.Response(200, json={"results": res, "failed_results": [{"url": "https://bad"}]})

    s = _searcher(tmp_path, handler)
    out = s.extract(["https://a.com/x", "https://bad"])
    assert out == {"https://a.com/x": "text of https://a.com/x", "https://bad": None}
    s.extract(["https://a.com/x", "https://bad"])
    assert len(calls) == 1  # second call fully served from the cache


def test_search_cached_and_auth_error(tmp_path):
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(200, json={"results": [{"url": "u", "title": "t", "content": "c", "raw_content": None, "score": 1}]})

    s = _searcher(tmp_path, handler)
    assert s.search("q") == [{"url": "u", "title": "t", "content": "c", "raw_content": None}]
    s.search("q")
    assert len(n) == 1
    bad = _searcher(tmp_path / "other", lambda req: httpx.Response(401))
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        bad.search("q")


def test_http_status_head_then_get():
    def handler(req):
        if req.url.path == "/gone":
            return httpx.Response(404)
        if req.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(200)

    c = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    assert http_status("https://x.test/ok", client=c) == 200
    assert http_status("https://x.test/gone", client=c) == 404

    def boom(req):
        raise httpx.ConnectError("dns")

    c2 = httpx.Client(transport=httpx.MockTransport(boom))
    assert http_status("https://nowhere.invalid/", client=c2) is None
