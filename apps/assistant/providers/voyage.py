"""Voyage AI embeddings (multilingual; Anthropic's recommended embedding partner).

Plain HTTPS so there's no extra SDK dependency. Uses ``input_type`` so queries and
documents are embedded asymmetrically, as Voyage recommends for retrieval.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence

from django.conf import settings

from ..models import EMBEDDING_DIMENSIONS
from . import BaseEmbeddings, ProviderError

API_URL = "https://api.voyageai.com/v1/embeddings"
BATCH_SIZE = 64


class VoyageEmbeddings(BaseEmbeddings):
    dimensions = EMBEDDING_DIMENSIONS
    timeout = 30

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.VOYAGE_API_KEY
        self.model = model or settings.ASSISTANT_EMBEDDING_MODEL
        if not self.api_key:
            raise ProviderError("VOYAGE_API_KEY is not configured.")

    def _embed(self, texts: Sequence[str], input_type: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            body = json.dumps(
                {
                    "input": list(texts[start : start + BATCH_SIZE]),
                    "model": self.model,
                    "input_type": input_type,
                    "output_dimension": self.dimensions,
                }
            ).encode()
            request = urllib.request.Request(
                API_URL,
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout
                ) as response:  # noqa: S310
                    payload = json.load(response)
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                raise ProviderError(f"Voyage embeddings failed: {exc}") from exc
            data = sorted(payload["data"], key=lambda d: d["index"])
            vectors += [d["embedding"] for d in data]
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]
