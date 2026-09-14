"""Shared fixtures for rag app tests (MED-264 indexing phase).

No factory_boy in this repo (see conventions in sibling apps) -- these are
thin `Model.objects.create(...)` helpers, matching meetings/notion_editor/
retrospective test patterns exactly so fixture data stays representative of
real rows.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from django.conf import settings
from django.test import override_settings

from core.models import Project
from meetings.models import Meeting, MeetingDocument
from meetings.services import ensure_meeting_type_definition
from notion_editor.models import ContentBlock, Draft, DraftProjectLink
from retrospective.models import Insight, RetrospectiveTask


@pytest.fixture
def project_b(organization, user):
    """A second Project in the same org, for project-isolation tests."""
    return Project.objects.create(
        name='Test Project B',
        organization=organization,
        owner=user,
        objectives=['awareness'],
        kpis={'ctr': {'target': 0.02}},
    )


@pytest.fixture
def make_meeting(project):
    def _make(project=project, **kwargs):
        type_def = ensure_meeting_type_definition(project, 'planning')
        defaults = dict(title='Test Meeting', type_definition=type_def, objective='Some objective')
        defaults.update(kwargs)
        return Meeting.objects.create(project=project, **defaults)
    return _make


@pytest.fixture
def make_meeting_document():
    def _make(meeting, **kwargs):
        defaults = dict(content='')
        defaults.update(kwargs)
        return MeetingDocument.objects.create(meeting=meeting, **defaults)
    return _make


@pytest.fixture
def make_draft(user):
    def _make(user=user, **kwargs):
        defaults = dict(title='Test Draft', status='draft')
        defaults.update(kwargs)
        return Draft.objects.create(user=user, **defaults)
    return _make


@pytest.fixture
def make_draft_link():
    def _make(draft, project, **kwargs):
        return DraftProjectLink.objects.create(draft=draft, project=project, **kwargs)
    return _make


@pytest.fixture
def make_content_block():
    def _make(draft, text='', order=0, **kwargs):
        defaults = dict(block_type='text', content={'text': text}, order=order)
        defaults.update(kwargs)
        return ContentBlock.objects.create(draft=draft, **defaults)
    return _make


@pytest.fixture
def make_retrospective(project, user):
    def _make(project=project, created_by=user, **kwargs):
        defaults = dict(decision='Ship it', primary_assumption='Users want this')
        defaults.update(kwargs)
        return RetrospectiveTask.objects.create(campaign=project, created_by=created_by, **defaults)
    return _make


@pytest.fixture
def make_insight():
    def _make(retrospective, **kwargs):
        defaults = dict(title='An insight', description='Some description')
        defaults.update(kwargs)
        return Insight.objects.create(retrospective=retrospective, **defaults)
    return _make


def _deterministic_vector(text: str, dimensions: int) -> list[float]:
    """A stable, distinct-per-input vector -- not a real embedding, just
    something that lets tests assert order/identity without hitting Gemini.
    """
    seed = sum(ord(c) for c in text) or 1
    return [((seed * (i + 1)) % 997) / 997.0 for i in range(dimensions)]


@pytest.fixture
def mock_embed(monkeypatch):
    """Patches rag.indexing.embed_document_chunks (the name imported into
    that module's namespace) so no test ever calls Gemini. Default
    side_effect returns deterministic, order-preserving vectors sized to
    settings.RAG_EMBEDDING_DIMENSIONS. Tests can override `.side_effect` /
    `.return_value` for failure or mismatched-count scenarios.
    """
    mock = MagicMock(
        side_effect=lambda texts: [_deterministic_vector(t, settings.RAG_EMBEDDING_DIMENSIONS) for t in texts]
    )
    monkeypatch.setattr('rag.indexing.embed_document_chunks', mock)
    return mock


@pytest.fixture
def small_pipeline_settings():
    """Deterministic small chunk_size/overlap matching test_chunking.py's
    own worked example (chunk_size=10, overlap=3 -> step=7), so chunk counts
    in indexing tests are easy to reason about by hand.
    """
    with override_settings(RAG_CHUNK_SIZE=10, RAG_CHUNK_OVERLAP=3):
        yield
