"""
RAG-specific embedding calls (MED-264).

Wraps core.services.gemini_embeddings with the two things that are specific
to *this* use case, not to "calling Gemini" in general:

- settings.RAG_EMBEDDING_MODEL / RAG_EMBEDDING_DIMENSIONS are the single
  source of truth here — neither function below takes a model/dimension
  override. That's what actually enforces "document and query embeddings
  always use the same model and dimensionality": pgvector only rejects a
  literal width mismatch, a same-width model swap would fail silently, so
  this has to be a code-level guarantee, not a schema-level one.
- gemini-embedding-2 has no `task_type` parameter, so retrieval framing
  (this is a document to be found vs. this is a query looking for
  documents) is encoded directly into the input text below. The exact
  wording is a placeholder pending real retrieval-quality tuning — it's
  isolated to this module specifically so eval work can change it without
  touching the provider client or the indexing pipeline.
"""
from __future__ import annotations

from django.conf import settings

from core.services import gemini_embeddings, local_embeddings

# Placeholder phrasing pending eval-driven tuning. What matters for
# correctness right now is that document and query framing are distinct and
# applied consistently — not the exact wording.
_DOCUMENT_INSTRUCTION = "Represent this document for retrieval: "
_QUERY_INSTRUCTION = "Represent this query for retrieving relevant documents: "

# Bump whenever _DOCUMENT_INSTRUCTION / _QUERY_INSTRUCTION (or the framing
# strategy itself, e.g. switching to a real task_type if Gemini ever
# supports one again) changes. rag.indexing.current_pipeline_hash() folds
# this in alongside the embedding model/dimensions and chunk size/overlap,
# so a wording change invalidates existing DocumentChunk rows exactly like a
# chunk-size retune would — content_hash alone can't see this, since the
# raw extracted text hasn't changed, only how it gets framed before embedding.
FRAMING_VERSION = "1"


def _provider():
    """Resolve settings.RAG_EMBEDDING_PROVIDER to (module, model_name).

    'gemini' is the only production-supported value and is what every
    existing caller gets by default -- this branch preserves the exact
    prior behavior (same module, same settings.RAG_EMBEDDING_MODEL). 'local'
    is a temporary MED-264 eval/local-dev unblock (see
    core.services.local_embeddings) and is never selected unless a developer
    or an eval run explicitly sets RAG_EMBEDDING_PROVIDER=local.

    Framing (_DOCUMENT_INSTRUCTION / _QUERY_INSTRUCTION below) is applied
    identically regardless of provider -- only which module receives the
    already-framed text differs here.
    """
    provider = settings.RAG_EMBEDDING_PROVIDER
    if provider == 'gemini':
        return gemini_embeddings, settings.RAG_EMBEDDING_MODEL
    if provider == 'local':
        return local_embeddings, settings.RAG_LOCAL_EMBEDDING_MODEL
    raise ValueError(
        f"Unknown RAG_EMBEDDING_PROVIDER {provider!r}; expected 'gemini' or 'local'."
    )


def embed_document_chunks(texts: list[str]) -> list[list[float]]:
    """Embed a batch of DocumentChunk texts. Order-preserving.

    Splits into groups of settings.RAG_EMBED_BATCH_SIZE so a single
    document's chunk count can't produce one unbounded request.
    """
    if not texts:
        return []

    framed = [_DOCUMENT_INSTRUCTION + text for text in texts]
    batch_size = settings.RAG_EMBED_BATCH_SIZE
    module, model_name = _provider()

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
    module, model_name = _provider()
    return module.embed_content(
        framed,
        model=model_name,
        dimensions=settings.RAG_EMBEDDING_DIMENSIONS,
    )
