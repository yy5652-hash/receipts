from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from receipts.config import Settings
from receipts.llm import TokenFactoryLLM, parse_json_lenient


def test_parse_plain_and_think_tags():
    assert parse_json_lenient('{"a": 1}') == {"a": 1}
    assert parse_json_lenient('<think>hmm {"x": 0}</think>\n{"a": 1}') == {"a": 1}
    assert parse_json_lenient('reasoning without opener</think>{"a": 2}') == {"a": 2}


def test_parse_prose_and_fences():
    assert parse_json_lenient('Sure! Here you go: {"a": {"b": [1, 2]}} Hope that helps.') == {"a": {"b": [1, 2]}}
    assert parse_json_lenient('```json\n{"a": "x"}\n```') == {"a": "x"}
    assert parse_json_lenient('Note {braces} first. {"ok": true}') == {"ok": True}


def test_parse_failure():
    for bad in ("", "no json here", "{broken", "[1, 2]"):
        with pytest.raises(ValueError):
            parse_json_lenient(bad)


def _resp(content, pt=10, ct=5):
    msg = SimpleNamespace(content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct),
    )


class StubClient:
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _bad_request():
    req = httpx.Request("POST", "https://x.test/v1/chat/completions")
    return BadRequestError("no json mode", response=httpx.Response(400, request=req), body=None)


def _settings():
    return Settings(models={"nano": "m-nano"}, tier_options={"nano": {"system_prefix": "PFX ", "extra_body": {"k": 1}}})


def test_client_retries_without_json_mode_and_records_usage():
    stub = StubClient([_bad_request(), _resp('{"a": 1}', 1_000_000, 1_000_000)])
    llm = TokenFactoryLLM(_settings(), client=stub)
    out = llm.complete_json(task="judge", tier="nano", system="SYS", user="U", payload={})
    assert out == {"a": 1}
    assert "response_format" in stub.calls[0] and "response_format" not in stub.calls[1]
    assert stub.calls[0]["messages"][0]["content"] == "PFX SYS"
    assert stub.calls[0]["extra_body"] == {"k": 1} and stub.calls[0]["temperature"] == 0
    u = llm.usage["nano"]
    assert (u.calls, u.prompt_tokens) == (1, 1_000_000)
    assert u.cost_usd == pytest.approx(0.06 + 0.24)


def test_client_retries_once_on_unparseable_output():
    stub = StubClient([_resp("not json"), _resp('{"b": 2}')])
    llm = TokenFactoryLLM(_settings(), client=stub)
    assert llm.complete_json(task="judge", tier="nano", system="S", user="U", payload={}) == {"b": 2}
    assert stub.calls[1]["messages"][1]["content"].endswith("Return only valid JSON.")
    assert llm.usage["nano"].calls == 2
