"""Text embeddings behind a small interface, so tests and evals can swap the model."""
from __future__ import annotations

import hashlib
import re
from typing import Protocol

import numpy as np

from . import config


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


def normalize(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    if m.ndim == 1:
        n = np.linalg.norm(m)
        return m / n if n else m
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1
    return m / n


class FastEmbedder:
    """BAAI/bge-small-en-v1.5 via fastembed (ONNX, CPU, no PyTorch). 384 dimensions."""

    def __init__(self, model: str | None = None, cache_dir: str | None = None):
        from fastembed import TextEmbedding  # imported lazily so tests don't need the model
        self.name = model or config.EMBED_MODEL
        self._m = TextEmbedding(self.name, cache_dir=cache_dir or config.EMBED_CACHE)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        return normalize(np.array(list(self._m.embed(texts, batch_size=64))))


class HashEmbedder:
    """Deterministic bag-of-words embedder for tests: shared words → high cosine."""

    name = "hash-bow-256"
    STOP = set("a an the of to in on for and or with at by from as is are be after over into new says say".split())

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in self.STOP]
        for w in words:
            h = int(hashlib.md5(w.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        return v

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return normalize(np.stack([self._vec(t) for t in texts]))


def article_text(title: str, description: str = "") -> str:
    return f"{title}. {description}".strip(". ") if description else title
