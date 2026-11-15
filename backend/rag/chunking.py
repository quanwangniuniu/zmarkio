"""
Deterministic character-based chunking for RAG indexing (MED-264).

Pure text -> list[str] transformation: no DB access, no source-type
awareness, no citation logic, no embedding calls. Extraction has already
normalized the document text by the time it reaches here, so chunk
boundaries are computed on the exact string passed in — individual chunks
are never re-stripped, since that would silently shift the boundaries this
function is supposed to be computing deterministically.

Units are CHARACTERS, not tokens (no tokenizer is used). See
RAG_CHUNK_SIZE / RAG_CHUNK_OVERLAP in settings.py — changing either
invalidates every existing DocumentChunk (see rag.indexing.current_pipeline_hash).
"""
from __future__ import annotations

from django.conf import settings


def chunk_text(text: str) -> list[str]:
    """Split `text` into overlapping, deterministically-ordered chunks.

    step = RAG_CHUNK_SIZE - RAG_CHUNK_OVERLAP. settings.py already validates
    chunk_size > 0 and 0 <= overlap < chunk_size at startup, so step is
    always >= 1 and this loop always terminates — no need to re-validate here.
    """
    if not text or not text.strip():
        return []

    chunk_size = settings.RAG_CHUNK_SIZE
    overlap = settings.RAG_CHUNK_OVERLAP
    step = chunk_size - overlap

    length = len(text)
    chunks: list[str] = []
    start = 0
    while start < length:
        end = start + chunk_size
        chunks.append(text[start:end])  # slicing past `length` truncates gracefully -> tail chunk
        if end >= length:
            break
        start += step
    return chunks
