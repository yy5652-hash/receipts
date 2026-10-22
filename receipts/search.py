"""Web access: Tavily search/extract with a disk cache, plus a link-status probe."""

import hashlib
import json
import time
from pathlib import Path
from collections.abc import Callable
from typing import Protocol

import httpx

TAVILY_URL = "https://api.tavily.com"
_UA = {"User-Agent": "Mozilla/5.0 (compatible; ReceiptsBot/0.1)"}
_MISS = object()


class Searcher(Protocol):
    def extract(self, urls: list[str]) -> dict[str, str | None]:
        """url -> page text, or None if extraction failed."""
        ...

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        """Dicts with url, title, content, raw_content."""
        ...


class DiskCache:
    """JSON values stored under the sha256 of a key string."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def _path(self, key: str) -> Path:
        return self.dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def get(self, key: str, default=None):
        try:
            return json.loads(self._path(key).read_text(encoding="utf-8"))["value"]
        except (OSError, ValueError, KeyError):
            return default

    def set(self, key: str, value) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(key).write_text(json.dumps({"key": key, "value": value}), encoding="utf-8")


class TavilySearcher:
    """Tavily REST client. Every call is cached, so each URL / query is fetched once."""

    def __init__(self, api_key: str, cache: DiskCache | None = None, client: httpx.Client | None = None,
                 on_credits: Callable[[float], None] | None = None):
        self._key = api_key
        self.cache = cache
        self.on_credits = on_credits  # told the Tavily credits each network call costs (cache hits are free)
        self.client = client or httpx.Client(timeout=60)

    def _post(self, path: str, body: dict) -> dict | None:
        """POST with backoff on 429/5xx/transport errors; auth errors raise, other failures -> None."""
        headers = {"Authorization": f"Bearer {self._key}"}
        for attempt in range(3):
            try:
                r = self.client.post(f"{TAVILY_URL}{path}", json=body, headers=headers)
            except httpx.TransportError:
                r = None
            if r is not None:
                if r.status_code in (401, 403):
                    raise RuntimeError(f"Tavily rejected the API key (HTTP {r.status_code}); check TAVILY_API_KEY")
                if r.status_code == 200:
                    return r.json()
                if r.status_code not in (429, 500, 502, 503, 504):
                    return None
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        return None

    def extract(self, urls: list[str]) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        todo: list[str] = []
        for u in dict.fromkeys(urls):
            hit = self.cache.get(f"extract:{u}", _MISS) if self.cache else _MISS
            if hit is _MISS:
                todo.append(u)
            else:
                out[u] = hit
        for i in range(0, len(todo), 20):
            batch = todo[i : i + 20]
            data = self._post("/extract", {"urls": batch, "extract_depth": "basic"})
            if data is not None and self.on_credits:
                self.on_credits(-(-len(batch) // 5))  # basic extract: 1 credit per 5 URLs
            if data is None:  # transient failure: report None but do not cache it
                out.update({u: None for u in batch})
                continue
            got = {r.get("url", "").rstrip("/"): r.get("raw_content") for r in data.get("results", [])}
            for u in batch:
                text = got.get(u.rstrip("/")) or None
                out[u] = text
                if self.cache:
                    self.cache.set(f"extract:{u}", text)
        return out

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        key = f"search:{max_results}:{query}"
        hit = self.cache.get(key, _MISS) if self.cache else _MISS
        if hit is not _MISS:
            return hit
        data = self._post(
            "/search",
            {"query": query, "max_results": max_results, "search_depth": "basic", "include_raw_content": True},
        )
        if data is None:
            return []
        if self.on_credits:
            self.on_credits(1)  # basic search: 1 credit
        results = [
            {k: r.get(k) for k in ("url", "title", "content", "raw_content")} for r in data.get("results", [])
        ]
        if self.cache:
            self.cache.set(key, results)
        return results


def http_status(url: str, client: httpx.Client | None = None) -> int | None:
    """HTTP status of a URL (HEAD, then GET fallback); None on DNS/connection errors."""
    own = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=10, headers=_UA)
    try:
        try:
            status = client.head(url).status_code
            if status < 400:
                return status
        except httpx.HTTPError:
            pass
        try:
            with client.stream("GET", url) as r:
                return r.status_code
        except httpx.HTTPError:
            return None
    finally:
        if own:
            client.close()
