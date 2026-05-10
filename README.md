# RouteBench

Route benchmarking tool that scores planned delivery/service routes across five dimensions, identifies specific inefficiencies with hypothesized causes, and benchmarks against a theoretical optimum.

## Architecture

Four-layer pipeline:

1. **Analysis tools (deterministic)** — Python functions that produce structured `Finding` objects
2. **Analysis orchestrator (agentic)** — Claude-powered loop selecting which analysis tools to run
3. **Report writer (agentic, narrow)** — Claude fills prose slots in a Jinja2 HTML template
4. **Verifier (deterministic)** — Ensures every claim maps to a structured finding

**Firewall principle:** every user-facing claim traces to a deterministic finding. The LLM rephrases and synthesizes; it never invents.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) — Python package manager
- Docker & Docker Compose — for OSRM
- An Anthropic API key (for the agent layer)

## Install

```bash
# Clone the repo
git clone https://github.com/rslayer/route-bench.git
cd route-bench

# Install dependencies
uv sync

# Copy and fill in your environment variables
cp .env.example .env
```

## OSRM Setup

RouteBench uses a self-hosted OSRM instance for driving distance/time matrices.

### One-time data preparation

Download a regional extract from [Geofabrik](https://download.geofabrik.de/) (start with Texas):

```bash
mkdir -p osrm-data
cd osrm-data

# Download the extract
wget https://download.geofabrik.de/north-america/us/texas-latest.osm.pbf

# Pre-process the data (this takes a few minutes)
docker run -t -v $(pwd):/data osrm/osrm-backend osrm-extract -p /opt/car.lua /data/texas-latest.osm.pbf
docker run -t -v $(pwd):/data osrm/osrm-backend osrm-partition /data/texas-latest.osrm
docker run -t -v $(pwd):/data osrm/osrm-backend osrm-customize /data/texas-latest.osrm

# Rename to the expected filename
mv texas-latest.osrm region.osrm
# Also rename all associated files
for f in texas-latest.osrm.*; do mv "$f" "region.osrm.${f#texas-latest.osrm.}"; done

cd ..
```

### Start OSRM

```bash
docker compose up osrm
```

Verify it's running:

```bash
curl "http://localhost:5000/table/v1/driving/-97.7431,30.2672;-96.7970,32.7767"
```

## Run Tests

```bash
uv run pytest
```

## Lint & Type Check

```bash
uv run ruff check src/
uv run mypy src/routebench
```

## Run the Streamlit App

*(Placeholder — available after Phase 8)*

```bash
uv run streamlit run src/routebench/app/streamlit_app.py
```

## Project Structure

```
route-bench/
├── src/routebench/
│   ├── core/           # Schemas, validation, config, exceptions
│   ├── infra/          # Matrix providers, storage, telemetry
│   ├── analysis/       # Scoring, diagnosis, benchmark, visuals
│   ├── report/         # Jinja2 templates, prose slots, PDF
│   ├── agent/          # Orchestrator, writer, verifier, prompts
│   └── app/            # Streamlit UI, pipeline, API stub
├── tests/
├── data/
├── scripts/
└── notebooks/
```
