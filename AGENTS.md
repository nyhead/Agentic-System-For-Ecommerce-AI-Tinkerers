# CommerceCrew team agreement

Build the Python LangGraph MVP in this repository. Delivery is 16:45 Prague on 13 November 2026; freeze features at 16:00. Official hand-in is 17:45 and demos start at 18:15, three minutes each.

These rules bind every teammate and every AI coding agent working in a teammate's checkout. Claude Code loads this file through `CLAUDE.md`; Codex and other agents read it directly. The [README](README.md) holds the product plan, layout and setup.

## Ownership

One owner per path. You, and the agent working for you, edit only your own lane.

| Paths | Owner |
| --- | --- |
| `app/agents/`, `app/tools/`, `app/graph.py`, `tests/test_agent_*.py`, `tests/test_tool_*.py` | Musa — agentic system and Shopify integration |
| `ui/` | Ibrahim — merchant cockpit dashboard |
| `.github/`, `scripts/`, `Dockerfile`, `.dockerignore`, `pyproject.toml`, `uv.lock`, `.env.example`, `app/main.py`, `app/api/`, `app/state.py`, `tests/test_api_*.py`, `tests/test_foundation.py` | Nihad — DevOps, deployment, HTTP wiring and shared contracts |
| `fixtures/`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `.gitignore` | Shared — change only in a PR that all three teammates have seen |

- Nihad does not own a feature agent. Nihad reviews every change to `app/graph.py`, because that is where the workstreams meet.
- Coordinate with Nihad before changing `app/state.py`, dependencies, CI or deployment. Only one dependency PR (`pyproject.toml` and `uv.lock`) is open at a time, and Nihad opens it.
- Appending a variable name to `.env.example` is allowed in any PR that introduces that variable.
- Do not change another owner's module without coordinating. If your task needs a change in another lane, stop and ask that owner; they make it in their own PR.

## Branching: one trunk

- `main` is the only long-lived branch. Keep `main` runnable: it is what gets deployed and demoed.
- Nobody pushes to `main` directly, and nobody force-pushes it.
- Branch per task from an up-to-date `main` as `<type>/<short-topic>`, where the type is `feat`, `fix`, `chore` or `docs`. Example: `feat/product-agent`.
- One task, one branch, one PR. Each teammate uses their own clone and coding session.
- Never branch from another feature branch. If you need unmerged work, ask its author to merge it first.
- Ship small pull requests every 30–45 minutes. A branch that lives longer than that is too big; split it.

```bash
git switch main
git pull --ff-only
git switch -c feat/<short-topic>
```

## Pull requests

1. Rebase on the trunk before opening the PR and again before merging: `git fetch origin && git rebase origin/main`. Use `git push --force-with-lease`, and only on your own branch.
2. Run `bash scripts/verify.sh` before requesting a merge. Without Docker, run at least `uv run ruff check .`, `uv run mypy app` and `uv run pytest -q`. Do not skip failing checks.
3. Title the PR like a commit: `<type>(<area>): <result>`, for example `feat(agents): draft a Shopify listing from a photo`. The area is `agents`, `tools`, `api`, `ui`, `ci` or `docs`.
4. Fill in the PR template. Keep the PR inside your lane; a PR that crosses lanes is split.
5. The CI `quality` check must be green.
6. One teammate approves. A PR that touches `app/state.py`, `fixtures/`, `app/graph.py` or the shape of an `/api` route needs Nihad and the teammate who consumes the change.
7. If nobody has reviewed a green PR that stays inside your lane after 10 minutes, merge it and post the link in the team chat.
8. The author merges with **squash and merge**, then deletes the branch.

## Merge order

When several PRs are open, merge them in this order. After every merge, each remaining PR rebases before it merges.

1. Shared contracts and inputs: `app/state.py`, `fixtures/`, dependencies.
2. Backend that implements the contract: `app/agents/`, `app/tools/`, `app/graph.py`, `app/api/`.
3. `ui/` changes that consume it.
4. Docs.

Within one tier, the oldest green PR goes first. The author of the later PR resolves conflicts by rebasing; never resolve a conflict by overwriting code in another lane.

If `main` breaks, nothing else merges until it is green again. Whoever merged the breaking PR fixes it or reverts it within 10 minutes.

After the 16:00 freeze, only `fix/` and `docs/` PRs merge.

## Rules for AI coding agents

- Before you write code, read this file, the README, `app/state.py` and `fixtures/`. Run `gh pr list` so that you do not duplicate open work.
- Work only in the lane of the teammate you are working for. Do not fix, reformat, rename or move files in another lane; report the problem to your teammate instead.
- Never commit on `main`. You may commit, push your task branch and open a PR. Merge only when your teammate tells you to.
- Do not add or upgrade dependencies. Tell your teammate, who asks Nihad.
- Do not weaken a check to make it pass: no skipped tests, no blanket `# type: ignore`, no edits to CI.
- Read the fixtures and shared models first. Work against fixtures while tools are being implemented. Label synthetic output.
- CI uses no live credentials. Tests never call Shopify, a model or any other live service; stub the tool instead.
- Keep credentials server-side and out of Git. The browser never holds a Shopify or model key.
- Real Shopify writes target a test store, require explicit merchant approval and must tolerate retries without duplicate writes.
