# Production serving image for Cloud Run — 2026-08-10, CIE CRITICAL fix.
# Distinct from Dockerfile.dev (the interactive dev container this whole
# build happened inside): that one ends in `CMD ["/bin/bash"]` and was never
# meant to serve traffic. This one runs the actual FastAPI app.
#
# The Day-0 spike (Step 6) deployed fine with `adk deploy cloud_run`'s
# auto-generated single-agent template because it shipped one hello-world
# agent. This app is a 5-agent FastAPI service with a Gateway, Model Armor,
# and a dashboard — the auto-template doesn't fit it (flagged in Step 7's
# build notes and never actioned until now).
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv, for a fast install from the already-verified lockfile (uv.lock) —
# no separate `uv sync` step re-resolving versions at build time.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN useradd --create-home --shell /bin/bash app
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# --frozen: fail the build rather than silently re-resolving if uv.lock is
# out of sync with pyproject.toml. --no-dev: excludes pytest from the
# production image (smaller image, no test tooling in the serving surface).
RUN uv sync --frozen --no-dev && chown -R app:app /app

USER app
ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8080
CMD ["uvicorn", "fleet_hackathon.app:app", "--host", "0.0.0.0", "--port", "8080"]
