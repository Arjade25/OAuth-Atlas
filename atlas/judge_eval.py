"""Score a support judge against the hand-labelled set in evals/judge_set.json.

    python -m atlas.judge_eval --judge nli
    python -m atlas.judge_eval --judge laya
    python -m atlas.judge_eval --judge llm

The two numbers that matter:
  false passes  - an unsupported or contradicted claim the judge let through.
                  These reach the page as "verified", so they are the costly error.
  false flags   - a supported claim the judge held back. These cost review time.
Results are saved to evals/results-<judge>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .schema import CheckResult
from .support import make_support, threshold

ROOT = Path(__file__).resolve().parent.parent
SET = ROOT / "evals" / "judge_set.json"


def score(judge, items: list[dict]) -> dict:
    rows = []
    for it in items:
        t0 = time.perf_counter()
        v = judge.check(it["claim"], it["passage"])
        rows.append({"id": it["id"], "label": it["label"], "result": v.result.value, "p_supports": v.p_supports,
                     "ms": round((time.perf_counter() - t0) * 1000), "note": v.note})
    passed = lambda r: r["result"] == CheckResult.passed.value  # noqa: E731
    should_pass = [r for r in rows if r["label"] == "supports"]
    should_hold = [r for r in rows if r["label"] != "supports"]
    false_passes = [r["id"] for r in should_hold if passed(r)]
    false_flags = [r["id"] for r in should_pass if not passed(r)]
    contradictions = [r for r in rows if r["label"] == "contradicts"]
    return {
        "judge": judge.name, "threshold": threshold(), "items": len(rows),
        "correct_pass_or_hold": len(rows) - len(false_passes) - len(false_flags),
        "false_passes": false_passes, "false_flags": false_flags,
        "contradictions_caught": sum(r["result"] == "fail" for r in contradictions),
        "contradictions_total": len(contradictions),
        "median_ms": sorted(r["ms"] for r in rows)[len(rows) // 2] if rows else 0,
        "rows": rows,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m atlas.judge_eval")
    ap.add_argument("--judge", choices=["nli", "laya", "llm", "auto"], default="auto")
    args = ap.parse_args(argv)
    from dotenv import load_dotenv

    load_dotenv(override=True)
    try:
        judge = make_support(args.judge)
    except Exception as e:
        print(f"x Could not load the {args.judge} judge: {e}", file=sys.stderr)
        return 2
    items = json.loads(SET.read_text(encoding="utf-8"))["items"]
    print(f"Scoring {judge.name} on {len(items)} labelled claims (threshold {threshold():.2f})...")
    res = score(judge, items)
    for r in res["rows"]:
        mark = "ok " if (r["result"] == "pass") == (r["label"] == "supports") else "XX "
        p = "" if r["p_supports"] is None else f" p={r['p_supports']:.2f}"
        print(f"  {mark}{r['id']:<10} label={r['label']:<11} judge={r['result']:<8}{p}")
    print(f"\n{res['correct_pass_or_hold']}/{res['items']} correct pass-or-hold decisions")
    print(f"False passes (unsupported claims let through): {len(res['false_passes'])} {res['false_passes']}")
    print(f"False flags (good claims held back):           {len(res['false_flags'])} {res['false_flags']}")
    print(f"Contradictions caught as contradictions:      {res['contradictions_caught']}/{res['contradictions_total']}")
    print(f"Median time per claim: {res['median_ms']} ms")
    out = ROOT / "evals" / f"results-{args.judge}.json"
    out.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
