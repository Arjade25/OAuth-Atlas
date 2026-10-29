# OAuth Atlas

Evidence-backed guides to creating and getting approval for OAuth apps. Built for Composio's APM (toolkits) take-home.

Getting credentials is easy; getting approved is not. Each guide answers four questions up front: how hard is this path, how many hours will I spend versus how long will I wait on the provider, what could block me, and which steps an agent can do for me. Every claim links to the passage it came from.

## Try it

- **Live demo:** https://oauth-atlas-18r4.onrender.com. The free host sleeps when idle, so the first load can take up to a minute.
- **No keys needed to review:** every guide, the JSON API and the three recorded agent runs (Pipedrive, Notion, LinkedIn) work without any API keys. Open **Write a new guide** and pick a recorded run to watch the full pipeline replay: search, fetch, passage selection, drafting and all three checks.
- **Live research** for a new app needs a Composio key and an OpenRouter key, set on the server or in `.env` locally. On the demo it is capped at a few runs an hour, one at a time, and uses the free OpenRouter model as the support judge, since the local NLI model does not fit the free host.

## What's here

**The library**
- **One schema for every guide** (`atlas/schema.py`). Evidence lives on each claim: URL, exact short quote, retrieval date, source type and the result of three checks.
- **Scenarios, not apps, get scored** (`atlas/scoring.py`). The same app can be "Self-serve" in testing and "Audited" in production. The 1 to 5 score is the highest of five dimensions: technical effort, prerequisites, approval, external uncertainty and maintenance. Each scenario also has a state: available now, testing only, or blocked until approved.
- **Three hand-verified seed guides** (`guides/`): GitHub, Slack and Gmail, from self-serve to restricted-scope verification plus an annual security assessment.

**The research agent** (`atlas/pipeline.py`)

```
app name or website
  -> search (Composio COMPOSIO_SEARCH_WEB; only result URLs are used, never the search summary)
  -> fetch up to 8 pages, provider docs first (COMPOSIO_SEARCH_FETCH_URL_CONTENT)
  -> pick about 28 relevant passages (BM25, no model)
  -> draft the guide (free OpenRouter model; must quote passages verbatim)
  -> check every claim three ways, independently:
       1. quote exists   the quote is really in a passage the model was given
       2. supports       a separate judge (Laya, or another model) says the passage backs the claim
       3. policy         deterministic rules: every number in a claim is in its quote,
                         no numeric wait without a number in the source, a deadline
                         or expiry is never shown as a wait, and more
  -> assemble: wait times kept only if a verified claim with a number backs them,
     named blockers force a blocked state, a step you take yourself ("agree to the
     terms", "submit for review") is not a blocker while a real review or approval
     remains, unknown references are dropped and reported
```

A claim is verified only if all three checks pass. Generated guides are shown with what the checks found and stay "not yet reviewed" until a person approves them. Model confidence is never used as a stand-in for evidence.

"Verified" means the claim passed these checks against its quoted passage. It does not mean the claim is beyond doubt: the checks are only as good as the quote, and the support judge still lets some overstatements through (see the measurements below).

**What the numbers on a guide mean**
- **Waiting on the provider** is shown only when a verified claim gives a duration. A deadline for someone else ("your admin has 30 days to verify") or an expiry is not a wait, and is flagged if filed as one. "None" (no wait) needs a cited claim that there is no review or approval; a source that simply never mentions review does not count. Where a number exists but is not a verified wait, the page says "No verified duration"; where the provider gives none, "Not published".
- **Your work** (hours) is always an estimate, never measured. Curated guides carry the author's estimate and generated guides the model's; the page and the API (`effort_basis`) say which.
- **Blocked until approved** names the review or approval that blocks the path. A conditional requirement, such as Gmail's security assessment (needed only if restricted data touches your servers), is listed on the path but does not block it on its own.

Every live run is recorded in `runs/<app>.json` (searches, the passages the model saw, its replies, the judge's verdicts), so it can be replayed with no keys.

## Run it

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

pytest                         # 118 tests, no network
python -m atlas.library        # validate the curated guides
uvicorn atlas.app:app --reload # then open http://127.0.0.1:8000
```

### Generate a guide

Copy `.env.example` to `.env` and add `COMPOSIO_API_KEY` (a project key starting with `ak_`) and `OPENROUTER_API_KEY`. `.env` is git-ignored.

```bash
python -m atlas.generate Pipedrive          # or a website: pipedrive.com
python -m atlas.generate Pipedrive --replay # re-run the recording, no keys
```

Or open `/generate` in the app and watch it work. Results land in `generated/` and appear in the library.

### Choosing the support judge

The judge is the weakest of the three checks, so it is chosen by measurement. `evals/judge_set.json` holds 22 hand-labelled claim/passage pairs from the Pipedrive run: 12 supported, 6 that overstate their passage, 4 contradicted. Score any judge with:

```bash
python -m atlas.judge_eval --judge nli    # local NLI model (pip install torch transformers sentencepiece protobuf)
python -m atlas.judge_eval --judge laya   # local Laya (pip install "pydecide[laya]")
python -m atlas.judge_eval --judge llm    # free OpenRouter chat model
```

It reports false passes (unsupported claims let through, the costly error) and false flags (good claims held back), for the judge alone and for the judge plus the fixed rules, which is what decides "verified" on the page.

**Measured so far** (22 claims; small, so read these as direction, not proof):

| Judge | Judge alone: false pass / false flag | With fixed rules: false pass / false flag |
| --- | --- | --- |
| Free chat model (Inkling, then Dots3-Note), live run, 18 of the claims | 5 / 1 | 2 / 1 |
| NLI, `cross-encoder/nli-deberta-v3-base`, local | 4 / 1 | 2 / 1 |

The NLI judge is the default: same result with the rules, but it also caught all 4 contradicted claims, runs locally in about half a second per claim, costs nothing and has no rate limits. Its input format was also chosen by measurement (`--compare-inputs`): the lowercased context window beat the cased window, adding the source name, and the quote's sentence alone.

What still gets through: claims that call something "required" when the quote uses "needs" or "must" in another sense (for example "Access scopes are required" from "specify precisely what data access your application needs"). Neither judge nor rule catches these yet, which is why generated guides stay "not yet reviewed" until a person approves them.

## Known limitations

- The three curated guides were checked by hand. Their quotes are short excerpts, so two Gmail claims lean on the section the quote sits in rather than the quote alone: that internal-only use is a verification exception, and which scopes count as restricted. Open the linked page to confirm both.
- Generated guides show flagged claims and policy errors on the page by design. They are what the checks found, not something to hide.
- Effort hours are estimates, not measurements.
- Six apps, sixteen paths. The point is the method, not coverage.

### Deploy your own copy

`render.yaml` describes a free Render web service. In Render, choose **New > Blueprint**, connect this repo and pick the branch. It deploys in replay mode; add `COMPOSIO_API_KEY` and `OPENROUTER_API_KEY` in the service's Environment tab to turn on live research. `LIVE_RUNS_PER_HOUR` (default 6) caps live runs.

## API

- `GET /api/guides`: every guide with its scenarios, states and scores
- `GET /api/guides/{slug}`: one full guide as JSON, with computed scores
- `GET /generate/stream?app=<name>[&replay=1]`: server-sent progress events for a generation run

## Branches

- `own-test`: work on the main development machine
- `comp-test`: work on the secondary machine
- `main`: the merged result, updated only by merging a working branch when it's ready

Work is never committed directly to `main`.
