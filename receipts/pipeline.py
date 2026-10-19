"""The audit pipeline: extract -> fetch -> judge."""

import time
from collections.abc import Callable

from .config import Settings
from .extract import extract
from .judge import MODES, judge_all
from .llm import LLM
from .models import AuditReport
from .search import Searcher, http_status
from .sources import fetch_sources


def audit(
    doc: str,
    llm: LLM,
    searcher: Searcher,
    settings: Settings,
    mode: str = "cascade",
    on_event: Callable[[dict], None] | None = None,
    status_fn: Callable[[str], int | None] = http_status,
) -> AuditReport:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose one of {sorted(MODES)}")
    emit = on_event or (lambda e: None)
    t0 = time.monotonic()
    emit({"type": "stage", "stage": "extract"})
    sources, claims = extract(doc, llm)
    emit({"type": "stage", "stage": "fetch"})
    fetched = fetch_sources(sources, searcher, status_fn=status_fn)
    emit({"type": "stage", "stage": "judge"})
    verdicts = judge_all(claims, sources, fetched, llm, mode, settings, on_event=on_event)
    emit({"type": "stage", "stage": "done"})
    usage = [u.model_copy() for _, u in sorted(llm.usage.items(), key=lambda kv: kv[0])]
    return AuditReport(document=doc, mode=mode, sources=sources, claims=claims, fetched=fetched,
                       verdicts=verdicts, usage=usage, seconds=round(time.monotonic() - t0, 2))
