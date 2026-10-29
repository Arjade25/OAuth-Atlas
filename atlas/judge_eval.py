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

from datetime import date

from .policy import claim_issues
from .schema import CheckResult, Claim, ClaimKind, Evidence
from .support import DEFAULT_JUDGE_INPUT, JUDGE_INPUTS, judge_input, make_support, threshold

ROOT = Path(__file__).resolve().parent.parent
SET = ROOT / "evals" / "judge_set.json"


def policy_passes(it: dict) -> bool:
    """The deterministic rules on the same claim and quote (check 3)."""
    if not it.get("quote"):
        return True
    c = Claim(id="x", text=it["claim"], kind=ClaimKind.fact,
              evidence=Evidence(url="https://example.com/", quote=it["quote"], retrieved_at=date(2026, 1, 1)))
    return not any(i.is_error for i in claim_issues(c, date(2026, 1, 1)))


def score(judge, items: list[dict], fmt: str = DEFAULT_JUDGE_INPUT) -> dict:
    rows = []
    for it in items:
        t0 = time.perf_counter()
        v = judge.check(it["claim"], judge_input(it["quote"], it["chunk"], it["source"], fmt))
        rows.append({"id": it["id"], "label": it["label"], "result": v.result.value, "p_supports": v.p_supports,
                     "ms": round((time.perf_counter() - t0) * 1000), "note": v.note,
                     "policy": policy_passes(it)})
    passed = lambda r: r["result"] == CheckResult.passed.value  # noqa: E731
    should_pass = [r for r in rows if r["label"] == "supports"]
    should_hold = [r for r in rows if r["label"] != "supports"]
    false_passes = [r["id"] for r in should_hold if passed(r)]
    false_flags = [r["id"] for r in should_pass if not passed(r)]
    contradictions = [r for r in rows if r["label"] == "contradicts"]
    # What reaches the page: verified only if the judge AND the rules pass.
    sys_pass = lambda r: passed(r) and r["policy"]  # noqa: E731
    system = {"false_passes": [r["id"] for r in should_hold if sys_pass(r)],
              "false_flags": [r["id"] for r in should_pass if not sys_pass(r)]}
    return {
        "input_format": fmt,
        "system": system,
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
    ap.add_argument("--input", choices=sorted(JUDGE_INPUTS), default=None,
                    help=f"How the judge's input is built (default {DEFAULT_JUDGE_INPUT} or JUDGE_INPUT)")
    ap.add_argument("--compare-inputs", action="store_true", help="Score every input format with one loaded model")
    args = ap.parse_args(argv)
    from dotenv import load_dotenv

    load_dotenv(override=True)
    try:
        judge = make_support(args.judge)
    except Exception as e:
        print(f"x Could not load the {args.judge} judge: {e}", file=sys.stderr)
        return 2
    items = json.loads(SET.read_text(encoding="utf-8"))["items"]
    if args.compare_inputs:
        return compare(judge, items, args.judge)
    import os

    fmt = args.input or os.getenv("JUDGE_INPUT", DEFAULT_JUDGE_INPUT)
    print(f"Scoring {judge.name} on {len(items)} labelled claims (input {fmt}, threshold {threshold():.2f})...")
    res = score(judge, items, fmt)
    for r in res["rows"]:
        mark = "ok " if (r["result"] == "pass") == (r["label"] == "supports") else "XX "
        p = "" if r["p_supports"] is None else f" p={r['p_supports']:.2f}"
        rule = "" if r["policy"] else "  (rules: flag)"
        print(f"  {mark}{r['id']:<10} label={r['label']:<11} judge={r['result']:<8}{p}{rule}")
    print(f"\n{res['correct_pass_or_hold']}/{res['items']} correct pass-or-hold decisions")
    print(f"False passes (unsupported claims let through): {len(res['false_passes'])} {res['false_passes']}")
    print(f"False flags (good claims held back):           {len(res['false_flags'])} {res['false_flags']}")
    print(f"Contradictions caught as contradictions:      {res['contradictions_caught']}/{res['contradictions_total']}")
    print(f"Median time per claim: {res['median_ms']} ms")
    sy = res["system"]
    print(f"\nWith the fixed rules added (what reaches the page as verified):")
    print(f"  False passes: {len(sy['false_passes'])} {sy['false_passes']}")
    print(f"  False flags:  {len(sy['false_flags'])} {sy['false_flags']}")
    out = ROOT / "evals" / f"results-{args.judge}.json"
    out.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {out.relative_to(ROOT)}")
    return 0


def compare(judge, items, judge_kind) -> int:
    print(f"Comparing input formats for {judge.name} on {len(items)} labelled claims "
          f"(threshold {threshold():.2f}). Lower is better.\n")
    print(f"  {'input format':<17} {'judge alone':>23}   {'with fixed rules':>23}")
    print(f"  {'':<17} {'false pass':>11} {'false flag':>11}   {'false pass':>11} {'false flag':>11}")
    results = {}
    for fmt in JUDGE_INPUTS:
        r = score(judge, items, fmt)
        results[fmt] = r
        sy = r["system"]
        print(f"  {fmt:<17} {len(r['false_passes']):>11} {len(r['false_flags']):>11}   "
              f"{len(sy['false_passes']):>11} {len(sy['false_flags']):>11}")
    best = min(results, key=lambda f: (len(results[f]["system"]["false_passes"]) * 2
                                       + len(results[f]["system"]["false_flags"]), f != DEFAULT_JUDGE_INPUT))
    sy = results[best]["system"]
    print(f"\nBest with rules (a false pass counts double): {best}")
    print(f"  still let through: {sy['false_passes']}   held back: {sy['false_flags']}")
    print(f"To use it, add to .env:  JUDGE_INPUT={best}")
    out = ROOT / "evals" / f"compare-{judge_kind}.json"
    out.write_text(json.dumps({"best": best, "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
