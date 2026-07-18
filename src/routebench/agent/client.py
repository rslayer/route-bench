"""Claude API wrapper with telemetry and retry logic."""

from __future__ import annotations

import time
from typing import Any

import anthropic
import structlog

from routebench.infra.telemetry import Telemetry

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

_RETRYABLE_STATUS = {429, 500, 502, 503, 529}
_MAX_RETRIES = 3


class LLMResponse:
    """Wrapper around Anthropic API response."""

    def __init__(
        self,
        content: list[dict[str, Any]],
        stop_reason: str | None,
        input_tokens: int,
        output_tokens: int,
        model: str,
    ) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model = model

    @property
    def text(self) -> str:
        """Extract text content from response."""
        parts: list[str] = []
        for block in self.content:
            if block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts)

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        """Extract tool use blocks from response."""
        return [b for b in self.content if b.get("type") == "tool_use"]

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMClient:
    """Wrapper around the Anthropic SDK with telemetry."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-opus-4-8",
        telemetry: Telemetry | None = None,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._telemetry = telemetry
        self._api_key = api_key

    @property
    def available(self) -> bool:
        """Whether this client can actually reach the API.

        The LLM is an enhancement here, not a requirement: it selects which
        deterministic analyzers to run and writes the narrative prose. Every
        number a user comes for — metrics, findings, benchmark, the grade — is
        computed without it. Callers branch on this to run the deterministic
        path rather than failing, so a missing key degrades the report instead
        of taking the service down.
        """
        return bool(self._api_key)

    def generate(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        turn_id: str | None = None,
        slot_id: str | None = None,
    ) -> LLMResponse:
        """Call Claude API with retry logic and telemetry.

        No `temperature`. Sampling parameters (temperature/top_p/top_k) were
        removed on current Claude models and a request carrying one is rejected
        with a 400 — so the 0.2 this used to send would have failed every call,
        not merely nudged the sampling. The determinism it was reaching for is
        better served by the prompt and by the verifier, which already checks
        every number in the generated prose against the source data.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                start = time.monotonic()
                response = self._client.messages.create(**kwargs)
                latency = time.monotonic() - start

                # Build response
                content: list[dict[str, Any]] = []
                for block in response.content:
                    if block.type == "text":
                        content.append({"type": "text", "text": block.text})
                    elif block.type == "tool_use":
                        content.append(
                            {
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                        )

                result = LLMResponse(
                    content=content,
                    stop_reason=response.stop_reason,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    model=self._model,
                )

                if self._telemetry:
                    self._telemetry.record_llm_call(
                        model=self._model,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        latency_seconds=latency,
                        slot_id=slot_id,
                        turn_id=turn_id,
                    )

                return result

            except anthropic.APIStatusError as e:
                last_error = e
                if e.status_code in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                    wait = 2**attempt
                    logger.warning(
                        "llm_retryable_error",
                        status=e.status_code,
                        attempt=attempt + 1,
                        wait_seconds=wait,
                    )
                    time.sleep(wait)
                    continue
                raise

        if last_error:
            raise last_error
        msg = "Unreachable"
        raise RuntimeError(msg)
