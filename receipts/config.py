"""Settings loaded from the environment (.env at the repo root). Keys are never printed."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
TIERS = ("nano", "super", "ultra")
BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# NVIDIA Nemotron models served on Token Factory (IDs and USD per 1M tokens from /v1/models, 2026-10-19).
DEFAULT_MODELS = {
    "nano": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "super": "nvidia/nemotron-3-super-120b-a12b",
    "ultra": "nvidia/Nemotron-3-Ultra-550b-a55b",
}
DEFAULT_PRICES = {"nano": (0.06, 0.24), "super": (0.30, 0.90), "ultra": (1.00, 3.00)}
# Nano extracts with reasoning off (fast, cheap); Super and Ultra judge with reasoning on.
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}
DEFAULT_EXTRA_BODY = {"nano": NO_THINKING, "super": {}, "ultra": {}}


@dataclass
class Settings:
    nebius_api_key: str | None = None
    tavily_api_key: str | None = None
    base_url: str = BASE_URL
    models: dict[str, str] = field(default_factory=dict)
    prices: dict[str, tuple[float, float]] = field(default_factory=lambda: dict(DEFAULT_PRICES))
    tier_options: dict[str, dict] = field(default_factory=dict)
    cache_dir: Path = REPO_ROOT / ".cache"
    escalate_below: float = 0.7
    max_workers: int = 8
    cascade: tuple[str, str] = ("super", "ultra")  # first-pass tier, escalation tier of the "cascade" mode
    route: tuple[str, str] = ("nano", "ultra")  # checking tier, second-look tier of the "routed" mode

    def __post_init__(self):
        for t in TIERS:
            self.models.setdefault(t, DEFAULT_MODELS[t])
            self.tier_options.setdefault(t, {"system_prefix": "", "extra_body": {}})


def _price(raw: str | None, default: tuple[float, float]) -> tuple[float, float]:
    if not raw:
        return default
    try:
        a, b = raw.split(",")
        return float(a), float(b)
    except ValueError:
        return default


def _json_dict(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return val if isinstance(val, dict) else {}


def _cascade(raw: str | None, default: tuple[str, str] = ("super", "ultra")) -> tuple[str, str]:
    """RECEIPTS_CASCADE="nano,ultra" -> ("nano", "ultra"); anything invalid keeps the default."""
    parts = tuple(p.strip().lower() for p in (raw or "").split(","))
    return parts if len(parts) == 2 and all(p in TIERS for p in parts) else default  # type: ignore[return-value]


def load_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")
    env = os.environ.get
    up = {t: t.upper() for t in TIERS}
    return Settings(
        cascade=_cascade(env("RECEIPTS_CASCADE")),
        route=_cascade(env("RECEIPTS_ROUTE"), ("nano", "ultra")),
        nebius_api_key=env("NEBIUS_API_KEY") or None,
        tavily_api_key=env("TAVILY_API_KEY") or None,
        models={t: env(f"RECEIPTS_MODEL_{up[t]}") or DEFAULT_MODELS[t] for t in TIERS},
        prices={t: _price(env(f"RECEIPTS_PRICE_{up[t]}"), DEFAULT_PRICES[t]) for t in TIERS},
        tier_options={
            t: {
                "system_prefix": env(f"RECEIPTS_SYSPREFIX_{up[t]}") or "",
                "extra_body": _json_dict(env(f"RECEIPTS_EXTRA_BODY_{up[t]}")) or dict(DEFAULT_EXTRA_BODY[t]),
            }
            for t in TIERS
        },
    )
