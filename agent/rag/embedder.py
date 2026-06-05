from __future__ import annotations

"""Embedding providers for local RAG."""

from hashlib import sha256
import math
import re
from typing import Any, Protocol

from openai import OpenAI

from agent.config import AppConfig


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]:
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...


class DeterministicEmbeddingProvider:
    """Stable hashing-trick embedding used when no external API is configured."""

    def __init__(self, dimensions: int = 64) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0 for _ in range(self.dimensions)]
        for token in self._tokens(text):
            digest = sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    def _tokens(self, text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z0-9_.$/:-]+|[\u4e00-\u9fff]", text.lower())
        return tokens or [sha256(text.encode("utf-8")).hexdigest()]


class OpenAICompatibleEmbeddingProvider:
    """OpenAI-compatible embedding provider, enabled only when configured."""

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout_seconds: int = 60, dimensions: int = 0) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": texts,
            "timeout": self.timeout_seconds,
        }
        if self.dimensions > 0:
            kwargs["dimensions"] = self.dimensions
        response = self.client.embeddings.create(**kwargs)
        embeddings = [list(item.embedding) for item in response.data]
        if self.dimensions > 0:
            for embedding in embeddings:
                if len(embedding) != self.dimensions:
                    raise ValueError(f"Embedding dimension mismatch: expected {self.dimensions}, got {len(embedding)}")
        return embeddings


def build_embedding_provider(config: AppConfig) -> EmbeddingProvider:
    provider = config.rag.embedding_provider.strip().lower()
    api_key = config.rag.embedding_api_key.strip() or config.llm.api_key.strip()
    base_url = config.rag.embedding_base_url.strip() or config.llm.base_url.strip()
    model = config.rag.embedding_model.strip()
    if provider == "openai_compatible" and api_key and model:
        return OpenAICompatibleEmbeddingProvider(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_seconds=config.llm.timeout_seconds,
            dimensions=config.rag.embedding_dimensions,
        )
    return DeterministicEmbeddingProvider()
