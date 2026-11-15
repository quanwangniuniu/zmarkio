"""
Local (fastembed/ONNX) embeddings provider -- MED-264 eval/local-dev unblock.

Temporary stand-in for core.services.gemini_embeddings when Gemini access is
blocked. Never used in production: rag.embeddings only calls into this
module when settings.RAG_EMBEDDING_PROVIDER == 'local', which is never the
case unless a developer or an eval run explicitly sets it -- the production
default stays 'gemini'.

Mirrors gemini_embeddings' two-function surface (embed_content /
batch_embed_contents) so rag.embeddings can dispatch between providers
without changing its own document/query framing logic. Deliberately uses
only TextEmbedding.embed() for both documents and queries -- never
fastembed's own query_embed()/passage_embed() (which inject a model-specific
prompt template) -- so the *only* framing applied to the text is the
existing _DOCUMENT_INSTRUCTION / _QUERY_INSTRUCTION prefix already added by
rag.embeddings before the text reaches either provider. That keeps
document/query framing semantics identical across providers instead of
stacking a second, provider-specific prefix on top, which would make a
Gemini vs. local retrieval-quality comparison compare two different framing
strategies instead of the same one on two encoders.

BAAI/bge-base-en-v1.5 outputs 768-dim vectors natively -- the same width as
DocumentChunk.embedding's VectorField -- so no padding/truncation logic
exists or is needed here, and RAG_EMBEDDING_DIMENSIONS does not change.
"""
from __future__ import annotations

from fastembed import TextEmbedding

_MODEL_CACHE: dict[str, TextEmbedding] = {}


class LocalEmbeddingResponseError(RuntimeError):
    """A local embedding call returned a vector of the wrong dimensionality."""


def _get_model(model: str) -> TextEmbedding:
    """Lazily construct and cache one TextEmbedding instance per model name
    for the lifetime of the process, so the ONNX model is loaded once rather
    than once per chunk/query.
    """
    cached = _MODEL_CACHE.get(model)
    if cached is None:
        cached = TextEmbedding(model_name=model)
        _MODEL_CACHE[model] = cached
    return cached


def _validate(vector, *, expected_dimensions: int) -> list[float]:
    values = [float(v) for v in vector]
    if len(values) != expected_dimensions:
        raise LocalEmbeddingResponseError(
            f"Local embedding model returned a {len(values)}-dim vector, expected {expected_dimensions}"
        )
    return values


def embed_content(text: str, *, model: str, dimensions: int, timeout: int | None = None) -> list[float]:
    """Embed a single text.

    `timeout` is accepted only for call-signature parity with
    gemini_embeddings.embed_content -- there is no network round-trip to
    bound here, so it is unused.
    """
    vector = next(iter(_get_model(model).embed([text])))
    return _validate(vector, expected_dimensions=dimensions)


def batch_embed_contents(
    texts: list[str], *, model: str, dimensions: int, timeout: int | None = None
) -> list[list[float]]:
    """Embed multiple texts, in the same order as `texts`."""
    if not texts:
        return []
    vectors = _get_model(model).embed(texts)
    return [_validate(vector, expected_dimensions=dimensions) for vector in vectors]
