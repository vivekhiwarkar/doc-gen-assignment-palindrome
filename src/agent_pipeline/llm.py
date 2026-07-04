"""A thin, swappable LLM backend.

The rest of the pipeline talks to :class:`LLMClient` and never imports the OpenAI SDK
directly, so the model provider is a single seam. ``complete`` returns free text;
``complete_json`` forces a JSON object back and parses it. Both retry transient failures.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from openai import OpenAI
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

logger = logging.getLogger(__name__)

# Errors worth retrying. insufficient_quota surfaces as RateLimitError but is *not*
# transient, so we special-case it below rather than retry it into the ground.
_RETRYABLE = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)


class LLMError(RuntimeError):
    """Raised when a completion cannot be produced after retries."""


class LLMClient:
    """OpenAI-backed chat client with retries and an optional JSON mode.

    Swapping providers means writing another class with the same two methods; nothing
    else in the pipeline changes.
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        client: OpenAI | None = None,
        max_retries: int = 3,
        temperature: float = 0.2,
    ) -> None:
        self._client = client or OpenAI()
        self._model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self._max_retries = max_retries
        self._temperature = temperature

    @property
    def model(self) -> str:
        return self._model

    def complete(self, system: str, user: str) -> str:
        """Return the model's free-text reply."""
        message = self._call(system, user, json_mode=False)
        return message.strip()

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Return a parsed JSON object. The prompt must ask for a single JSON object."""
        raw = self._call(system, user, json_mode=True)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise LLMError(f"model did not return valid JSON: {raw[:200]!r}") from exc

    def _call(self, system: str, user: str, *, json_mode: bool) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "temperature": self._temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                if content is None:
                    raise LLMError("model returned empty content")
                return content
            except RateLimitError as exc:
                # Quota exhaustion is terminal; retrying only wastes time and money.
                if _is_quota_error(exc):
                    raise LLMError(
                        "OpenAI request failed: insufficient quota. Add billing/credit to the "
                        "key in .env (see README) or supply a funded key."
                    ) from exc
                last_exc = exc
            except _RETRYABLE as exc:
                last_exc = exc
            if attempt < self._max_retries:
                backoff = 2 ** (attempt - 1)
                logger.warning("LLM call failed (attempt %d), retrying in %ss", attempt, backoff)
                time.sleep(backoff)
        raise LLMError(f"LLM call failed after {self._max_retries} attempts") from last_exc


def _is_quota_error(exc: RateLimitError) -> bool:
    body = getattr(exc, "body", None)
    if isinstance(body, dict) and body.get("code") == "insufficient_quota":
        return True
    return "insufficient_quota" in str(exc)
