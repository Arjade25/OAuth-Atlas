# OAuth Atlas

Evidence-backed guides to creating and getting approval for OAuth apps. Built for Composio's APM (toolkits) take-home.

Getting credentials is easy; getting approved is not. Each guide answers four questions up front: how hard is this path, how many hours will I spend versus how long will I wait on the provider, what could block me, and which steps an agent can do for me. Every claim links to the passage it came from.

## What's here (Day 1)

- **One schema for every guide** (`atlas/schema.py`). Evidence lives on each claim: URL, exact short quote, retrieval date, source type and the result of three checks.
- **Scenarios, not apps, get scored** (`atlas/scoring.py`). The same app can be "Self-serve" in testing and "Audited" in production. The 1 to 5 score is the highest of five dimensions: technical effort, prerequisites, approval, external uncertainty and maintenance. Each scenario also has a state: available now, testing only, or blocked until approved.
- **Deterministic checks** (`atlas/evidence.py`, `atlas/policy.py`). The quote must appear on the stored page. A numeric wait time needs a number in its source, so "several weeks" can never become "21 days". A path can't be "available now" on an unverified claim. Conflicting claims, broken references and stale evidence are caught.
- **Three hand-verified seed guides** (`guides/`): GitHub (self-serve), Slack (light review to Marketplace review) and Gmail (testing to restricted-scope verification plus annual security assessment).
- **Library and guide pages** plus a JSON API (`atlas/app.py`).

Coming next: the research agent on the Composio SDK that writes new guides, and the support check that decides whether a quote actually backs its claim.

## Run it

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

pytest                         # 47 tests
python -m atlas.library        # validate every guide against the policy rules
uvicorn atlas.app:app --reload # then open http://127.0.0.1:8000
```

Day 1 needs no API keys. The agent will read them from a local `.env` (copy `.env.example`), which is git-ignored.

## API

- `GET /api/guides`: every guide with its scenarios, states and scores
- `GET /api/guides/{slug}`: one full guide as JSON, with computed scores

## Branches

- `main`: the most up-to-date branch; everything lands here
- `own-test`: work on the main development machine
- `comp-test`: work on the secondary machine

Each machine branch pulls from `main` before starting work.
