"""Tests for rag.extraction — source-document extraction correctness,
exclusions, and deterministic ordering (MED-264).
"""
import pytest
from django.utils import timezone

from notion_editor.models import ContentBlock
from retrospective.models import Insight, RetrospectiveStatus

from rag.extraction import extract_meeting, extract_notion_draft, extract_retrospective


@pytest.mark.django_db
class TestExtractMeeting:
    def test_semantic_text_includes_title_objective_summary_and_document_content(
        self, make_meeting, make_meeting_document,
    ):
        meeting = make_meeting(title='Kickoff', objective='Align on scope', summary='Went well')
        make_meeting_document(meeting, content='Detailed notes go here')

        doc = extract_meeting(meeting)

        assert doc is not None
        assert 'Kickoff' in doc.text
        assert 'Align on scope' in doc.text
        assert 'Went well' in doc.text
        assert 'Detailed notes go here' in doc.text
        # Order matters for a stable content_hash: title, objective, summary, then document.
        assert doc.text.index('Kickoff') < doc.text.index('Align on scope')
        assert doc.text.index('Align on scope') < doc.text.index('Went well')
        assert doc.text.index('Went well') < doc.text.index('Detailed notes go here')

    def test_meeting_without_document_does_not_crash(self, make_meeting):
        meeting = make_meeting(title='No doc yet', objective='Objective', summary='')

        doc = extract_meeting(meeting)

        assert doc is not None
        assert 'No doc yet' in doc.text

    def test_meeting_document_content_change_changes_content_hash(self, make_meeting, make_meeting_document):
        meeting = make_meeting(title='T', objective='O', summary='')
        document = make_meeting_document(meeting, content='original content')
        before = extract_meeting(meeting)

        document.content = 'updated content'
        document.save()
        meeting.refresh_from_db()
        after = extract_meeting(meeting)

        assert before.content_hash != after.content_hash

    def test_soft_deleted_meeting_is_excluded(self, make_meeting):
        meeting = make_meeting(is_deleted=True)

        assert extract_meeting(meeting) is None

    def test_archived_meeting_is_still_indexed(self, make_meeting):
        meeting = make_meeting(status='archived', is_archived=True, summary='Final notes')

        doc = extract_meeting(meeting)

        assert doc is not None
        assert 'Final notes' in doc.text


@pytest.mark.django_db
class TestExtractNotionDraft:
    def test_draft_without_project_link_is_excluded(self, make_draft):
        draft = make_draft(
            title='Unassigned draft',
            content_blocks=[{'type': 'text', 'content': {'text': 'some text'}, 'order': 0}],
        )

        assert extract_notion_draft(draft) is None

    def test_draft_with_link_extracts_title_and_ordered_json_blocks(
        self, make_draft, make_draft_link, project,
    ):
        draft = make_draft(
            title='Linked draft',
            content_blocks=[
                {'type': 'text', 'content': {'text': 'second'}, 'order': 1},
                {'type': 'text', 'content': {'text': 'first'}, 'order': 0},
            ],
        )
        make_draft_link(draft, project)

        doc = extract_notion_draft(draft)

        assert doc is not None
        assert doc.project_id == project.id
        assert 'Linked draft' in doc.text
        assert doc.text.index('Linked draft') < doc.text.index('first')
        assert doc.text.index('first') < doc.text.index('second')

    def test_json_html_is_stripped_to_plain_text(self, make_draft, make_draft_link, project):
        draft = make_draft(
            title='HTML draft',
            content_blocks=[
                {
                    'type': 'rich_text',
                    'content': {'html': '<p>Welcome code <strong>WELCOME15</strong></p>'},
                    'order': 0,
                },
            ],
        )
        make_draft_link(draft, project)

        doc = extract_notion_draft(draft)

        assert doc is not None
        assert 'WELCOME15' in doc.text
        assert '<p>' not in doc.text
        assert '<strong>' not in doc.text

    def test_json_is_preferred_over_stale_content_block_rows(
        self, make_draft, make_draft_link, project,
    ):
        draft = make_draft(
            title='Dual storage draft',
            content_blocks=[
                {'type': 'text', 'content': {'text': 'from-json-body'}, 'order': 0},
            ],
        )
        make_draft_link(draft, project)
        ContentBlock.objects.create(
            draft=draft,
            block_type='text',
            content={'text': 'from-table-only'},
            order=0,
        )

        doc = extract_notion_draft(draft)

        assert 'from-json-body' in doc.text
        assert 'from-table-only' not in doc.text

    def test_falls_back_to_content_block_rows_when_json_empty(
        self, make_draft, make_draft_link, project,
    ):
        draft = make_draft(title='Legacy draft', content_blocks=[])
        make_draft_link(draft, project)
        ContentBlock.objects.create(
            draft=draft,
            block_type='text',
            content={'text': 'table-fallback'},
            order=0,
        )

        doc = extract_notion_draft(draft)

        assert doc is not None
        assert 'table-fallback' in doc.text


@pytest.mark.django_db
class TestExtractRetrospective:
    def test_semantic_fields_and_active_insights_are_included(self, make_retrospective, make_insight):
        retro = make_retrospective(
            decision='Launch the campaign',
            confidence_level=4,
            primary_assumption='CTR will hold steady',
        )
        make_insight(retro, title='Budget risk', description='Spend outpaced plan', is_active=True)

        doc = extract_retrospective(retro)

        assert doc is not None
        assert 'Launch the campaign' in doc.text
        assert 'CTR will hold steady' in doc.text
        assert 'Budget risk' in doc.text
        assert 'Spend outpaced plan' in doc.text

    def test_cancelled_retrospective_is_excluded(self, make_retrospective):
        retro = make_retrospective(status=RetrospectiveStatus.CANCELLED)

        assert extract_retrospective(retro) is None

    def test_inactive_insight_is_excluded_from_extracted_text(self, make_retrospective, make_insight):
        retro = make_retrospective()
        make_insight(retro, title='Should not appear', description='Stale insight', is_active=False)

        doc = extract_retrospective(retro)

        assert doc is not None
        assert 'Should not appear' not in doc.text

    def test_active_insights_are_ordered_by_severity_then_created_at_then_pk(self, make_retrospective, make_insight):
        retro = make_retrospective()
        insight_a = make_insight(retro, title='Insight A', description='dA', severity='high')
        insight_b = make_insight(retro, title='Insight B', description='dB', severity='high')
        make_insight(retro, title='Insight C', description='dC', severity='critical')

        # Force A and B to tie on created_at so the extractor's final `pk`
        # tiebreak is actually exercised, not accidentally satisfied by
        # insertion-order timestamps.
        tied_at = timezone.now()
        Insight.objects.filter(pk__in=[insight_a.pk, insight_b.pk]).update(created_at=tied_at)

        expected_titles = list(
            Insight.objects.filter(retrospective=retro, is_active=True)
            .order_by('-severity', '-created_at', 'pk')
            .values_list('title', flat=True)
        )

        doc = extract_retrospective(retro)
        positions = [doc.text.index(title) for title in expected_titles]

        assert positions == sorted(positions)


@pytest.mark.django_db
class TestExtractionOrderingDeterminism:
    def test_draft_extraction_is_deterministic(self, make_draft, make_draft_link, project):
        draft = make_draft(
            title='Stable draft',
            content_blocks=[
                {'type': 'text', 'content': {'text': 'alpha'}, 'order': 0},
                {'type': 'text', 'content': {'text': 'beta'}, 'order': 0},
            ],
        )
        make_draft_link(draft, project)

        first = extract_notion_draft(draft)
        second = extract_notion_draft(draft)

        assert first.text == second.text
        assert first.content_hash == second.content_hash

    def test_retrospective_extraction_is_deterministic(self, make_retrospective, make_insight):
        retro = make_retrospective()
        make_insight(retro, title='One', description='d1', severity='high')
        make_insight(retro, title='Two', description='d2', severity='high')  # ties on severity -> tiebreak

        first = extract_retrospective(retro)
        second = extract_retrospective(retro)

        assert first.text == second.text
        assert first.content_hash == second.content_hash
