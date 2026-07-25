# RouteBench API image.
#
# One process: the FastAPI app under uvicorn. It used to run FastAPI and a
# legacy Streamlit UI side by side under supervisord, with Streamlit exposed
# publicly on a second port with no auth. The Next.js web app replaced Streamlit,
# so that whole surface is gone — one process, one port, no supervisor.
#
# The web frontend is a separate image (web/Dockerfile); this builds the API only.

# ---- Builder stage ----
# Pinned to bookworm (Debian 12). The bare `python:3.12-slim` tag follows Debian
# stable, which rolled to trixie (13) and renamed apt packages the runtime stage
# installs (libgdk-pixbuf2.0-0 -> libgdk-pixbuf-2.0-0), breaking the build with
# no code change. Pin the suite so the image is reproducible; bump deliberately.
FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src/ src/
COPY scripts/ scripts/
COPY data/ data/

# ---- Runtime stage ----
FROM python:3.12-slim-bookworm AS runtime

# WeasyPrint's native libraries, for PDF report rendering. No supervisor now.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi8 \
    shared-mime-info \
    fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/scripts /app/scripts
COPY --from=builder /app/data /app/data
COPY --from=builder /app/pyproject.toml /app/pyproject.toml

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

# Build identity for GET /health and the web footer. A running container has no
# .git directory, so the commit has to be baked in at build time:
#   docker build --build-arg GIT_SHA=$(git rev-parse HEAD) .
#   fly deploy --build-arg GIT_SHA=$(git rev-parse HEAD)
# Absent, /health reports commit "unknown" rather than guessing.
ARG GIT_SHA=""
ENV GIT_SHA=${GIT_SHA}

RUN mkdir -p /app/data/sessions /app/data/samples

# Binds $PORT when the platform injects one (Railway/Render/Heroku), else 8000.
# Fly sets internal_port instead and does not inject $PORT, so the default holds
# there. Shell form so ${PORT} is expanded at runtime, not frozen at build.
ENV PORT=8000
EXPOSE 8000
CMD uvicorn routebench.app.api.app:create_app --factory --host 0.0.0.0 --port ${PORT}
