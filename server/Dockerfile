# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.8.4 AS uv

FROM python:3.13-slim-bookworm AS builder

COPY --from=uv /uv /uvx /bin/

# fyers-apiv3 historically pinned aiohttp 3.9.3 (sdist-only on Python 3.13).
# uv override-dependencies bump that series; keep a compiler for any remaining sdists.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY main.py run_worker.py ./
COPY app ./app
COPY scripts ./scripts
COPY db ./db
COPY ind_nifty500list.csv ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app \
    && chown appuser:appuser /app

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FYERS_LOG_PATH=/tmp

COPY --from=builder --chown=appuser:appuser /app /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
