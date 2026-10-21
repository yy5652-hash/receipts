"""Records ONE real audit of samples/ai-energy-briefing.md (mode "ultra") through the real web server.

    .venv/bin/python film/record_live.py

The server runs in this process with the repository's own settings (keys come from the environment / .env and are
never printed) and a fresh, empty cache in film/work/cache-live, so every source is fetched for real. The audit is
requested over HTTP exactly as the web app does it, and every server-sent event is stored with its arrival time:

    film/data/live-run.jsonl       one line per event: {"t": seconds since the request was sent, "event": {...}}
    film/data/live-run.meta.json   what was run, when, and what it cost

Spends about 0.04 USD of model credits plus a handful of Tavily calls. Run it once.
"""
import hashlib
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn

FILM = Path(__file__).resolve().parent
ROOT = FILM.parent
sys.path.insert(0, str(ROOT))

from app.server import ServerConfig, create_app  # noqa: E402
from receipts.config import load_settings  # noqa: E402

PORT = 8765
OUT = FILM / "data/live-run.jsonl"
META = FILM / "data/live-run.meta.json"
DOC_PATH = ROOT / "samples/ai-energy-briefing.md"


def main() -> int:
    if OUT.exists():
        print(f"{OUT} exists; refusing to spend credits on a second run", file=sys.stderr)
        return 2
    settings = load_settings()
    if not (settings.nebius_api_key and settings.tavily_api_key):
        print("error: model and search keys are not configured", file=sys.stderr)
        return 2
    settings.cache_dir = FILM / "work/cache-live"
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(settings=settings, config=ServerConfig.from_env())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/api/config", timeout=2).json().get("live"):
                break
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(0.2)
    else:
        print("error: the server did not come up live", file=sys.stderr)
        return 2

    doc = DOC_PATH.read_text(encoding="utf-8")
    events: list[dict] = []
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    with httpx.Client(timeout=None) as client:
        with client.stream("POST", f"http://127.0.0.1:{PORT}/api/audit", json={"document": doc, "mode": "ultra"}) as r:
            r.raise_for_status()
            buf = ""
            for chunk in r.iter_text():
                now = round(time.monotonic() - t0, 3)
                buf += chunk
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    for line in block.split("\n"):
                        if not line.startswith("data:"):
                            continue
                        ev = json.loads(line[5:].strip())
                        events.append({"t": now, "event": ev})
                        if ev["type"] in ("stage", "claims", "done", "error"):
                            print(f"{now:7.2f} s  {ev['type']} {ev.get('stage', '')}", flush=True)
    total = round(time.monotonic() - t0, 3)
    OUT.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    done = next((e["event"] for e in events if e["event"]["type"] == "done"), None)
    if not done:
        print("error: the run did not finish; see the stored events", file=sys.stderr)
        return 1
    report = done["report"]
    cost = sum(u["cost_usd"] for u in report["usage"])
    META.write_text(json.dumps({
        "recorded_at": started.isoformat(timespec="seconds"),
        "document": "samples/ai-energy-briefing.md",
        "document_sha256": hashlib.sha256(doc.encode("utf-8")).hexdigest(),
        "mode": "ultra",
        "request_to_last_event_s": total,
        "report_seconds": report["seconds"],
        "model_cost_usd": round(cost, 6),
        "usage": report["usage"],
        "events": len(events),
        "cache_entries_created": len(list(settings.cache_dir.glob("*.json"))),
        "note": "cold cache: sources fetched live (Tavily Extract/Search, OpenAlex, arXiv); events are stored with their arrival times",
    }, indent=1), encoding="utf-8")
    print(f"done: {len(events)} events in {total:.1f} s, report {report['seconds']} s, model cost ${cost:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
