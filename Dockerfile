# ---- Builder stage ----
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv
RUN pip install uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy source
COPY src/ src/
COPY scripts/ scripts/
COPY data/ data/

# ---- Runtime stage ----
FROM python:3.12-slim AS runtime

# WeasyPrint system deps + supervisor
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi8 \
    shared-mime-info \
    fonts-dejavu \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy venv and source from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/scripts /app/scripts
COPY --from=builder /app/data /app/data
COPY --from=builder /app/pyproject.toml /app/pyproject.toml

# Copy supervisord config
COPY supervisord.conf /etc/supervisor/conf.d/routebench.conf

# Add venv to PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

# Build identity for GET /health and the web footer. A running container has no
# .git directory, so the commit has to be baked in at build time:
#   docker build --build-arg GIT_SHA=$(git rev-parse HEAD) .
#   fly deploy --build-arg GIT_SHA=$(git rev-parse HEAD)
# Absent, /health reports commit "unknown" rather than guessing.
ARG GIT_SHA=""
ENV GIT_SHA=${GIT_SHA}

# Create data directories
RUN mkdir -p /app/data/sessions /app/data/samples

EXPOSE 8000 8501

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/routebench.conf"]
