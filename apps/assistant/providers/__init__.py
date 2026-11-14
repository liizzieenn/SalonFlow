"""Pluggable LLM and embedding providers.

Configured by dotted path, like Django's email backends::

    ASSISTANT_LLM_BACKEND = "apps.assistant.providers.anthropic.AnthropicLLM"
    ASSISTANT_EMBEDDING_BACKEND = "apps.assistant.providers.voyage.VoyageEmbeddings"

The ``fake`` providers need no network or keys and are deterministic, so they
are the defaults for development and tests.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string


class ProviderError(Exception):
    """The provider failed (network, quota, bad config). Shown as 'unavailable'."""


@dataclass(frozen=True)
class ContextItem:
    """A retrieved knowledge chunk, numbered for citations ([1], [2], ...)."""

    number: int
    source: str
    source_id: int
    title: str
    content: str


@dataclass(frozen=True)
class ToolSpec:
    """A function the model may call. ``func`` takes the tool input as kwargs."""

    name: str
    description: str
    input_schema: dict[str, Any]
    func: Callable[..., str]


@dataclass
class ChatRequest:
    system: str
    #: Prior turns + the current user turn, as ``{"role", "content"}`` dicts.
    messages: list[dict[str, Any]]
    tools: list[ToolSpec] = field(default_factory=list)
    # Structured view of the same request (used by the fake provider).
    question: str = ""
    language: str = "en"
    context: list[ContextItem] = field(default_factory=list)
    salon_phone: str = ""


@dataclass(frozen=True)
class ChatResult:
    text: str
    refused: bool = False


class BaseLLM:
    def answer(self, request: ChatRequest) -> ChatResult:  # pragma: no cover
        raise NotImplementedError


class BaseEmbeddings:
    model: str = ""
    dimensions: int = 0

    def embed_documents(
        self, texts: Sequence[str]
    ) -> list[list[float]]:  # pragma: no cover
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:  # pragma: no cover
        raise NotImplementedError


@cache
def _load(path: str):
    return import_string(path)()


def get_llm() -> BaseLLM:
    return _load(settings.ASSISTANT_LLM_BACKEND)


def get_embeddings() -> BaseEmbeddings:
    return _load(settings.ASSISTANT_EMBEDDING_BACKEND)


def reset_providers() -> None:
    """Forget cached provider instances (after changing settings in tests)."""
    _load.cache_clear()
