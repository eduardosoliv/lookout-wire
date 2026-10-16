# Image for Railway (or any container host). Runs the agent as a long-lived worker.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so a code-only change reuses this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev

# Run the script directly so SIGTERM from a redeploy reaches Python and it stops cleanly.
CMD ["/app/.venv/bin/lookout-wire"]
