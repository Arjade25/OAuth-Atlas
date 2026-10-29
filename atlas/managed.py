"""Does Composio already manage an OAuth app for this toolkit?

This is the first question a developer has, and the one step of setup the
agent can actually perform for anyone: it asks Composio's toolkits API, the
same data behind https://docs.composio.dev/toolkits/managed-auth. Run it
over the library with

    python -m atlas.managed --write

which needs COMPOSIO_API_KEY and updates guides/, generated/ and runs/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .schema import ComposioInfo

DOCS = "https://docs.composio.dev/toolkits/managed-auth"
_REDIRECT = ("To bring your own app, register the redirect URI shown when you create a custom auth config "
             "in Composio, then enter the app's client ID and secret there.")


def composio_info(result: dict, app: str) -> ComposioInfo:
    """Turn a managed_auth result into what the guide shows."""
    if not result or not result.get("checked"):
        return ComposioInfo(notes="Not checked yet whether Composio manages an OAuth app for "
                                  f"{app}; see Composio's managed OAuth directory. " + _REDIRECT)
    checked = date.fromisoformat(result["checked_on"]) if result.get("checked_on") else None
    if not result.get("found"):
        return ComposioInfo(checked_on=checked,
                            notes=f"Composio's toolkits API has no toolkit for {app}, so plan on your own "
                                  "OAuth app and confirm in Composio's toolkit list.")
    managed = result.get("managed_schemes") or []
    common = dict(toolkit=result.get("toolkit"), auth_modes=result.get("auth_modes") or [],
                  managed_auth_schemes=managed, checked_on=checked)
    if managed:
        return ComposioInfo(managed_auth_available=True, **common, notes=(
            f"Composio manages an OAuth app for {app} ({', '.join(managed)}), so your users can connect "
            "through Composio today without you registering one. You need your own app, and the paths "
            "below, to show your brand on the consent screen, request custom scopes or get your own "
            "rate-limit quota, as Composio recommends for production. " + _REDIRECT))
    return ComposioInfo(managed_auth_available=False, **common, notes=(
        f"Composio has a {app} toolkit but does not manage an OAuth app for it, so you need your own "
        "before users can connect: follow the paths below. " + _REDIRECT))


ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m atlas.managed", description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="Save results into the guides and recordings")
    args = ap.parse_args(argv)

    from dotenv import load_dotenv

    from .sources import LiveSources

    load_dotenv(override=True)
    src = LiveSources()
    for f in sorted([*ROOT.glob("guides/*.json"), *ROOT.glob("generated/*.json")]):
        g = json.loads(f.read_text(encoding="utf-8"))
        result = src.managed_auth(g["slug"])
        info = composio_info(result, g["app"])
        print(f"{g['app']:<10} toolkit={info.toolkit} modes={info.auth_modes} managed={info.managed_auth_schemes}")
        if args.write:
            old = g.get("composio") or {}
            new = info.model_dump(mode="json")
            if old.get("callback_url"):
                new["callback_url"] = old["callback_url"]
            g["composio"] = new
            f.write_text(json.dumps(g, indent=2, ensure_ascii=False), encoding="utf-8")
            run = ROOT / "runs" / f"{g['slug']}.json"
            if run.exists():
                r = json.loads(run.read_text(encoding="utf-8"))
                r["managed_auth"] = result
                run.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
