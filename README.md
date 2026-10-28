# OAuth Atlas

Evidence-backed guides to creating and getting approval for OAuth apps. Built for Composio's APM (toolkits) take-home.

Getting credentials is easy; getting approved is not. Each guide answers four questions up front: how hard is this path, how many hours will I spend versus how long will I wait on the provider, what could block me, and which steps an agent can do for me. Every claim links to the passage it came from.

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
                         no numeric wait without a number in the source, and more
  -> assemble: wait times kept only if a verified claim with a number backs them,
     named blockers force a blocked state, unknown references are dropped and reported
```

A claim is verified only if all three checks pass. Generated guides are shown with what the checks found and stay "not yet reviewed" until a person approves them. Model confidence is never used as a stand-in for evidence.

Every live run is recorded in `runs/<app>.json` (searches, the passages the model saw, its replies, the judge's verdicts), so it can be replayed with no keys.

## Run it

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

pytest                         # 74 tests, no network
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

It reports false passes (unsupported claims let through, the costly error) and false flags (good claims held back). In the first live run the chat-model judge let all 5 overstated claims through and held back 1 good one, which is why `auto` now prefers a local NLI model.

## API

- `GET /api/guides`: every guide with its scenarios, states and scores
- `GET /api/guides/{slug}`: one full guide as JSON, with computed scores
- `GET /generate/stream?app=<name>[&replay=1]`: server-sent progress events for a generation run

## Branches

- `own-test`: work on the main development machine
- `comp-test`: work on the secondary machine
- `main`: the merged result, updated only by merging a working branch when it's ready

Work is never committed directly to `main`.
