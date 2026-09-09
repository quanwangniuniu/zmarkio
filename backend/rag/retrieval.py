"""
Retrieval over DocumentChunk for a single project (MED-264 retrieval phase).

Exact pgvector cosine distance (sequential scan) over non-deleted,
non-null-embedding chunks — no ANN index, reranking, or hybrid/keyword
retrieval yet. Must be called with the correct tenant schema already active
on the current DB connection, same convention as rag.indexing.

Retrieval framing for the query text is already applied by
rag.embeddings.embed_query (the "Represent this query for retrieving
relevant documents: " prefix) — this module only consumes that vector, it
does not re-frame it.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from pgvector.django import CosineDistance

from rag.embeddings import embed_query
from rag.models import DocumentChunk


@dataclass(frozen=True)
class RetrievedChunk:
    content: str
    source_type: str
    source_id: str
    chunk_index: int
    citation_metadata: dict
    distance: float

    @property
    def similarity(self) -> float:
        """1 - cosine distance. Only meaningful because distance here is
        always CosineDistance -- would not hold for L2/inner-product."""
        return 1.0 - self.distance


def retrieve_chunks(
    project_id: int,
    question: str,
    *,
    top_k: int | None = None,
    min_similarity: float | None = None,
) -> list[RetrievedChunk]:
    """Embed `question` and return the top-K most similar chunks for `project_id`.

    - Only non-deleted chunks with a non-null embedding are considered.
    - Ranked by pgvector cosine distance, ascending (most similar first).
    - Ties broken deterministically by (source_type, source_id, chunk_index)
      so repeated calls against unchanged data return byte-identical
      results -- required for eval reproducibility, not just cosmetic.
    - `top_k` falls back to settings.RAG_RETRIEVAL_TOP_K when omitted, and
      must be positive either way.
    - `min_similarity` falls back to settings.RAG_RETRIEVAL_MIN_SIMILARITY
      (None = no threshold filtering) when omitted. Applied as a post-filter
      in Python since the underlying query is already a full ordered scan
      (no ANN index to protect).
    """
    if top_k is None:
        top_k = settings.RAG_RETRIEVAL_TOP_K
    if top_k <= 0:
        raise ValueError(f"top_k must be positive, got {top_k}")

    if min_similarity is None:
        min_similarity = settings.RAG_RETRIEVAL_MIN_SIMILARITY

    query_vector = embed_query(question)

    qs = (
        DocumentChunk.objects.filter(
            project_id=project_id,
            is_deleted=False,
            embedding__isnull=False,
        )
        .annotate(distance=CosineDistance('embedding', query_vector))
        .order_by('distance', 'source_type', 'source_id', 'chunk_index')[:top_k]
    )

    results = [
        RetrievedChunk(
            content=row.content,
            source_type=row.source_type,
            source_id=row.source_id,
            chunk_index=row.chunk_index,
            citation_metadata=row.citation_metadata,
            distance=float(row.distance),
        )
        for row in qs
    ]

    if min_similarity is not None:
        results = [r for r in results if r.similarity >= min_similarity]

    return results
