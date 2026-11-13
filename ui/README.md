# Merchant cockpit

Owner: Ibrahim. This is the dashboard the shop owner uses and the screen the team demos. It is the only thing a non-technical merchant sees, so every agent action shows up here in plain language.

## Screens

| Priority | Screen | Shows |
| --- | --- | --- |
| P0 | New product | Photo upload and a notes field. |
| P0 | Review and approve | The draft listing from the product agent, with Approve and Reject. |
| P0 | Launch copy | SEO title, meta description and two social ad drafts for review. |
| P0 | Analytics | Sessions, orders, conversion rate and one recommended next action. Synthetic numbers carry a visible "synthetic" label. |
| P0 | Action log | Which agent acted, which tool ran, what it returned and what waits for approval. |
| P1 | Customer chat | A demo chat where triage routes the question to support or sales. |

## Rules for this directory

- Talk only to the service's `/api` routes, listed in the [root README](../README.md#cockpit-api-v0-proposal). Never call Shopify or a model from the browser, and never put a key in UI code.
- Read the API base URL from one environment variable, defaulting to `http://localhost:8000`.
- Generate types from the service's `/openapi.json` rather than retyping the models in `app/state.py`.
- Build against `../fixtures/` until a route exists, then switch that screen to the route.
- Show no conversion rate when sessions are zero.
- The stack is Ibrahim's choice in the first `ui/` PR. Tell Nihad the build command and output folder so that CI and deployment can pick them up.

The root `.gitignore` re-includes `lib/` folders under `ui/`, because the Python rules ignore `lib/` everywhere else. Build output in `dist/`, `build/` and `.next/` stays ignored.
