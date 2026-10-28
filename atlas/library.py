"""Load and validate the guide library from guides/*.json."""

from __future__ import annotations

import json
from pathlib import Path

from .policy import Issue, check_guide
from .schema import Guide

ROOT = Path(__file__).resolve().parent.parent
GUIDES_DIR = ROOT / "guides"        # curated: must pass every policy check
GENERATED_DIR = ROOT / "generated"  # agent-written: shown with their issues, pending review


def load_guide(path: Path) -> Guide:
    return Guide.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_all(directory: Path = GUIDES_DIR) -> dict[str, Guide]:
    guides = {}
    for path in sorted(directory.glob("*.json")):
        g = load_guide(path)
        guides[g.slug] = g
    return guides


def load_library() -> dict[str, Guide]:
    """Curated guides plus generated ones. A curated guide wins over a
    generated guide with the same slug."""
    guides = load_all(GENERATED_DIR) if GENERATED_DIR.exists() else {}
    guides.update(load_all(GUIDES_DIR))
    return guides


def validate_all(directory: Path = GUIDES_DIR) -> dict[str, list[Issue]]:
    return {slug: check_guide(g) for slug, g in load_all(directory).items()}


if __name__ == "__main__":  # python -m atlas.library
    import sys

    failed = False
    for slug, issues in validate_all().items():
        errs = [i for i in issues if i.is_error]
        warns = [i for i in issues if not i.is_error]
        print(f"{slug}: {len(errs)} errors, {len(warns)} warnings")
        for i in issues:
            print(f"  [{i.severity}] {i.code}: {i.message}")
        failed |= bool(errs)
    sys.exit(1 if failed else 0)
