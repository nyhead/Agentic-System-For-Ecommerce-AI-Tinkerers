"""The service: /api routes for the merchant cockpit, and the cockpit itself at /."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import analytics, chat, orders, products, research

# Static cockpit with no build step; ui/index.html is the entry point.
UI_DIR = Path(__file__).resolve().parents[1] / "ui"

app = FastAPI(title="CommerceCrew", version="0.1.0")
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
