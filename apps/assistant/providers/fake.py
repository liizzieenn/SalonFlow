"""Offline, deterministic providers for development and tests.

* :class:`FakeEmbeddings` — feature hashing of words and character trigrams. No
  semantics, but texts sharing words ("manicure", "მანიკური") land close
  together, which is enough to exercise retrieval end to end.
* :class:`FakeLLM` — a rule-based stand-in for the chat model: it answers from the
  top retrieved item, calls ``find_free_slots`` for booking questions, cites with
  ``[n]`` markers and answers in Georgian or English like the real one.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from ..models import EMBEDDING_DIMENSIONS
from . import BaseEmbeddings, BaseLLM, ChatRequest, ChatResult

TOKEN_RE = re.compile(r"\w+", re.UNICODE)

BOOKING_WORDS = (
    "book", "appointment", "available", "free", "slot", "when", "time", "today",
    "tomorrow", "ჩაწერ", "ჯავშ", "თავისუფ", "დრო", "როდის", "ხვალ", "დღეს",
)  # fmt: skip


class FakeEmbeddings(BaseEmbeddings):
    model = "fake-hash-v1"
    dimensions = EMBEDDING_DIMENSIONS

    def _features(self, text: str) -> list[str]:
        words = TOKEN_RE.findall(text.lower())
        grams = [w[i : i + 3] for w in words if len(w) > 3 for i in range(len(w) - 2)]
        return words + grams

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for feature in self._features(text):
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FakeLLM(BaseLLM):
    def answer(self, request: ChatRequest) -> ChatResult:
        ka = request.language == "ka"
        question = request.question.lower()
        services = [c for c in request.context if c.source == "service"]
        tool = next((t for t in request.tools if t.name == "find_free_slots"), None)

        if tool and services and any(w in question for w in BOOKING_WORDS):
            item = services[0]
            tool.func(service_id=item.source_id)
            if ka:
                return ChatResult(
                    f"აი, „{item.title}“-ის უახლოესი თავისუფალი დროები [{item.number}]."
                )
            return ChatResult(
                f"Here are the next free times for {item.title} [{item.number}]."
            )

        if request.context:
            item = request.context[0]
            body = (
                item.content.split("\nA: ", 1)[-1]
                if item.source == "faq"
                else item.content
            )
            snippet = " ".join(body.split())[:300]
            return ChatResult(f"{snippet} [{item.number}]")

        phone = f" {request.salon_phone}" if request.salon_phone else ""
        if ka:
            return ChatResult(f"სამწუხაროდ, ეს ინფორმაცია არ მაქვს. დაგვირეკეთ{phone}.")
        return ChatResult(
            f"Sorry, I don't have that information. Please call the salon{phone}."
        )
