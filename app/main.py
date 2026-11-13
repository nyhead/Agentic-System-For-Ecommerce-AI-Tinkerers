"""Runnable service foundation; commerce agents are implemented by their owners."""

from fastapi import FastAPI

from app.api import analytics

app = FastAPI(title="CommerceCrew", version="0.1.0")
app.include_router(analytics.router)


@app.get("/")
def index() -> dict[str, str]:
    return {
        "name": "CommerceCrew",
        "status": "service foundation; commerce agents not connected yet",
        "docs": "/docs",
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    """Readiness must work without external APIs or credentials."""
    return {"status": "ok"}
