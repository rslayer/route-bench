"""Non-Streamlit CLI for running the pipeline.

Usage:
    uv run python scripts/run_local.py data/synthetic/sample.csv
    uv run python scripts/run_local.py data/synthetic/sample.csv --output ./output
    uv run python scripts/run_local.py data/synthetic/sample.csv \
        --config '{"traffic": "urban_us", "include_pdf": true}'
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run RouteBench pipeline on a CSV file")
    parser.add_argument("csv_path", type=Path, help="Path to the route CSV file")
    parser.add_argument("--output", type=Path, default=Path("./output"), help="Output directory")
    parser.add_argument("--include-pdf", action="store_true", help="Generate PDF report")
    parser.add_argument("--include-benchmark", action="store_true", default=True)
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help=(
            "AnalysisConfig as JSON, matching the API's config field. "
            'Anything set here wins over the flags, e.g. \'{"traffic": "urban_us"}\''
        ),
    )
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"Error: {args.csv_path} not found")
        sys.exit(1)

    from routebench.agent.client import LLMClient
    from routebench.app.pipeline import PipelineDeps, run_session
    from routebench.app.sessions import SessionState
    from routebench.core.config import AnalysisConfig, Settings
    from routebench.infra.matrix.osrm import OSRMMatrixProvider
    from routebench.infra.storage.local import LocalStorageBackend
    from routebench.infra.telemetry import Telemetry

    settings = Settings()
    session_id = uuid.uuid4().hex
    output_dir = args.output / session_id
    output_dir.mkdir(parents=True, exist_ok=True)

    storage = LocalStorageBackend(base_path=str(args.output))
    matrix_provider = OSRMMatrixProvider(host=settings.osrm_host)
    llm_client = LLMClient(api_key=settings.anthropic_api_key, model=settings.claude_model)
    telemetry = Telemetry(session_id=session_id)

    deps = PipelineDeps(
        matrix_provider=matrix_provider,
        storage=storage,
        llm_client=llm_client,
        settings=settings,
    )

    # Same shape the API accepts, so a profile can be exercised headlessly.
    config_data: dict[str, object] = {}
    if args.config:
        try:
            config_data = json.loads(args.config)
        except json.JSONDecodeError as exc:
            print(f"Error: --config is not valid JSON: {exc}")
            sys.exit(1)
    config_data.setdefault("include_benchmark", args.include_benchmark)
    config_data.setdefault("include_pdf", args.include_pdf)

    try:
        config = AnalysisConfig(**config_data)
    except ValueError as exc:
        print(f"Error: invalid config: {exc}")
        sys.exit(1)

    if config.traffic.is_active:
        print(f"Traffic profile: {len(config.traffic.bands)} band(s) active")

    async def on_progress(state: SessionState, pct: int, detail: str) -> None:
        print(f"  [{pct:3d}%] {state}: {detail}")

    print(f"Session: {session_id}")
    print(f"Input:   {args.csv_path}")
    print(f"Output:  {output_dir}")
    print()

    result = await run_session(
        session_id=session_id,
        upload_path=args.csv_path,
        config=config,
        deps=deps,
        telemetry=telemetry,
        on_progress=on_progress,
    )

    print()
    if result.state == "succeeded":
        print(f"Report: {output_dir / 'report.html'}")
        if result.cost:
            print(f"Cost:   ${result.cost.total_cost_usd:.4f}")
    else:
        print(f"Failed: {result.error_message}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
