# CommerceCrew team agreement

Build the Python LangGraph MVP in this repository. Delivery is 16:45 Prague on 13 November 2026; freeze features at 16:00.

- Nihad: `product-agent` branch; `app/agents/product.py`, `app/tools/shopify.py`, demo composition and final integration.
- Ibrahim: `marketing-agent` branch; `app/agents/marketing.py` and its tests/component.
- Musa: `analytics-agent` branch; `app/agents/analytics.py` and its tests/component.
- Nihad coordinates changes to `app/state.py`, the eventual `app/graph.py`, `pyproject.toml`, `uv.lock`, CI and deployment.
- Each teammate uses their own clone and coding session. Ship small pull requests every 30–45 minutes.
- Read the fixtures and shared models first. Work against fixtures while tools are being implemented. Label synthetic output.
- Keep credentials server-side and out of Git. Real Shopify writes target a test store, require explicit merchant approval and must tolerate retries without duplicate writes.
- CI uses no live credentials. Run `bash scripts/verify.sh` before requesting a merge. Do not skip failing checks.
- Do not change another owner's module without coordinating. Keep `main` runnable.
