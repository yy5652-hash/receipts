"""Data models shared by every stage of the audit."""

from typing import Literal

from pydantic import BaseModel

Label = Literal["supported", "partial", "contradicted", "not_in_source", "source_unavailable"]
Tier = Literal["nano", "super", "ultra", "none"]

# Best-to-worst order used when a claim cites several sources.
LABEL_ORDER: list[str] = ["supported", "partial", "contradicted", "not_in_source", "source_unavailable"]


class Source(BaseModel):
    """One entry of the report's reference list, or an inline link."""

    id: str  # "s1", "s2", ...
    marker: str | None = None  # "1" for [1], "^1" for [^1], None for inline links
    raw: str  # entry text as written
    url: str | None = None
    title: str | None = None
    authors: list[str] = []
    year: int | None = None
    doi: str | None = None


class Claim(BaseModel):
    id: str  # "c1", ...
    sentence: str  # verbatim sentence from the report body
    start: int  # char offsets of `sentence` in the ORIGINAL document
    end: int
    claim: str  # decontextualised assertion attributed to the source(s), markers removed
    source_ids: list[str]


class FetchedSource(BaseModel):
    source_id: str
    status: Literal["ok", "dead_link", "not_found"]
    url: str | None = None
    title: str | None = None
    text: str = ""
    via: str = ""  # "tavily_extract" | "tavily_search" | "openalex" | "fixture" | ""
    note: str = ""
    coverage: Literal["full", "abstract"] = "full"  # "abstract": only the abstract could be fetched
    exists: bool | None = None  # True: a record was found; False: looked and found none; None: not checked


class Passage(BaseModel):
    text: str
    start: int  # offsets into FetchedSource.text
    end: int
    score: float


class Verdict(BaseModel):
    claim_id: str
    source_id: str
    label: Label
    confidence: float = 0.0
    quote: str = ""
    quote_found: bool = False  # quote verified to occur (normalised) in the fetched text
    rationale: str = ""
    model: str = ""
    tier: Tier = "none"
    escalated: bool = False
    passages: list[Passage] = []


class Usage(BaseModel):
    tier: str
    model: str
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class AuditReport(BaseModel):
    document: str
    mode: str
    sources: list[Source]
    claims: list[Claim]
    fetched: list[FetchedSource]
    verdicts: list[Verdict]
    usage: list[Usage]
    seconds: float

    def claim_label(self, claim_id: str) -> Label:
        """Best verdict label across the claim's sources."""
        labels = [v.label for v in self.verdicts if v.claim_id == claim_id]
        if not labels:
            return "source_unavailable"
        return min(labels, key=LABEL_ORDER.index)

    def claim_verdict(self, claim_id: str) -> Verdict | None:
        """The verdict that determines the claim's label (first best one)."""
        vs = [v for v in self.verdicts if v.claim_id == claim_id]
        return min(vs, key=lambda v: LABEL_ORDER.index(v.label), default=None)

    def summary(self) -> dict:
        counts = {label: 0 for label in LABEL_ORDER}
        for c in self.claims:
            counts[self.claim_label(c.id)] += 1
        return {
            "counts": counts,
            "cost_usd": round(sum(u.cost_usd for u in self.usage), 6),
            "n_claims": len(self.claims),
            "n_sources": len(self.sources),
        }
