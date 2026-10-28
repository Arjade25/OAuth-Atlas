"""Generate a guide from the command line.

    python -m atlas.generate Pipedrive
    python -m atlas.generate pipedrive.com --support llm
    python -m atlas.generate pipedrive --replay        # re-run runs/pipedrive.json, no keys

Live runs read COMPOSIO_API_KEY and OPENROUTER_API_KEY from .env and write
generated/<slug>.json (the guide) and runs/<slug>.json (the recording).
"""

from __future__ import annotations

import argparse
import json
import sys

from dotenv import load_dotenv

from . import pipeline
from .extract import slugify
from .llm import OpenRouterModel
from .sources import LiveSources
from .support import make_support

ICONS = {"step": ">", "detail": " ", "warn": "!", "error": "x", "done": "="}


def live_parts(support_kind: str | None = None):
    load_dotenv(override=True)
    return LiveSources(), OpenRouterModel(), make_support(support_kind)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m atlas.generate", description="Research an app and write a checked guide.")
    ap.add_argument("app", help="App name or website, e.g. Pipedrive or pipedrive.com")
    ap.add_argument("--replay", action="store_true", help="Re-run the recording in runs/<slug>.json with no keys")
    ap.add_argument("--support", choices=["auto", "laya", "llm", "none"], default=None,
                    help="Support judge (default: SUPPORT_BACKEND or auto)")
    ap.add_argument("--no-save", action="store_true", help="Print a summary only")
    args = ap.parse_args(argv)

    if args.replay:
        path = pipeline.RUNS_DIR / f"{slugify(pipeline.resolve(args.app).app)}.json"
        if not path.exists():
            print(f"No recording at {path}. Run once without --replay first.", file=sys.stderr)
            return 2
        record = json.loads(path.read_text(encoding="utf-8"))
        sources, model, support = pipeline.replay_parts(record)
        query = record["query"]
    else:
        try:
            sources, model, support = live_parts(args.support)
        except Exception as e:
            print(f"x Setup failed: {e}", file=sys.stderr)
            return 2
        query = args.app

    for ev in pipeline.run(query, sources, model, support, replayed=args.replay):
        print(f"{ICONS.get(ev.type, ' ')} {ev.message}", flush=True)
        if ev.type == "error":
            return 1
        if ev.type == "done":
            if not args.no_save:
                out = pipeline.save(ev.guide, None if args.replay else ev.run)
                print(f"  Saved {out.relative_to(pipeline.ROOT)}"
                      + ("" if args.replay else f" and runs/{ev.guide.slug}.json"))
                print(f"  Open http://127.0.0.1:8000/guides/{ev.guide.slug} with the app running")
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
