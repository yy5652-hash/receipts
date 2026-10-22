"""Web app tests: FastAPI TestClient, fully offline (fake LLM and search fixtures)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import server
from app.server import Budget, ServerConfig, create_app, fake_factory
from receipts.config import Settings
from receipts.testing import FakeLLM, load_fixtures

FIX = Path(__file__).parent / "fixtures"
DOC = (FIX / "report_basic.md").read_text(encoding="utf-8")
FIXTURES = FIX / "sources.json"


@pytest.fixture(autouse=True)
def fixture_samples(monkeypatch):
    """Server tests use the fake-data example; samples/ only holds recorded real runs."""
    monkeypatch.setattr(server, "SAMPLES_DIR", FIX / "samples")


def events_of(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


@pytest.fixture
def client():
    return TestClient(create_app(fake=FIXTURES))


def keyed_settings(tmp_path) -> Settings:
    return Settings(nebius_api_key="test-key", tavily_api_key="test-key", cache_dir=tmp_path)


class CostlyLLM(FakeLLM):
    """A fake LLM that reports a cost, to exercise the budget accounting."""

    def complete_json(self, **kw):
        out = super().complete_json(**kw)
        self.usage[kw["tier"]].cost_usd += 0.01
        return out


def costly_factory():
    searcher, status_fn = load_fixtures(FIXTURES)
    return lambda: (CostlyLLM(), searcher, status_fn, None)


def test_index_and_static(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_config_lists_models_limits_and_examples(client):
    cfg = client.get("/api/config").json()
    assert cfg["live"] is True and cfg["fake"] is True and cfg["reason"] is None
    assert set(cfg["models"]) == {"nano", "super", "ultra"}
    assert cfg["limits"]["max_chars"] == 40_000
    assert cfg["limits"]["suggest_max"] == 8  # how many unsupported claims get a web search; the page uses it for its progress
    (ex,) = [e for e in cfg["examples"] if e["id"] == "fixture-demo"]
    assert ex["title"] and ex["blurb"]


def test_config_not_live_without_keys(tmp_path):
    app = create_app(settings=Settings(cache_dir=tmp_path), config=ServerConfig(budget_file=tmp_path / "b.json"))
    c = TestClient(app)
    assert c.get("/api/config").json()["live"] is False and c.get("/api/config").json()["reason"] == "keys"
    r = c.post("/api/audit", json={"document": DOC})
    assert r.status_code == 503 and "paused" in r.json()["error"]


def test_example_endpoint_returns_document_and_recorded_audit(client):
    r = client.get("/api/examples/fixture-demo")
    assert r.status_code == 200
    body = r.json()
    assert body["document"].startswith("# Renewable Energy Outlook 2023")
    assert len(body["audit"]["claims"]) == 9 and body["audit"]["verdicts"]
    assert all(f["text"] == "" for f in body["audit"]["fetched"])


def test_example_endpoint_rejects_unknown_and_traversal(client):
    assert client.get("/api/examples/nope").status_code == 404
    assert client.get("/api/examples/..%2Fpyproject").status_code == 404
    assert client.get("/api/examples/.env").status_code == 404


def test_example_without_recorded_audit(tmp_path):
    (tmp_path / "plain.md").write_text("# Plain\n\nText [1].\n", encoding="utf-8")
    c = TestClient(create_app(fake=FIXTURES, samples_dir=tmp_path))
    assert c.get("/api/examples/plain").json() == {"document": "# Plain\n\nText [1].\n", "audit": None}
    assert c.get("/api/config").json()["examples"] == [{"id": "plain", "title": "Plain", "blurb": ""}]


def test_audit_streams_claims_then_verdicts_then_done(client):
    r = client.post("/api/audit", json={"document": DOC, "mode": "cascade"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = events_of(r)
    kinds = [e["type"] for e in ev]
    assert kinds[0] == "stage" and ev[0]["stage"] == "extract"
    assert kinds.index("claims") < kinds.index("source") < kinds.index("verdict") < kinds.index("done")
    assert kinds[-1] == "done" and "error" not in kinds
    claims = next(e for e in ev if e["type"] == "claims")
    assert len(claims["claims"]) == 9 and len(claims["sources"]) == 7
    assert [e["fetched"]["source_id"] for e in ev if e["type"] == "source"] == [s["id"] for s in claims["sources"]]
    assert all("text" not in e["fetched"] for e in ev if e["type"] == "source")
    verdicts = [e["verdict"] for e in ev if e["type"] == "verdict"]
    assert verdicts and all(v["passages"] is not None for v in verdicts)
    report = ev[-1]["report"]
    assert len(report["verdicts"]) == len(verdicts) and all(f["text"] == "" for f in report["fetched"])
    assert [s["stage"] for s in ev if s["type"] == "stage"] == ["extract", "fetch", "judge", "suggest", "done"]


def test_audit_streams_suggestions_for_unsupported_claims(client):
    ev = events_of(client.post("/api/audit", json={"document": DOC, "mode": "cascade"}))
    kinds = [e["type"] for e in ev]
    suggestions = [e["suggestion"] for e in ev if e["type"] == "suggestion"]
    assert {s["claim_id"]: s["status"] for s in suggestions} == {"c3": "supported", "c5": "contradicted", "c6": "none"}
    assert kinds.index("suggestion") > max(i for i, k in enumerate(kinds) if k == "verdict")
    assert max(i for i, k in enumerate(kinds) if k == "suggestion") < kinds.index("done")
    sup = next(s for s in suggestions if s["status"] == "supported")
    assert sup["url"] == "https://hvac.example/heat-pump-sales" and sup["quote_found"] and sup["tier"] == "nano"
    report = ev[-1]["report"]
    assert sorted(s["claim_id"] for s in report["suggestions"]) == ["c3", "c5", "c6"]


def test_suggest_false_turns_the_stage_off(client):
    ev = events_of(client.post("/api/audit", json={"document": DOC, "suggest": False}))
    assert not any(e["type"] == "suggestion" for e in ev) and ev[-1]["report"]["suggestions"] == []
    assert "suggest" not in [e["stage"] for e in ev if e["type"] == "stage"]
    assert client.post("/api/audit", json={"document": DOC, "suggest": "maybe"}).status_code == 400


def test_suggest_calls_are_charged_to_the_budget(tmp_path):
    def spent(suggest: bool) -> float:
        path = tmp_path / f"budget-{suggest}.json"
        cfg = ServerConfig(daily_budget_usd=100, budget_file=path)
        c = TestClient(create_app(settings=keyed_settings(tmp_path), config=cfg, factory=costly_factory()))
        assert events_of(c.post("/api/audit", json={"document": DOC, "suggest": suggest}))[-1]["type"] == "done"
        return json.loads(path.read_text())[today()]

    assert spent(True) > spent(False)


def test_audit_validates_the_request(client):
    assert client.post("/api/audit", json={"document": "  \n"}).status_code == 400
    assert client.post("/api/audit", json={"document": DOC, "mode": "turbo"}).status_code == 400
    assert client.post("/api/audit", content=b"not json").status_code == 400
    assert "error" in client.post("/api/audit", json={}).json()


def test_oversize_document_is_rejected(client):
    r = client.post("/api/audit", json={"document": "x" * 40_001})
    assert r.status_code == 413 and "40,000" in r.json()["error"]
    assert client.post("/api/audit", json={"document": "x" * 40_000}).status_code != 413


def test_claims_beyond_the_cap_are_dropped_with_a_notice():
    c = TestClient(create_app(fake=FIXTURES, config=ServerConfig(max_claims=4)))
    ev = events_of(c.post("/api/audit", json={"document": DOC}))
    assert len(next(e for e in ev if e["type"] == "claims")["claims"]) == 4
    assert any(e["type"] == "notice" for e in ev) and ev[-1]["type"] == "done"
    assert len(ev[-1]["report"]["claims"]) == 4


def test_budget_exhausted_returns_503(tmp_path):
    budget_file = tmp_path / "budget.json"
    budget_file.write_text(json.dumps({today(): 2.0}))
    cfg = ServerConfig(daily_budget_usd=2.0, budget_file=budget_file)
    c = TestClient(create_app(settings=keyed_settings(tmp_path), config=cfg, factory=costly_factory()))
    assert c.get("/api/config").json() == {**c.get("/api/config").json(), "live": False, "reason": "budget"}
    r = c.post("/api/audit", json={"document": DOC})
    assert r.status_code == 503 and r.json() == {"error": "live audits are paused; try an example"}


def test_budget_is_charged_after_each_audit(tmp_path):
    cfg = ServerConfig(daily_budget_usd=0.05, budget_file=tmp_path / "budget.json")
    c = TestClient(create_app(settings=keyed_settings(tmp_path), config=cfg, factory=costly_factory()))
    assert c.get("/api/config").json()["live"] is True
    assert events_of(c.post("/api/audit", json={"document": DOC}))[-1]["type"] == "done"
    spent = json.loads((tmp_path / "budget.json").read_text())[today()]
    assert spent > 0.05  # many model calls at 0.01 each exceed the tiny budget
    assert c.get("/api/config").json()["live"] is False
    assert c.post("/api/audit", json={"document": DOC}).status_code == 503


def test_per_ip_rate_limit(tmp_path):
    cfg = ServerConfig(per_ip_per_hour=2, budget_file=tmp_path / "budget.json")
    c = TestClient(create_app(settings=keyed_settings(tmp_path), config=cfg, factory=fake_factory(FIXTURES)))
    assert [c.post("/api/audit", json={"document": DOC}).status_code for _ in range(2)] == [200, 200]
    r = c.post("/api/audit", json={"document": DOC})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0 and "hour" in r.json()["error"]


def test_failures_are_reported_without_leaking_details(tmp_path):
    def boom():
        raise RuntimeError("secret-key-123 exploded at /home/user/x.py")

    c = TestClient(create_app(settings=keyed_settings(tmp_path),
                              config=ServerConfig(budget_file=tmp_path / "b.json"), factory=boom))
    r = c.post("/api/audit", json={"document": DOC})
    ev = events_of(r)
    assert ev[-1]["type"] == "error" and "secret" not in r.text and "/home" not in r.text


def test_budget_file_roundtrip(tmp_path):
    b = Budget(tmp_path / "x" / "budget.json", 1.0)
    assert b.spent() == 0 and b.has_room()
    b.add(0.4)
    b.add(0.6)
    assert b.spent() == pytest.approx(1.0) and not b.has_room()


def test_render_endpoint_returns_the_markdown_report(client):
    report = client.get("/api/examples/fixture-demo").json()["audit"]
    r = client.post("/api/render", json=report)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert r.text.startswith("# Receipts: citation audit of “Renewable Energy Outlook 2023”")
    assert "❌ contradicted" in r.text and "$400 billion in 2022" in r.text
    assert client.post("/api/render", json={"nope": 1}).status_code == 400


def test_tavily_credit_budget_pauses_live_audits(tmp_path):
    (tmp_path / "tavily_budget.json").write_text(json.dumps({today(): 45}))
    cfg = ServerConfig(daily_budget_usd=2.0, daily_tavily_credits=45, budget_file=tmp_path / "budget.json")
    c = TestClient(create_app(settings=keyed_settings(tmp_path), config=cfg, factory=costly_factory()))
    cfg_json = c.get("/api/config").json()
    assert (cfg_json["live"], cfg_json["reason"], cfg_json["limits"]["tavily_credits_per_day"]) == (False, "budget", 45)
    assert c.post("/api/audit", json={"document": "x [1].\n\n[1] https://a.example"}).status_code == 503
