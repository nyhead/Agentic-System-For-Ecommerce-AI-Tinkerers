# CommerceCrew — Agentic System for Ecommerce

CommerceCrew is an AI operations team for a small Shopify shop. The owner uploads a product photo and rough notes. Specialist agents prepare the draft listing, the launch copy and a follow-up recommendation, and the owner approves every consequential action from one cockpit dashboard.

Built in the room at [AI Agents for the Rest of Us](https://luma.com/6rgvg844), AI Tinkerers Prague, 13 November 2026.

## Today (Prague time)

| Time | Milestone |
| --- | --- |
| 14:00 | First combined flow runs on the deployed URL, on fixtures. |
| 15:00 | Real Shopify and model calls replace the fixtures. |
| **16:00** | Feature freeze. Fixes and docs only. |
| **16:45** | Flow rehearsed. GitHub and demo links work and are on the [hackathon form](https://hackathon.computergeneratedreality.com/register). |
| 17:45 | Official hand-in. |
| 18:15 | Demos: three minutes, hard stop, live, no slides. |

The [rules page](https://hackathon.computergeneratedreality.com/rules) gives 17:45 and 18:15; Luma says demos start at 18:00. Follow what the organizers announce in the room.

What the rules and judges ask for:

- Build it in the room. Ask an organizer before reusing code written before today, such as Musa's earlier listing agent.
- Put a GitHub link and a demo or site link on the form.
- Judges score three things: it solves a real problem for a real person; it works and uses the sponsor tools well (Apify, Featherless, PostHog); people outside tech would use it.

## Team

| Who | Role | Owns | First tasks |
| --- | --- | --- | --- |
| **Musa** | Agentic system and Shopify integration | `app/agents/`, `app/tools/`, `app/graph.py` | Create the Shopify dev store and API credentials. Build `tools/shopify.py`, then the product agent, then marketing and analytics. |
| **Ibrahim** | Merchant cockpit UI | `ui/` | Pick the UI stack and scaffold `ui/`. Build the upload, review-and-approve, launch-copy and analytics screens against the fixtures. Switch to the API when the routes land. |
| **Nihad** | DevOps, deployment, HTTP wiring, shared contracts; unblocks the other two | `.github/`, `scripts/`, `Dockerfile`, dependencies, `app/main.py`, `app/api/`, `app/state.py` | Add the `/api` routes, returning fixture data first. Deploy the container and the UI to get the demo URL. Hold the secrets on the host. Add PostHog events. |

Nihad does not own a feature agent and reviews every change to `app/graph.py`. [AGENTS.md](AGENTS.md) has the exact path ownership and the rules every teammate and AI coding agent follows.

## What we are building

One Python service (FastAPI and LangGraph) runs the agents. The cockpit in `ui/` is the only thing the merchant sees, and it talks only to the service's `/api` routes. The service holds every credential and makes every Shopify, model, Apify and PostHog call.

| Priority | Agent | Module | What it does | Integration |
| --- | --- | --- | --- | --- |
| P0 | Product | `app/agents/product.py` | Turns a photo and merchant notes into a draft listing. Publishes after the merchant approves. | Vision model, Shopify Admin GraphQL |
| P0 | Marketing | `app/agents/marketing.py` | Writes an SEO title, a meta description and two social ad drafts for review. | Text model |
| P0 | Analytics | `app/agents/analytics.py` | Shows sessions, orders and conversion rate, and proposes one next action. | Labeled synthetic metrics, then PostHog |
| P1 | Triage | `app/agents/triage.py` | Routes a customer message to support, sales or the merchant. | Text model |
| P1 | Support | `app/agents/support.py` | Answers product and policy questions from the catalog. | Shopify catalog |
| P1 | Sales | `app/agents/sales.py` | Recommends a variant and returns a checkout link. | Shopify Storefront API cart |
| P2 | Research | `app/agents/research.py` | Collects source-backed findings from public product pages. | Apify Actor |

P0 is the scope the team already agreed: product, then marketing, then analytics, with merchant approval in the cockpit. Start P1 only after P0 runs end to end on the deployed URL. P2 is a stretch. Live ad publishing, refunds, fulfillment and order lookup are out of scope today.

The model reaches the service through one adapter, `app/tools/llm.py`, configured by `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL`. The product agent needs a model that accepts images, so confirm that on the chosen provider before building on it.

## Repository layout

```text
.
├── AGENTS.md              rules for teammates and AI coding agents
├── CLAUDE.md              points Claude Code at AGENTS.md
├── app/                   Python service: FastAPI and LangGraph
│   ├── main.py            FastAPI app and /api/health            Nihad
│   ├── state.py           shared Pydantic contracts              Nihad
│   ├── api/               HTTP routes the cockpit calls          Nihad
│   ├── graph.py           LangGraph wiring of the agents         Musa   (to add)
│   ├── agents/            one module per agent                   Musa
│   └── tools/             shopify.py, llm.py, apify.py, ...      Musa
├── ui/                    merchant cockpit dashboard             Ibrahim
├── fixtures/              sample product and metrics             shared
├── tests/                 offline tests, flat, one file per module
├── scripts/               verify and CI helpers                  Nihad
├── .github/               CI workflow and PR template            Nihad
├── Dockerfile             deployable container                   Nihad
└── pyproject.toml, uv.lock                                       Nihad
```

Name tests after the module they cover so that owners never share a test file: `tests/test_agent_product.py`, `tests/test_tool_shopify.py`, `tests/test_api_products.py`.

## Cockpit API (v0 proposal)

Nihad and Ibrahim confirm these routes in the first API PR. Request and response bodies are the models in `app/state.py`; the service publishes the schema at `/openapi.json`, so the UI can generate its types from it.

| Route | Purpose | Priority |
| --- | --- | --- |
| `GET /api/health` | Readiness. Exists today. | — |
| `POST /api/products/draft` | Photo and notes in, draft `ProductRecord` out with approval `pending`. | P0 |
| `POST /api/products/{productId}/approval` | Merchant approves or rejects. Approval publishes the listing. | P0 |
| `POST /api/products/{productId}/marketing` | Returns a `MarketingResult` for review. | P0 |
| `GET /api/products/{productId}/analytics` | Metrics, conversion rate and one recommended action. | P0 |
| `GET /api/events` | Action log: which agent acted, which tool ran, what needs approval. | P0 |
| `POST /api/chat` | Customer message in; triage routes it to support or sales. | P1 |

## Shared inputs

- Start with [the product fixture](fixtures/product.json). Keep `productId` stable across agents. The live listing fills in `shopifyProductId` and `imageUrl`.
- Marketing returns `seoTitle`, `metaDescription`, and two `socialAds` entries, each with `channel` and `copy`.
- Analytics joins [the sample metrics](fixtures/metrics.json) by `productId`. Divide orders by sessions and show no rate when sessions are zero. Keep synthetic data labeled.
- Use local environment variables for credentials. Create the draft in a dev store with Shopify's [`productCreate` mutation](https://shopify.dev/docs/api/admin-graphql/latest/mutations/productCreate), using `DRAFT` status and Admin API version `2026-10`. The store's app needs the scopes `write_products` to create the draft, and `read_publications` and `write_publications` to publish it to the Online Store.

## Run the shared Python foundation

The repository uses Python 3.12, LangGraph, FastAPI and a committed uv lockfile. The initial service exposes `/api/health` and `/docs`; the commerce agents and merchant dashboard are the team's implementation work.

```bash
# Ubuntu: install uv once if missing, from its official installer.
curl -LsSf https://astral.sh/uv/0.13.8/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

uv sync --locked --group dev
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open http://localhost:8000/docs. Copy `.env.example` to an ignored `.env` only when implementing the actual integrations; the skeleton does not load env files automatically or require API credentials.

## How we work

One trunk. `main` is the only long-lived branch and always runs. Every task gets a short branch from `main` and one small PR, merged by squash within 30–45 minutes. GitHub allows only squash merges here and deletes the branch after the merge.

```bash
git switch main
git pull --ff-only
git switch -c feat/<short-topic>
```

Each teammate uses their own clone and their own Codex or Claude session inside it. Post blockers in the team chat. The full rules are in [AGENTS.md](AGENTS.md): path ownership, PR checks, review, merge order and what AI agents may and may not do. The short version:

1. Stay in your lane. Ask the owner for changes outside it.
2. Rebase on `origin/main` and run `bash scripts/verify.sh` before you open a PR and before you merge.
3. Merge shared contracts first, then backend, then UI, then docs. After every merge, the other open PRs rebase.
4. If `main` breaks, nothing else merges until it is fixed or reverted.

Start with the fixtures instead of waiting for Shopify. Before the demo, run the full flow with the sample product, then verify the live Shopify draft and its photo in Admin.

## CI and deployment

```bash
uv run ruff check .
uv run mypy app
uv run pytest -q
# Full CI equivalent; requires Docker running and curl available:
bash scripts/verify.sh
```

Every PR to `main` and every push to `main` runs the `quality` check: locked dependency installation, lint, type checking, offline tests, Docker build and container readiness. Live Shopify/model calls are not part of CI. CI does not cover `ui/` yet; Nihad adds that job when the UI scaffold lands. Configure branch protection to require `quality` and a teammate review where your GitHub plan permits it.

```bash
docker build -t commercecrew .
docker run --rm -p 8000:8000 commercecrew
```

Deploy the Dockerfile to the team's chosen container host and set its health path to `/api/health`. The process honors the host's `PORT` variable (default 8000). Hosting and automatic deployment are not connected yet. Add secrets only in hosting settings, keep merchant mutation routes authenticated, and use a persistent checkpointer/database for deployed approval state. The container runs as a non-root user; app files are read-only to that user, so use external storage or an explicitly configured writable volume.

## Tools and accounts

| Tool | Use today |
| --- | --- |
| Claude Max (two seats), ChatGPT Plus | Coding agents in each teammate's checkout, review and demo prep. They are not the runtime model; the service calls a model API with its own key. |
| Featherless | Runtime model, paid from the event credits. |
| Apify | Research agent (P2). The [Actor development skill](https://github.com/apify/agent-skills/tree/main/skills/apify-actor-development) helps a coding agent build the Actor. |
| PostHog | Cockpit and storefront events for the analytics agent. |
| Figma | Ibrahim's cockpit sketch. Timebox it, then build. |
| Higgsfield | Optional product media. Keep it off the demo's critical path. |

Paperclip, Jev and Harness² are not part of the MVP.

## Open items

- **Nihad:** the repository is private. Judges need to open the GitHub link, so make it public, or invite them, before 16:45.
- **Nihad and Ibrahim:** decide whether the service serves the built UI (one URL) or the UI deploys separately (then the service needs CORS).
- **Musa:** confirm with an organizer that the earlier listing-agent code may be reused.
- **All:** decide who holds the registration edit link and who presents the demo.
