"""The service: /api routes for the merchant cockpit, and the cockpit itself at /."""

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from app.api import analytics, chat, orders, products, research

# Static cockpit with no build step; ui/index.html is the entry point.
UI_DIR = Path(__file__).resolve().parents[1] / "ui"

app = FastAPI(title="CommerceCrew", version="0.1.0")


@app.middleware("http")
async def revalidate_cockpit_files(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Browsers re-check cockpit files on each load (304 when unchanged); deploys show at once."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response
app.include_router(analytics.router)
app.include_router(products.router)
app.include_router(chat.router)
app.include_router(orders.router)
app.include_router(research.router)


@app.get("/api")
def index() -> dict[str, str]:
    return {"name": "CommerceCrew", "docs": "/docs"}


@app.get("/api/health")
def health() -> dict[str, str]:
    """Readiness must work without external APIs or credentials."""
    return {"status": "ok"}


# Mounted last so /api, /docs and /openapi.json match first; every other path is a cockpit file.
app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")
