"""Report writer — Claude fills prose slots from structured findings."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import structlog

from routebench.agent.client import LLMClient
from routebench.report.prose_slots import ProseSlot

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.md").read_text()


class ReportWriter:
    """Claude-powered report writer that fills prose slots."""

    def __init__(
        self,
        client: LLMClient,
        max_workers: int = 4,
    ) -> None:
        self._client = client
        self._max_workers = max_workers

    def fill_slots(
        self,
        slots: list[ProseSlot],
    ) -> dict[str, str]:
        """Fill all prose slots in parallel.

        Returns a dict mapping slot_id to generated prose.
        """
        results: dict[str, str] = {}

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures = {
                executor.submit(self._fill_one_slot, slot): slot.slot_id
                for slot in slots
            }
            for future in futures:
                slot_id = futures[future]
                try:
                    results[slot_id] = future.result()
                except Exception:
                    logger.exception("slot_fill_error", slot_id=slot_id)
                    results[slot_id] = f"[Error generating {slot_id}]"

        return results

    def _fill_one_slot(self, slot: ProseSlot) -> str:
        """Fill a single prose slot by calling the LLM."""
        system_prompt = _load_prompt(slot.prompt_template)

        user_content = self._build_user_message(slot)

        response = self._client.generate(
            messages=[{"role": "user", "content": user_content}],
            system=system_prompt,
            slot_id=slot.slot_id,
            max_tokens=1024,
        )

        return response.text

    def _build_user_message(self, slot: ProseSlot) -> str:
        """Build the user message for a slot."""
        data_str = json.dumps(slot.input_data, indent=2, default=str)

        parts = [
            f"Generate the {slot.slot_type} section.",
            f"Word budget: {slot.word_budget} words.",
        ]

        if slot.required_references:
            refs = ", ".join(slot.required_references)
            parts.append(f"You MUST reference these finding IDs: {refs}")

        parts.append(f"\nInput data:\n{data_str}")

        return "\n".join(parts)
