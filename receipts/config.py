"""Settings loaded from the environment (.env at the repo root). Keys are never printed."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
TIERS = ("nano", "super", "ultra")
BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

DEFAULT_PRICES = {"nano": (0.06, 0.24), "super": (0.30, 0.90), "ultra": (0.60, 1.80)}


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

    def __post_init__(self):
        for t in TIERS:
            self.models.setdefault(t, f"nvidia/nemotron-{t}-placeholder")
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


def load_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")
    env = os.environ.get
    up = {t: t.upper() for t in TIERS}
    return Settings(
        nebius_api_key=env("NEBIUS_API_KEY") or None,
        tavily_api_key=env("TAVILY_API_KEY") or None,
        models={t: env(f"RECEIPTS_MODEL_{up[t]}") or f"nvidia/nemotron-{t}-placeholder" for t in TIERS},
        prices={t: _price(env(f"RECEIPTS_PRICE_{up[t]}"), DEFAULT_PRICES[t]) for t in TIERS},
        tier_options={
            t: {
                "system_prefix": env(f"RECEIPTS_SYSPREFIX_{up[t]}") or "",
                "extra_body": _json_dict(env(f"RECEIPTS_EXTRA_BODY_{up[t]}")),
            }
            for t in TIERS
        },
    )
