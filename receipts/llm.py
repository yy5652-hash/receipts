"""LLM access: a small protocol, plus the Nebius Token Factory (OpenAI-compatible) client."""

import json
import re
import threading
from typing import Protocol

from .config import Settings
from .models import Usage

TASKS = ("parse_sources", "extract_claims", "judge")


class LLM(Protocol):
    usage: dict[str, Usage]  # keyed by tier

    def complete_json(self, *, task: str, tier: str, system: str, user: str, payload: dict,
                      think: bool | None = None) -> dict:
        """Run one JSON-returning call; `think` overrides the tier's reasoning setting. `payload` is the structured input `user` was built from
        (fakes use it; the real client ignores it)."""
        ...


def parse_json_lenient(text: str) -> dict:
    """Extract the JSON object from model output: drops <think> blocks, code fences and prose."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    if "</think>" in text.lower():  # reasoning without an opening tag
        text = re.split(r"</think>", text, flags=re.I)[-1]
    text = re.sub(r"```(?:json)?", "", text, flags=re.I)
    first, last = text.find("{"), text.rfind("}")
    if first == -1 or last <= first:
        raise ValueError("no JSON object in model output")
    try:
        val = json.loads(text[first : last + 1])
        if isinstance(val, dict):
            return val
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()  # fall back: first decodable object, e.g. prose with stray braces
    for m in re.finditer(r"\{", text):
        try:
            val, _ = decoder.raw_decode(text[m.start() :])
        except json.JSONDecodeError:
            continue
        if isinstance(val, dict):
            return val
    raise ValueError("could not parse JSON from model output")


class TokenFactoryLLM:
    """OpenAI-compatible client for Nebius Token Factory serving the Nemotron tiers."""

    def __init__(self, settings: Settings, client=None):
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                base_url=settings.base_url,
                api_key=settings.nebius_api_key,
                timeout=120,
                max_retries=2,  # the SDK backs off on 429 / 5xx
            )
        self.client = client
        self.settings = settings
        self.usage: dict[str, Usage] = {}
        self._lock = threading.Lock()
        self._json_mode_rejected = False

    def _chat(self, tier: str, system: str, user: str, think: bool | None = None):
        from openai import BadRequestError

        opts = self.settings.tier_options.get(tier, {})
        kwargs = dict(
            model=self.settings.models[tier],
            messages=[
                {"role": "system", "content": (opts.get("system_prefix") or "") + system},
                {"role": "user", "content": user},
            ],
            temperature=0,
        )
        extra = dict(opts.get("extra_body") or {})
        if think is not None:
            extra["chat_template_kwargs"] = {**extra.get("chat_template_kwargs", {}), "enable_thinking": think}
        if extra:
            kwargs["extra_body"] = extra
        if not self._json_mode_rejected:
            try:
                return self.client.chat.completions.create(
                    **kwargs, response_format={"type": "json_object"}
                )
            except BadRequestError:
                self._json_mode_rejected = True  # server has no JSON mode: retry once without it
        return self.client.chat.completions.create(**kwargs)

    def _record(self, tier: str, resp) -> None:
        u = getattr(resp, "usage", None)
        pt, ct = (getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0)
        price_in, price_out = self.settings.prices[tier]
        with self._lock:
            rec = self.usage.setdefault(tier, Usage(tier=tier, model=self.settings.models[tier]))
            rec.calls += 1
            rec.prompt_tokens += pt
            rec.completion_tokens += ct
            rec.cost_usd += (pt * price_in + ct * price_out) / 1e6

    def complete_json(self, *, task: str, tier: str, system: str, user: str, payload: dict,
                      think: bool | None = None) -> dict:
        resp = self._chat(tier, system, user, think)
        self._record(tier, resp)
        try:
            return parse_json_lenient(resp.choices[0].message.content or "")
        except ValueError:
            resp = self._chat(tier, system, user + "\n\nReturn only valid JSON.", think)
            self._record(tier, resp)
            return parse_json_lenient(resp.choices[0].message.content or "")
