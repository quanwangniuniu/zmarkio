"""
RAG-specific embedding calls (MED-264, MED-537).

Document and query embeddings share one provider and one model. The
production default is a local Ollama model (settings.RAG_OLLAMA_EMBEDDING_MODEL,
768-dim nomic-embed-text). 'local' remains the FastEmbed fallback. There is
no Gemini embedding provider.

settings.RAG_EMBEDDING_DIMENSIONS is the single width for both calls.
Neither function below takes a model or dimension override, so a document
vector and a query vector cannot be produced by different models.

Retrieval framing (document to be found vs. query looking for documents)
is encoded in the input text below. The exact wording is a placeholder
pending retrieval-quality tuning, isolated here so it can change without
touching the provider client or the indexing pipeline.
"""
from __future__ import annotations

from django.conf import settings

from core.services import local_embeddings, ollama_embeddings

# Placeholder phrasing pending eval-driven tuning. What matters for
# correctness right now is that document and query framing are distinct and
# applied consistently — not the exact wording.
_DOCUMENT_INSTRUCTION = "Represent this document for retrieval: "
_QUERY_INSTRUCTION = "Represent this query for retrieving relevant documents: "

# Bump whenever _DOCUMENT_INSTRUCTION / _QUERY_INSTRUCTION changes.
# rag.indexing.current_pipeline_hash() folds this in alongside the embedding
# model/dimensions and chunk size/overlap, so a wording change invalidates
# existing DocumentChunk rows exactly like a chunk-size retune would —
# content_hash alone can't see this, since the raw extracted text hasn't
# changed, only how it gets framed before embedding.
FRAMING_VERSION = "1"


def _provider():
    """Resolve settings.RAG_EMBEDDING_PROVIDER to (module, provider, model_name).

    'ollama' is the production default (core.services.ollama_embeddings).
    'local' is the FastEmbed fallback (core.services.local_embeddings).

    Framing (_DOCUMENT_INSTRUCTION / _QUERY_INSTRUCTION below) is applied
    identically regardless of provider -- only which module receives the
    already-framed text differs here.

    `provider` is returned (not just re-read from settings by callers) so
    active_embedding_identity() below builds its fingerprint from the exact
    same resolution this function used to dispatch.
    """
    provider = settings.RAG_EMBEDDING_PROVIDER
    if provider == 'ollama':
        return ollama_embeddings, provider, settings.RAG_OLLAMA_EMBEDDING_MODEL
    if provider == 'local':
        return local_embeddings, provider, settings.RAG_LOCAL_EMBEDDING_MODEL
    raise ValueError(
        f"Unknown RAG_EMBEDDING_PROVIDER {provider!r}; expected 'ollama' or 'local'."
    )


def active_embedding_identity() -> str:
    """Fingerprint input for the provider+model actually active right now.

    rag.indexing.current_pipeline_hash() folds this in, so a provider or
    model swap changes the hash. Every provider is qualified as
    f"{provider}:{model_name}" (for example 'ollama:nomic-embed-text').

    Deliberately stateless: built entirely from _provider()'s current
    resolution, never from a previously stored hash.
    """
    _, provider, model_name = _provider()
    return f"{provider}:{model_name}"


def embed_document_chunks(texts: list[str]) -> list[list[float]]:
    """Embed a batch of DocumentChunk texts. Order-preserving.

    Splits into groups of settings.RAG_EMBED_BATCH_SIZE so a single
    document's chunk count can't produce one unbounded request.
    """
    if not texts:
        return []

    framed = [_DOCUMENT_INSTRUCTION + text for text in texts]
    batch_size = settings.RAG_EMBED_BATCH_SIZE
    module, _, model_name = _provider()

    embeddings: list[list[float]] = []
    for start in range(0, len(framed), batch_size):
        batch = framed[start:start + batch_size]
        embeddings.extend(
            module.batch_embed_contents(
                batch,
                model=model_name,
                dimensions=settings.RAG_EMBEDDING_DIMENSIONS,
            )
        )
    return embeddings


def embed_query(text: str) -> list[float]:
    """Embed a single user query for retrieval.

    Raises ValueError for empty/whitespace-only input rather than silently
    embedding just the retrieval instruction on its own (which would be a
    well-formed but meaningless vector, not an error — worse than failing
    loudly, since it would look like a normal query result).
    """
    if not text or not text.strip():
        raise ValueError("Query text must not be empty")
    framed = _QUERY_INSTRUCTION + text
    module, _, model_name = _provider()
    return module.embed_content(
        framed,
        model=model_name,
        dimensions=settings.RAG_EMBEDDING_DIMENSIONS,
    )
