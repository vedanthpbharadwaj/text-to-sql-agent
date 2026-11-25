"""LLM client used by the agents.

Every structured step (table selection, SQL generation, review) is an OpenAI
function call with `tool_choice` forced to a single function, so the model
always returns arguments that match a JSON schema instead of free text we
would have to parse.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Any, Protocol

from openai import OpenAI


class LLM(Protocol):
    def call_function(self, system: str, user: str, function: dict[str, Any]) -> dict[str, Any]:
        """Force the model to call `function` and return its parsed arguments."""

    def complete(self, system: str, user: str) -> str:
        """Plain text completion."""


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0


class OpenAILLM:
    def __init__(self, model: str, api_key: str | None = None, temperature: float = 0.0) -> None:
        self.model = model
        self.temperature = temperature
        self.client = OpenAI(api_key=api_key or None)
        self.usage = Usage()
        self._lock = threading.Lock()

    def _record(self, response: Any) -> None:
        if response.usage is None:
            return
        with self._lock:
            self.usage.prompt_tokens += response.usage.prompt_tokens
            self.usage.completion_tokens += response.usage.completion_tokens
            self.usage.calls += 1

    def call_function(self, system: str, user: str, function: dict[str, Any]) -> dict[str, Any]:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[{"type": "function", "function": function}],
            tool_choice={"type": "function", "function": {"name": function["name"]}},
        )
        self._record(response)
        message = response.choices[0].message
        if not message.tool_calls:
            raise RuntimeError(f"Model did not call {function['name']}")
        return json.loads(message.tool_calls[0].function.arguments)

    def complete(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        self._record(response)
        return (response.choices[0].message.content or "").strip()
