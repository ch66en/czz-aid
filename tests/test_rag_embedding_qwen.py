from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.config import AppConfig
from agent.rag.embedder import DeterministicEmbeddingProvider, OpenAICompatibleEmbeddingProvider, build_embedding_provider


class FakeEmbeddings:
    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        texts = kwargs["input"]
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * self.dimensions) for _ in texts])


def test_openai_compatible_embedding_passes_dimensions_when_configured() -> None:
    provider = OpenAICompatibleEmbeddingProvider(
        api_key="key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="text-embedding-v4",
        dimensions=1024,
    )
    fake = FakeEmbeddings(1024)
    provider.client = SimpleNamespace(embeddings=fake)  # type: ignore[assignment]

    vectors = provider.embed_batch(["a", "b"])

    assert len(vectors) == 2
    assert fake.calls[0]["dimensions"] == 1024


def test_openai_compatible_embedding_omits_dimensions_when_zero() -> None:
    provider = OpenAICompatibleEmbeddingProvider(
        api_key="key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="text-embedding-v4",
        dimensions=0,
    )
    fake = FakeEmbeddings(3)
    provider.client = SimpleNamespace(embeddings=fake)  # type: ignore[assignment]

    provider.embed_batch(["a"])

    assert "dimensions" not in fake.calls[0]


def test_embedding_dimension_mismatch_fails_early() -> None:
    provider = OpenAICompatibleEmbeddingProvider(
        api_key="key",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="text-embedding-v4",
        dimensions=1024,
    )
    provider.client = SimpleNamespace(embeddings=FakeEmbeddings(3))  # type: ignore[assignment]

    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        provider.embed_batch(["a"])


def test_openai_compatible_without_key_falls_back_to_deterministic() -> None:
    config = AppConfig(
        rag={
            "embedding_provider": "openai_compatible",
            "embedding_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "embedding_api_key": "",
            "embedding_model": "text-embedding-v4",
        },
        llm={"api_key": ""},
    )

    provider = build_embedding_provider(config)

    assert isinstance(provider, DeterministicEmbeddingProvider)
