"""Service-level tests for conversation quality inspection.

Covers the annotation upsert, the agent snapshot, and the report aggregation.
API-level coverage lives in test_quality_api.py.
"""

import datetime as _dt

import pytest
from django.utils import timezone

from csm.models import (
    Conversation,
    ConversationMessage,
    ConversationQualityReview,
    CustomerUser,
    Queue,
)
from csm.services.quality import (
    build_quality_report,
    default_bucket,
    filtered_conversations,
    parse_filters,
    resolve_agent,
    upsert_review,
)

pytestmark = pytest.mark.django_db

Rating = ConversationQualityReview.Rating


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _supervisor(user, customer_organisation):
    return CustomerUser.objects.create(
        user=user, organisation=customer_organisation,
        user_type='supervisor', is_active=True,
    )


def _agent(user, queue, organisation):
    return CustomerUser.objects.create(
        user=user, queue=queue, organisation=organisation,
        user_type='agent', is_active=True,
    )


def _conversation(queue, customer=None, **kwargs):
    kwargs.setdefault('status', 'closed')
    kwargs.setdefault('started_at', timezone.now())
    return Conversation.objects.create(queue=queue, customer=customer, **kwargs)


def _filters(**overrides):
    base = {
        'date_from': None, 'date_to': None,
        'agent_user_ids': [], 'include_unassigned': False,
        'queue_ids': [], 'channels': [], 'customer_ids': [],
        'customer_search': '', 'tags': [], 'statuses': [],
        'bucket': None, 'date_basis': None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# upsert semantics
# ---------------------------------------------------------------------------

def test_reviewing_twice_updates_the_same_row(user, user2, csm_queue, customer_organisation):
    supervisor = user
    _supervisor(supervisor, customer_organisation)
    conversation = _conversation(csm_queue)

    review, created = upsert_review(supervisor, conversation, Rating.POOR, 'Missed the policy.')
    assert created is True
    first_reviewed_at = review.reviewed_at

    review2, created2 = upsert_review(supervisor, conversation, Rating.GOOD, 'Recovered well.')
    assert created2 is False
    assert review2.pk == review.pk
    assert conversation.quality_reviews.count() == 1

    review2.refresh_from_db()
    assert review2.rating == Rating.GOOD
    assert review2.comment == 'Recovered well.'
    # The "as of" date must move, or a stale bucket keeps counting the old rating.
    assert review2.reviewed_at > first_reviewed_at


def test_two_supervisors_each_get_their_own_review(user, user2, csm_queue, customer_organisation):
    _supervisor(user, customer_organisation)
    _supervisor(user2, customer_organisation)
    conversation = _conversation(csm_queue)

    upsert_review(user, conversation, Rating.GOOD)
    upsert_review(user2, conversation, Rating.POOR)

    assert conversation.quality_reviews.count() == 2


def test_review_records_reviewer_identity_and_timestamp(user, csm_queue, customer_organisation):
    user.first_name, user.last_name = 'Grace', 'Hopper'
    user.save(update_fields=['first_name', 'last_name'])
    _supervisor(user, customer_organisation)
    conversation = _conversation(csm_queue)

    before = timezone.now()
    review, _ = upsert_review(user, conversation, Rating.NEEDS_IMPROVEMENT, 'Tone.')

    assert review.reviewer_id == user.id
    assert review.reviewer_name == 'Grace Hopper'
    assert before <= review.reviewed_at <= timezone.now()


def test_review_rejects_conversation_without_a_queue(user, customer_organisation):
    from rest_framework.exceptions import PermissionDenied

    _supervisor(user, customer_organisation)
    orphan = Conversation.objects.create(queue=None, status='closed')

    with pytest.raises(PermissionDenied):
        upsert_review(user, orphan, Rating.GOOD)


# ---------------------------------------------------------------------------
# agent snapshot
# ---------------------------------------------------------------------------

def test_agent_snapshot_survives_reassignment(user, user2, csm_queue, customer_organisation):
    """A later reassignment must not rewrite quality history."""
    _supervisor(user, customer_organisation)
    original_agent = _agent(user2, csm_queue, customer_organisation)
    conversation = _conversation(csm_queue, assigned_to=original_agent)

    review, _ = upsert_review(user, conversation, Rating.POOR)
    assert review.agent_user_id == user2.id
    assert review.agent_customer_user_id == original_agent.id

    # Reassign to somebody else entirely.
    from django.contrib.auth import get_user_model
    other = get_user_model().objects.create_user(
        username='other', email='other@test.com', password='x',
        organization=user.organization,
    )
    new_agent = _agent(other, csm_queue, customer_organisation)
    conversation.assigned_to = new_agent
    conversation.save(update_fields=['assigned_to'])

    review.refresh_from_db()
    assert review.agent_user_id == user2.id, 'snapshot must not follow the reassignment'


def test_agent_falls_back_to_last_agent_message_author(user, user2, csm_queue, customer_organisation):
    _supervisor(user, customer_organisation)
    agent = _agent(user2, csm_queue, customer_organisation)
    conversation = _conversation(csm_queue, assigned_to=None)
    ConversationMessage.objects.create(
        conversation=conversation, sender_type='agent',
        sender_agent=agent, content='Hello',
    )

    assert resolve_agent(conversation) == agent
    review, _ = upsert_review(user, conversation, Rating.GOOD)
    assert review.agent_user_id == user2.id


def test_agent_is_null_when_nobody_handled_the_conversation(user, csm_queue, customer_organisation):
    _supervisor(user, customer_organisation)
    conversation = _conversation(csm_queue, assigned_to=None)

    review, _ = upsert_review(user, conversation, Rating.GOOD)
    assert review.agent_user_id is None
    assert review.agent_name == ''


def test_review_snapshots_queue_and_organisation(user, csm_queue, customer_organisation):
    _supervisor(user, customer_organisation)
    conversation = _conversation(csm_queue)

    review, _ = upsert_review(user, conversation, Rating.GOOD)
    assert review.queue_id == csm_queue.id
    assert review.organisation_id == customer_organisation.id


# ---------------------------------------------------------------------------
# report aggregation
# ---------------------------------------------------------------------------

def test_report_zero_fills_every_rating(user, csm_queue, customer_organisation):
    _supervisor(user, customer_organisation)
    upsert_review(user, _conversation(csm_queue), Rating.GOOD)

    report = build_quality_report(user, _filters())
    ratings = {row['rating']: row['count'] for row in report['by_rating']}

    assert ratings == {'good': 1, 'needs_improvement': 0, 'poor': 0}
    assert len(report['by_rating']) == 3


def test_report_pivots_counts_per_agent(user, user2, csm_queue, customer_organisation):
    _supervisor(user, customer_organisation)
    agent = _agent(user2, csm_queue, customer_organisation)

    for rating in (Rating.GOOD, Rating.GOOD, Rating.POOR):
        upsert_review(user, _conversation(csm_queue, assigned_to=agent), rating)

    report = build_quality_report(user, _filters())
    row = next(r for r in report['by_agent'] if r['agent_user_id'] == user2.id)

    assert (row['total'], row['good'], row['poor']) == (3, 2, 1)
    assert row['needs_improvement'] == 0


def test_report_collapses_one_person_holding_two_customer_user_rows(
    user, user2, csm_queue, project, customer_organisation
):
    """CustomerUser is unique per (user, queue), so one human spans queues."""
    _supervisor(user, customer_organisation)
    second_queue = Queue.objects.create(
        project=project, organisation=customer_organisation,
        name='Escalations', tier='T2', display_order=1, is_active=True,
    )
    agent_t1 = _agent(user2, csm_queue, customer_organisation)
    agent_t2 = _agent(user2, second_queue, customer_organisation)

    upsert_review(user, _conversation(csm_queue, assigned_to=agent_t1), Rating.GOOD)
    upsert_review(user, _conversation(second_queue, assigned_to=agent_t2), Rating.POOR)

    report = build_quality_report(user, _filters())
    rows = [r for r in report['by_agent'] if r['agent_user_id'] == user2.id]

    assert len(rows) == 1, 'one human must be one row'
    assert rows[0]['total'] == 2


def test_report_reports_unassigned_agent_explicitly(user, csm_queue, customer_organisation):
    _supervisor(user, customer_organisation)
    upsert_review(user, _conversation(csm_queue, assigned_to=None), Rating.POOR)

    report = build_quality_report(user, _filters())
    row = next(r for r in report['by_agent'] if r['agent_user_id'] is None)

    assert row['agent_name'] == 'Unassigned'
    assert row['total'] == 1


def test_report_counts_do_not_move_after_reassignment(user, user2, csm_queue, customer_organisation):
    _supervisor(user, customer_organisation)
    agent = _agent(user2, csm_queue, customer_organisation)
    conversation = _conversation(csm_queue, assigned_to=agent)
    upsert_review(user, conversation, Rating.POOR)

    before = build_quality_report(user, _filters())['by_agent']

    from django.contrib.auth import get_user_model
    other = get_user_model().objects.create_user(
        username='other2', email='other2@test.com', password='x',
        organization=user.organization,
    )
    conversation.assigned_to = _agent(other, csm_queue, customer_organisation)
    conversation.save(update_fields=['assigned_to'])

    assert build_quality_report(user, _filters())['by_agent'] == before


def test_report_includes_coverage_against_conversations_in_scope(
    user, csm_queue, customer_organisation
):
    _supervisor(user, customer_organisation)
    upsert_review(user, _conversation(csm_queue), Rating.GOOD)
    _conversation(csm_queue)  # in scope, unreviewed
    _conversation(csm_queue)
    _conversation(csm_queue)

    totals = build_quality_report(user, _filters())['totals']

    assert totals['reviews'] == 1
    assert totals['conversations_reviewed'] == 1
    assert totals['conversations_in_scope'] == 4
    assert totals['coverage_pct'] == 25.0


def test_report_buckets_by_day_and_zero_fills_gaps(user, csm_queue, customer_organisation):
    _supervisor(user, customer_organisation)
    today = timezone.localdate()
    review, _ = upsert_review(user, _conversation(csm_queue), Rating.GOOD)
    # Push the review two days back so the range spans an empty day.
    review.reviewed_at = review.reviewed_at - _dt.timedelta(days=2)
    review.save(update_fields=['reviewed_at'])

    report = build_quality_report(user, _filters(
        date_from=today - _dt.timedelta(days=2), date_to=today, bucket='day',
    ))

    assert report['filters_echo']['bucket'] == 'day'
    assert len(report['by_date']) == 3
    # Newest bucket first, matching the conversation list; the review was
    # backdated two days, so it lands on the last row.
    assert [row['total'] for row in report['by_date']] == [0, 0, 1]
    buckets = [row['bucket'] for row in report['by_date']]
    assert buckets == sorted(buckets, reverse=True)


def test_default_bucket_widens_with_the_span():
    day = _dt.date(2026, 3, 1)
    assert default_bucket(day, day + _dt.timedelta(days=10)) == 'day'
    assert default_bucket(day, day + _dt.timedelta(days=90)) == 'week'
    assert default_bucket(day, day + _dt.timedelta(days=400)) == 'month'


def test_report_date_basis_review_vs_conversation(user, csm_queue, customer_organisation):
    """An old conversation reviewed today counts under review basis only."""
    _supervisor(user, customer_organisation)
    old = _conversation(csm_queue, started_at=timezone.now() - _dt.timedelta(days=90))
    upsert_review(user, old, Rating.GOOD)

    today = timezone.localdate()
    window = {'date_from': today, 'date_to': today}

    by_review = build_quality_report(user, _filters(date_basis='review', **window))
    by_conversation = build_quality_report(user, _filters(date_basis='conversation', **window))

    assert by_review['totals']['reviews'] == 1
    assert by_conversation['totals']['reviews'] == 0
