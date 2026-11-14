"""Claude chat provider (official ``anthropic`` SDK, tool runner).

* Model ``claude-opus-5-5`` by default (``ASSISTANT_LLM_MODEL``). Thinking is
  always on for this model; ``effort: low`` keeps a short chat answer fast.
* Tools are passed to the SDK's tool runner, which runs the call → result loop.
  Each tool is a closure bound to one salon (see ``apps.assistant.chat``), so the
  model can't reach another tenant's data whatever it puts in the tool input.
* Server-side ``fallbacks: "default"`` re-runs a refused request on a suitable
  model; a refusal that survives is reported as ``refused``.
"""

from __future__ import annotations

import logging

import anthropic
from anthropic import beta_tool
from django.conf import settings

from . import BaseLLM, ChatRequest, ChatResult, ProviderError

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_ITERATIONS = 4


class AnthropicLLM(BaseLLM):
    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        # Credentials come from ANTHROPIC_API_KEY (or another SDK-supported source).
        self.client = client or anthropic.Anthropic(timeout=60.0, max_retries=2)

    def _tools(self, request: ChatRequest) -> list:
        return [
            beta_tool(
                spec.func,
                name=spec.name,
                description=spec.description,
                input_schema=spec.input_schema,
                strict=True,
            )
            for spec in request.tools
        ]

    def answer(self, request: ChatRequest) -> ChatResult:
        try:
            runner = self.client.beta.messages.tool_runner(
                model=settings.ASSISTANT_LLM_MODEL,
                max_tokens=16000,
                system=request.system,
                messages=request.messages,
                tools=self._tools(request),
                max_iterations=MAX_TOOL_ITERATIONS,
                output_config={"effort": "low"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            message = runner.until_done()
        except anthropic.RateLimitError as exc:
            logger.warning("Claude rate limited (request %s)", exc.request_id)
            raise ProviderError("rate limited") from exc
        except anthropic.APIStatusError as exc:
            logger.error(
                "Claude API error %s (request %s)", exc.status_code, exc.request_id
            )
            raise ProviderError(str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            logger.error("Claude connection error: %s", exc)
            raise ProviderError("connection error") from exc

        if message.stop_reason == "refusal":
            return ChatResult(text="", refused=True)
        text = "".join(block.text for block in message.content if block.type == "text")
        return ChatResult(text=text.strip())
