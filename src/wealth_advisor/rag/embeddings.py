"""Embedders. The default is a deterministic hashing embedder (no model download, offline)."""

import re
import zlib
from typing import Protocol

import numpy as np

_STOP = {"the", "a", "an", "of", "for", "is", "are", "and", "or", "to", "in", "on", "what", "does"}


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashingEmbedder:
    """Signed feature hashing over unigrams+bigrams, L2-normalised. Lexical, not semantic."""

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim
        self.name = f"hashing-{dim}"

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            toks = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP]
            feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
            for f in feats:
                h = zlib.crc32(f.encode())
                out[i, h % self.dim] += 1.0 if (h >> 16) & 1 else -1.0
            norm = np.linalg.norm(out[i])
            if norm:
                out[i] /= norm
        return out
