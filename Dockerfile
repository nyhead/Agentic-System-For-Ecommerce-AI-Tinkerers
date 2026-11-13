# Proposed FastAPI deployment entrypoint: app.main:app.
# Adapt to the actual repository before enabling the pipeline.
FROM ghcr.io/astral-sh/uv:0.13.8 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .

RUN useradd --system --uid 10001 --create-home appuser
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
