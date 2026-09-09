from django.conf import settings
from django.db import models
from pgvector.django import VectorField

from core.models import TimeStampedModel


class DocumentSourceType(models.TextChoices):
    MEETING = 'meeting', 'Meeting'
    NOTION_DRAFT = 'notion_draft', 'Notion Draft'
    RETROSPECTIVE = 'retrospective', 'Retrospective'


class DocumentChunk(TimeStampedModel):
    """
    A single embeddable chunk of text sourced from a project document
    (meeting, Notion draft, or retrospective), scoped to one project.

    Shared across all RAG sources instead of one table per source type, so
    retrieval only ever queries one table and stays trivially project-scoped.
    `source_id` is a string rather than a typed FK because source PKs differ
    in type across apps (Meeting/Draft use int PKs, RetrospectiveTask uses a
    UUID PK) — a generic identifier avoids one FK column per source type.

    Incremental indexing, and what this table does/doesn't give you for free
    ------------------------------------------------------------------------
    The incremental unit is the *source document*, not the individual chunk.
    Re-indexing a source re-chunks that document only — it never triggers a
    full rebuild of other documents' chunks (see `source_updated_at` below
    for how a document is judged unchanged and skipped entirely).

    When a document IS re-chunked, the indexer must reconcile the *entire*
    chunk set for that `(project, source_type, source_id)`, not just write
    the new chunks: if the re-chunked document produces fewer chunks than
    before (e.g. 5 -> 3), the old rows at `chunk_index` 3 and 4 are now stale
    and must be explicitly deleted, or they'd linger as retrievable garbage
    that no longer corresponds to any real position in the source.

    The `(project, source_type, source_id, chunk_index)` uniqueness below
    only provides a stable key for the indexer to upsert a chunk against —
    it does not itself implement incremental indexing or prune stale rows.
    That reconciliation (diff old chunk count vs new, delete the remainder)
    is the indexing service's responsibility, not this model's.
    """

    project = models.ForeignKey(
        'core.Project',
        on_delete=models.CASCADE,
        related_name='document_chunks',
    )
    source_type = models.CharField(
        max_length=20,
        choices=DocumentSourceType.choices,
    )
    source_id = models.CharField(
        max_length=64,
        help_text="String form of the source row's PK (int or UUID depending on source_type).",
    )
    chunk_index = models.PositiveIntegerField(
        help_text="Position of this chunk within the source document, for stable re-assembly/citation.",
    )
    content = models.TextField()
    embedding = VectorField(
        dimensions=settings.RAG_EMBEDDING_DIMENSIONS,
        null=True,
        blank=True,
        help_text="Null until the indexing pipeline embeds this chunk.",
    )
    source_updated_at = models.DateTimeField(
        help_text=(
            "updated_at of the source row as of this chunk's last (re-)embedding. "
            "The indexer compares this against the source's live updated_at to "
            "decide whether the document is unchanged and can be skipped, or "
            "must be re-chunked."
        ),
    )
    citation_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Source-specific fields needed to render a citation (e.g. title, url, timestamp).",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'source_type', 'source_id', 'chunk_index'],
                name='rag_chunk_unique_source_position',
            ),
        ]
        indexes = [
            models.Index(
                fields=['project', 'source_type', 'source_id'],
                name='rag_chunk_prj_src',
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_type}:{self.source_id}#{self.chunk_index} (project={self.project_id})"


class DocumentIndexStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    COMPLETE = 'complete', 'Complete'
    FAILED = 'failed', 'Failed'


class DocumentIndexState(models.Model):
    """
    One bookkeeping row per `(project, source_type, source_id)` recording
    whether that source document's chunks are up to date.

    This is the only thing an indexing run may trust to decide "skip, nothing
    to do" — never `DocumentChunk` rows themselves, and never a source's own
    `updated_at` alone (a child row like `MeetingDocument`/`ContentBlock`/
    `Insight` can change the extracted text without touching the parent's
    `updated_at`). Two independent guards must both match:

    - `indexed_content_hash`: sha256 of the exact text that was chunked and
      embedded last time. Compared against a fresh extraction's hash to
      detect real content changes regardless of which row (parent or child)
      changed.
    - `indexed_pipeline_hash`: fingerprint of the indexing pipeline's own
      configuration (embedding model/dimensions, chunk size/overlap, an
      explicit indexing-logic version — see `rag.indexing.current_pipeline_hash`).
      Content can be byte-identical to last time while the pipeline that
      would process it has changed (e.g. chunk size tuned during eval work);
      that must still force a re-index, which `indexed_content_hash` alone
      cannot detect.

    `status` must also be `complete` — `pending` (a run is in flight or was
    interrupted) and `failed` never count as "nothing to do," regardless of
    whether the hashes happen to match.

    `indexed_source_updated_at` is kept only for observability/debugging
    (e.g. "when did the underlying row last change vs. when did we last
    index it") — it is not a correctness guard.
    """

    project = models.ForeignKey(
        'core.Project',
        on_delete=models.CASCADE,
        related_name='document_index_states',
    )
    source_type = models.CharField(
        max_length=20,
        choices=DocumentSourceType.choices,
    )
    source_id = models.CharField(max_length=64)
    status = models.CharField(
        max_length=10,
        choices=DocumentIndexStatus.choices,
        default=DocumentIndexStatus.PENDING,
    )
    indexed_content_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="sha256 of the extracted text as of the last successful index. Null if never successfully indexed (or currently excluded).",
    )
    indexed_pipeline_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="Fingerprint of the indexing pipeline config as of the last successful index (see rag.indexing.current_pipeline_hash).",
    )
    indexed_source_updated_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Observability only: the source row's updated_at as of the last successful index. Not used for the skip decision.",
    )
    last_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'source_type', 'source_id'],
                name='rag_index_state_unique_source',
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_type}:{self.source_id} (project={self.project_id}, status={self.status})"
