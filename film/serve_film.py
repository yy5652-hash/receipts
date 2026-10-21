"""Serves the real Receipts web app (this repository's app/ and samples/) for filming.

    .venv/bin/python film/serve_film.py [port]

No model or search keys are loaded into this process and any attempt at a live audit fails, so filming can never
spend credits. The film plays back the recorded run (film/data/live-run.jsonl) to the page instead.
"""
import sys
from pathlib import Path

import uvicorn

FILM = Path(__file__).resolve().parent
sys.path.insert(0, str(FILM.parent))

from app.server import ServerConfig, create_app  # noqa: E402
from receipts.config import Settings  # noqa: E402


def no_live():
    raise RuntimeError("the film server never makes live calls")


settings = Settings(cache_dir=FILM / "work/cache-film")  # defaults only: no keys
app = create_app(settings=settings, config=ServerConfig(budget_file=FILM / "work/budget-film.json"), factory=no_live)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8101, log_level="warning")
