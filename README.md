# Agentic System for Ecommerce

A Shopify merchant turns a product photo and rough notes into a draft listing, launch copy, and a follow-up recommendation.

| Owner | Branch | Demo deliverable |
| --- | --- | --- |
| Nihad | `product-agent` | Turn a photo and notes into a Shopify dev-store draft with an image. Own integration and the demo screen. |
| Ibrahim | `marketing-agent` | Generate an SEO title, meta description, and two social ad drafts for review. |
| Musa | `analytics-agent` | Show sessions, orders, conversion rate, and one suggested next action using labeled synthetic metrics. |

## Shared inputs

- Start with [the product fixture](fixtures/product.json). Keep `productId` stable across agents. The live listing fills in `shopifyProductId` and `imageUrl`.
- Marketing returns `seoTitle`, `metaDescription`, and two `socialAds` entries, each with `channel` and `copy`.
- Analytics joins [the sample metrics](fixtures/metrics.json) by `productId`. Divide orders by sessions and show no rate when sessions are zero. Keep synthetic data labeled.
- Use local environment variables for credentials. Create the draft in a dev store with Shopify's [`productCreate` mutation](https://shopify.dev/docs/api/admin-graphql/latest/mutations/productCreate), using `DRAFT` status and `write_products` access.

## Team workflow

Open one issue and one PR per workstream. Branch from `main` and post blockers in the team chat. Agree on fixture changes before merging. Run all three stages with the sample product, then verify the live Shopify draft and its photo in Admin. Rehearse the complete flow before the [18:00 Prague demo](https://luma.com/6rgvg844).

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

## Parallel development today

Each teammate clones the same repository in their own Ubuntu `~/code` folder and starts Codex/Claude inside that checkout. Keep the existing branches: Nihad `product-agent`, Ibrahim `marketing-agent`, Musa `analytics-agent`. See [AGENTS.md](AGENTS.md) for file ownership.

```bash
git switch main
git pull --ff-only
git switch -c product-agent  # choose your assigned branch instead
```

Nihad owns `app/state.py`, `app/graph.py`, dependency changes and final integration. Teammates implement their own nodes in `app/agents/`; start with the fixtures instead of waiting for Shopify. Merge small reviewed PRs every 30–45 minutes.

## CI and deployment

```bash
uv run ruff check .
uv run mypy app
uv run pytest -q
# Full CI equivalent; requires Docker running and curl available:
bash scripts/verify.sh
```

Every PR to `main` and every push to `main` runs the `quality` check: locked dependency installation, lint, type checking, offline tests, Docker build and container readiness. Live Shopify/model calls are not part of CI. Configure branch protection to require `quality` and a teammate review where your GitHub plan permits it.

```bash
docker build -t commercecrew .
docker run --rm -p 8000:8000 commercecrew
```

Deploy the Dockerfile to the team's chosen container host and set its health path to `/api/health`. The process honors the host's `PORT` variable (default 8000). Hosting and automatic deployment are not connected yet. Add secrets only in hosting settings, keep merchant mutation routes authenticated, and use a persistent checkpointer/database for deployed approval state. The container runs as a non-root user; app files are read-only to that user, so use external storage or an explicitly configured writable volume.

**Milestones (Prague):** first combined deployed flow by 14:00; real integrations by 15:00; feature freeze at **16:00**; rehearsal and working final links by **16:45**. Preserve the product → marketing → analytics scope already agreed above.
