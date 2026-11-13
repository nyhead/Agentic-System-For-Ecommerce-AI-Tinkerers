# Merchant cockpit

Owner: Ibrahim. This is the dashboard the shop owner uses and the screen the team demos. It is the only thing a non-technical merchant sees, so every agent action shows up here in plain language.

## Run it

```bash
python3 -m http.server 5173 --directory ui
```

Open http://localhost:5173. Serve the folder over HTTP rather than opening `index.html` as a file: the runtime re-reads the page with `fetch`. The browser needs internet access, because React 18.3.1 loads from unpkg and the Archivo font from Google Fonts.

Everything on screen is demo data for now: simulated agents, the sample "Atelier Store" and a sample watch. Demo state persists in the browser's `localStorage` under `commercecrew-demo-v4`; **⋯ → Reset demo…** clears it.

The demo follows two rules the real agents should keep. **Add product** takes only photos: the Product agent writes the listing, the Research agent suggests a price from comparable listings, and quantity starts at 1, all editable at review. **Conversations** are agent-first: the assigned agent answers the customer and hands the conversation to the merchant only when it can't answer.

## Stack

The first version was designed in Claude Design and exported as a static page. There is no build step and no package manager.

- `index.html` holds the whole cockpit: an `<x-dc>` template (HTML with `{{ }}` bindings, `<sc-if>` and `<sc-for>`) and a `class Component extends DCLogic` script with the state and handlers.
- `vendor/dc-runtime.js` turns that template into React components at load time.

**For deployment (Nihad):** build command: none. Output folder: `ui/` as is, with `index.html` as the entry point. Any static host works, as does mounting the folder from FastAPI.

## Layout

```text
ui/
├── index.html              the cockpit: template, styles and component logic
├── js/
│   ├── demo.js             window.CCDemo: seed data and simulated agents (the API swap point)
│   └── icons.js            <cc-icon name="" size="">, a Lucide subset
├── vendor/                 third-party; do not edit
│   ├── dc-runtime.js       Claude Design runtime (generated)
│   └── modernist/          design system stylesheet and its guide
└── assets/                 sample watch photos used by the demo data
```

## Connecting the API

The page reads data and calls agents only through `window.CCDemo` in `js/demo.js`. `CCDemo.seed()` builds the demo store; `CCDemo.agents.generateListing`, `reviseListing` and `retryTask` fake agent work with timers. To go live, replace those functions with `fetch` calls to the `/api` routes and keep their return shapes, or adapt the callers in `index.html`.

## Editing notes

- Leave `vendor/` untouched. `index.html` overrides the Modernist theme tokens (accent, radii) at the top of its `<style>` block, so the page intentionally departs from `vendor/modernist/README.md`.
- `<cc-icon>` draws its SVG into a shadow root. Do not switch it back to `innerHTML`: the runtime builds its first render from the live page, and React owns the element's children, so a light-DOM SVG crashes the page with `removeChild … not a child of this node`.
- The runtime calls `componentDidUpdate(prevProps)` with one argument. React's second argument, `prevState`, never arrives, so the component keeps its own `this._prevState`.
- Re-exporting from Claude Design overwrites both fixes above and the file layout. Re-apply them after any re-export.
- On load, the server log shows 404s for URLs like `/{{ im.src }}`. The browser requests the raw template's image placeholders before the runtime replaces them. This is expected and harmless.

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
- The stack is recorded under [Stack](#stack). Tell Nihad when the build command or output folder changes, so that CI and deployment can follow.

The root `.gitignore` re-includes `lib/` folders under `ui/`, because the Python rules ignore `lib/` everywhere else. Build output in `dist/`, `build/` and `.next/` stays ignored.
