"""Receipts web app: a FastAPI server that streams an audit as Server-Sent Events."""

import json
import logging
import os
import queue
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from receipts.config import REPO_ROOT, Settings, load_settings
from receipts.models import AuditReport
from receipts.pipeline import audit
from receipts.render import render_markdown
from receipts.search import http_status

log = logging.getLogger("receipts.server")
STATIC_DIR = Path(__file__).resolve().parent / "static"
SAMPLES_DIR = REPO_ROOT / "samples"
EXAMPLE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PAUSED = "live audits are paused; try an example"

# (llm, searcher, status_fn, openalex) for one audit
Runtime = tuple
Factory = Callable[[], Runtime]


def _env_num(name: str, default, cast):
    try:
        return cast(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass
class ServerConfig:
    """Guardrails of the public demo; every field can be set from the environment."""

    max_doc_chars: int = 40_000
    max_claims: int = 60
    per_ip_per_hour: int = 6
    daily_budget_usd: float = 2.0
    max_concurrent: int = 4
    trust_proxy: bool = False  # take the client IP from X-Forwarded-For (behind a reverse proxy)
    budget_file: Path | None = None  # default: <cache_dir>/budget.json

    @classmethod
    def from_env(cls) -> "ServerConfig":
        return cls(
            max_doc_chars=_env_num("RECEIPTS_MAX_DOC_CHARS", 40_000, int),
            max_claims=_env_num("RECEIPTS_MAX_CLAIMS", 60, int),
            per_ip_per_hour=_env_num("RECEIPTS_IP_LIMIT_PER_HOUR", 6, int),
            daily_budget_usd=_env_num("RECEIPTS_DAILY_BUDGET_USD", 2.0, float),
            max_concurrent=_env_num("RECEIPTS_MAX_CONCURRENT", 4, int),
            trust_proxy=os.environ.get("RECEIPTS_TRUST_PROXY", "") not in ("", "0", "false"),
        )


class Budget:
    """USD spent per UTC day, persisted as {date: spent} in a JSON file."""

    def __init__(self, path: Path, limit: float):
        self.path, self.limit = Path(path), limit
        self._lock = threading.Lock()

    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def spent(self) -> float:
        with self._lock:
            return float(self._read().get(self._today(), 0.0))

    def has_room(self) -> bool:
        return self.spent() < self.limit

    def add(self, usd: float) -> None:
        if usd <= 0:
            return
        with self._lock:
            data = self._read()
            day = self._today()
            data[day] = round(float(data.get(day, 0.0)) + usd, 6)
            data = dict(sorted(data.items())[-30:])  # keep a month of history
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            tmp.replace(self.path)


class RateLimiter:
    """At most `limit` audits per key within a sliding window."""

    def __init__(self, limit: int, window: float = 3600.0):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> int | None:
        """Record a hit; return the seconds to wait if the key is over its limit (hit not recorded)."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(1, int(self.window - (now - q[0])) + 1)
            q.append(now)
            for k in [k for k, v in self._hits.items() if not v]:
                del self._hits[k]
            return None


class AuditRequest(BaseModel):
    document: str
    mode: Literal["cascade", "super", "ultra", "nano"] = "cascade"


def _err(status: int, message: str, **headers) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status, headers={k.replace("_", "-"): v for k, v in headers.items()})


def _friendly(exc: Exception) -> str:
    """A message safe to show: never the exception text (it may carry URLs or key hints)."""
    name = type(exc).__name__
    if name in ("AuthenticationError", "PermissionDeniedError") or "rejected the API key" in str(exc):
        return "A model or search service rejected this deployment's credentials. Try an example instead."
    if name in ("RateLimitError", "InternalServerError"):
        return "The model service is busy right now. Try again in a minute."
    if name in ("APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout", "TimeoutException"):
        return "Could not reach the model or search service. Try again in a minute."
    return "Something went wrong while checking this report. Try again, or try an example."


def public_report(report: AuditReport) -> dict:
    """The report as JSON for the browser: fetched page texts are dropped (they can be large)."""
    data = report.model_dump(mode="json")
    for f in data["fetched"]:
        f["text"] = ""
    return data


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _example_ids(samples: Path) -> list[str]:
    return sorted(p.stem for p in samples.glob("*.md") if EXAMPLE_ID.match(p.stem))


def _example_meta(samples: Path, ex_id: str) -> dict:
    """Title and blurb from samples/<id>.meta.json, else the document's first heading."""
    meta = _read_json(samples / f"{ex_id}.meta.json") or {}
    title = meta.get("title")
    if not title:
        doc = (samples / f"{ex_id}.md").read_text(encoding="utf-8")
        m = re.search(r"^#\s+(.+)$", doc, re.M)
        title = m.group(1).strip() if m else ex_id
    return {"id": ex_id, "title": str(title), "blurb": str(meta.get("blurb") or ""), "order": meta.get("order", 100)}


def fake_factory(fixtures: str | Path) -> Factory:
    from receipts.testing import FakeLLM, load_fixtures

    def make() -> Runtime:
        searcher, status_fn = load_fixtures(fixtures)
        return FakeLLM(), searcher, status_fn, None

    return make


def live_factory(settings: Settings) -> Factory:
    """A fresh TokenFactoryLLM per audit (usage is per audit); the HTTP clients and the disk cache are shared."""
    from receipts.llm import TokenFactoryLLM
    from receipts.scholarly import OpenAlex
    from receipts.search import DiskCache, TavilySearcher

    cache = DiskCache(settings.cache_dir)
    searcher, openalex = TavilySearcher(settings.tavily_api_key, cache), OpenAlex(cache)
    return lambda: (TokenFactoryLLM(settings), searcher, http_status, openalex)


class _Static(StaticFiles):
    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


CSP = ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def create_app(
    settings: Settings | None = None,
    config: ServerConfig | None = None,
    fake: str | Path | None = None,
    factory: Factory | None = None,
    samples_dir: Path | None = None,
) -> FastAPI:
    """`fake`: offline dev with FakeLLM/FakeSearcher fixtures (no budget, no rate limit).
    `factory`: a custom runtime builder (tests); guardrails stay on."""
    settings = settings or load_settings()
    cfg = config or ServerConfig.from_env()
    samples = Path(samples_dir) if samples_dir else SAMPLES_DIR
    enforce = fake is None
    if fake is not None:
        factory = fake_factory(fake)
    has_keys = bool(settings.nebius_api_key and settings.tavily_api_key)
    budget = Budget(cfg.budget_file or settings.cache_dir / "budget.json", cfg.daily_budget_usd)
    limiter = RateLimiter(cfg.per_ip_per_hour)
    slots = threading.BoundedSemaphore(max(1, cfg.max_concurrent))
    state: dict = {"factory": factory}

    def live_state() -> tuple[bool, str | None]:
        if enforce and not (has_keys or factory):
            return False, "keys"
        if enforce and not budget.has_room():
            return False, "budget"
        return True, None

    def get_factory() -> Factory:
        if state["factory"] is None:
            state["factory"] = live_factory(settings)
        return state["factory"]

    app = FastAPI(title="Receipts", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError):
        return _err(400, "The request was not understood.")

    app.mount("/static", _Static(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/config")
    def api_config():
        live, reason = live_state()
        examples = sorted((_example_meta(samples, i) for i in _example_ids(samples)), key=lambda e: (e["order"], e["id"]))
        return {
            "live": live,
            "reason": reason,
            "fake": fake is not None,
            "models": dict(settings.models),
            "limits": {"max_chars": cfg.max_doc_chars, "max_claims": cfg.max_claims, "per_ip_per_hour": cfg.per_ip_per_hour},
            "examples": [{k: e[k] for k in ("id", "title", "blurb")} for e in examples],
        }

    @app.get("/api/examples/{ex_id}")
    def api_example(ex_id: str):
        if not EXAMPLE_ID.match(ex_id) or not (samples / f"{ex_id}.md").is_file():
            return _err(404, "No such example.")
        document = (samples / f"{ex_id}.md").read_text(encoding="utf-8")
        recorded = _read_json(samples / f"{ex_id}.audit.json")
        if recorded:
            for f in recorded.get("fetched", []):
                f["text"] = ""
        return {"document": document, "audit": recorded}

    @app.post("/api/render")
    def api_render(report: AuditReport):
        """The markdown report (same renderer as `receipts audit --md`) for a report JSON."""
        return Response(render_markdown(report), media_type="text/markdown; charset=utf-8")

    def client_key(request: Request) -> str:
        if cfg.trust_proxy and (fwd := request.headers.get("x-forwarded-for")):
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @app.post("/api/audit")
    async def api_audit(request: Request):
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > cfg.max_doc_chars * 4 + 4096:
            return _err(413, f"This report is too long; the limit is {cfg.max_doc_chars:,} characters.")
        try:
            req = AuditRequest.model_validate_json(await request.body())
        except ValidationError:
            return _err(400, 'Send JSON like {"document": "...", "mode": "cascade"}.')
        doc = req.document
        if not doc.strip():
            return _err(400, "Paste a report to check.")
        if len(doc) > cfg.max_doc_chars:
            return _err(413, f"This report is too long ({len(doc):,} characters); the limit is {cfg.max_doc_chars:,}.")
        live, _ = live_state()
        if not live:
            return _err(503, PAUSED)
        if enforce and (wait := limiter.hit(client_key(request))) is not None:
            return _err(429, f"You have used your {cfg.per_ip_per_hour} live checks for this hour. "
                             "Try an example, or come back later.", Retry_After=str(wait))
        if not slots.acquire(blocking=False):
            return _err(429, "The checker is busy with other reports. Try again in a minute.", Retry_After="60")

        events: queue.Queue = queue.Queue()

        def work() -> None:
            llm = None
            try:
                llm, searcher, status_fn, openalex = get_factory()()
                report = audit(doc, llm, searcher, settings, mode=req.mode, on_event=events.put,
                               status_fn=status_fn, openalex=openalex, max_claims=cfg.max_claims)
                events.put({"type": "done", "report": public_report(report)})
            except Exception as exc:  # noqa: BLE001 - reported to the client in a safe form
                log.warning("audit failed: %s", type(exc).__name__)
                events.put({"type": "error", "message": _friendly(exc)})
            finally:
                if enforce and llm is not None:
                    budget.add(sum(u.cost_usd for u in llm.usage.values()))
                slots.release()
                events.put(None)

        threading.Thread(target=work, daemon=True, name="audit").start()

        def stream():
            while True:
                try:
                    event = events.get(timeout=15)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    return
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    return app
