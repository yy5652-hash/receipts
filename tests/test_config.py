from receipts import config


def test_load_settings_defaults_and_overrides(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    for k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "RECEIPTS_MODEL_SUPER", "RECEIPTS_PRICE_ULTRA"):
        monkeypatch.delenv(k, raising=False)
    s = config.load_settings()
    assert s.nebius_api_key is None and s.tavily_api_key is None
    assert s.models["nano"] == "nvidia/nemotron-nano-placeholder"
    assert s.prices == {"nano": (0.06, 0.24), "super": (0.30, 0.90), "ultra": (0.60, 1.80)}
    assert s.tier_options["super"] == {"system_prefix": "", "extra_body": {}}
    assert (s.escalate_below, s.max_workers, s.cache_dir.name) == (0.7, 8, ".cache")

    monkeypatch.setenv("RECEIPTS_MODEL_SUPER", "nvidia/real-super")
    monkeypatch.setenv("RECEIPTS_PRICE_ULTRA", "1.5, 4")
    monkeypatch.setenv("RECEIPTS_SYSPREFIX_SUPER", "/think ")
    monkeypatch.setenv("RECEIPTS_EXTRA_BODY_SUPER", '{"chat_template_kwargs": {"thinking": true}}')
    monkeypatch.setenv("RECEIPTS_EXTRA_BODY_NANO", "not json")
    s = config.load_settings()
    assert s.models["super"] == "nvidia/real-super" and s.prices["ultra"] == (1.5, 4.0)
    assert s.tier_options["super"] == {"system_prefix": "/think ", "extra_body": {"chat_template_kwargs": {"thinking": True}}}
    assert s.tier_options["nano"]["extra_body"] == {}
