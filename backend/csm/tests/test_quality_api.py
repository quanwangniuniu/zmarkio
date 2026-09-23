"""API tests for the conversation quality inspection endpoints.

Organised by acceptance criterion:
  AC1 scope, AC2 filters, AC3 annotation, AC4 report, AC5 CSV export.
"""

import csv
import datetime as _dt
import io

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from csm.models import (
    Conversation,
    ConversationQualityReview,
    CustomerUser,
    Queue,
    QueueAgent,
)

pytestmark = pytest.mark.django_db

Rating = ConversationQualityReview.Rating


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _rows(response):
    data = response.data
    if isinstance(data, dict) and 'results' in data:
        return data['results']
    return data


def _list_url():
    return reverse('quality-conversation-list')


def _detail_url(pk):
    return reverse('quality-conversation-detail', kwargs={'pk': pk})


def _review_url(pk):
    return reverse('quality-conversation-review', kwargs={'pk': pk})


def _report_url():
    return reverse('csm-quality-report')


def _export_url():
    return reverse('csm-quality-report-export-csv')


def _options_url():
    return reverse('csm-quality-filter-options')


def _supervisor(user, organisation):
    return CustomerUser.objects.create(
        user=user, organisation=organisation, user_type='supervisor', is_active=True,
    )


def _agent(user, queue, organisation):
    return CustomerUser.objects.create(
        user=user, queue=queue, organisation=organisation,
        user_type='agent', is_active=True,
    )


def _conversation(queue, **kwargs):
    kwargs.setdefault('status', 'closed')
    kwargs.setdefault('started_at', timezone.now())
    return Conversation.objects.create(queue=queue, **kwargs)


@pytest.fixture
def supervisor_client(api_client, user, customer_organisation):
    _supervisor(user, customer_organisation)
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def other_org(organization):
    from customer.models import CustomerOrganisation
    return CustomerOrganisation.objects.create(name='Other Org', organization=organization)


@pytest.fixture
def other_queue(project, other_org):
    return Queue.objects.create(
        project=project, organisation=other_org, name='Other Frontline',
        tier='T1', display_order=0, is_active=True,
    )


# ---------------------------------------------------------------------------
# AC1 — scope
# ---------------------------------------------------------------------------

def test_supervisor_sees_only_their_organisation(supervisor_client, csm_queue, other_queue):
    mine = _conversation(csm_queue)
    _conversation(other_queue)

    response = supervisor_client.get(_list_url())

    assert response.status_code == status.HTTP_200_OK
    assert [row['id'] for row in _rows(response)] == [mine.id]


def test_agent_without_supervision_is_denied(api_client, user2, csm_queue, customer_organisation):
    _agent(user2, csm_queue, customer_organisation)
    api_client.force_authenticate(user=user2)

    response = api_client.get(_list_url())

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_anonymous_user_is_denied(api_client):
    assert api_client.get(_list_url()).status_code in (
        status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)


def test_mixed_role_user_sees_only_the_org_they_supervise(
    api_client, user2, csm_queue, other_queue, other_org
):
    """Agent on one queue, supervisor of another org: supervision does not follow agency."""
    _agent(user2, csm_queue, csm_queue.organisation)
    QueueAgent.objects.create(queue=csm_queue, user=user2)
    _supervisor(user2, other_org)
    api_client.force_authenticate(user=user2)

    _conversation(csm_queue)
    supervised = _conversation(other_queue)

    response = api_client.get(_list_url())

    assert [row['id'] for row in _rows(response)] == [supervised.id]


def test_list_includes_closed_and_active_conversations(supervisor_client, csm_queue):
    """Regression guard: the agent inbox defaults to active+pending only."""
    closed = _conversation(csm_queue, status='closed')
    active = _conversation(csm_queue, status='active')
    resolved = _conversation(csm_queue, status='resolved')

    response = supervisor_client.get(_list_url())
    ids = {row['id'] for row in _rows(response)}

    assert ids == {closed.id, active.id, resolved.id}


def test_list_includes_conversations_in_deactivated_queues(
    supervisor_client, project, customer_organisation
):
    """Quality inspection is historical: archiving a queue must not hide its work."""
    archived = Queue.objects.create(
        project=project, organisation=customer_organisation, name='Retired',
        tier='T1', display_order=9, is_active=False,
    )
    conversation = _conversation(archived)

    response = supervisor_client.get(_list_url())

    assert conversation.id in {row['id'] for row in _rows(response)}


def test_detail_of_another_org_conversation_is_not_found(supervisor_client, other_queue):
    """404 rather than 403, so existence does not leak."""
    foreign = _conversation(other_queue)

    assert supervisor_client.get(_detail_url(foreign.id)).status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# AC2 — filters
# ---------------------------------------------------------------------------

def test_filter_by_date_range_includes_both_boundaries(supervisor_client, csm_queue):
    tz = timezone.get_current_timezone()
    today = timezone.localdate()

    def at(day, hour, minute):
        return timezone.make_aware(_dt.datetime.combine(day, _dt.time(hour, minute)), tz)

    first_moment = _conversation(csm_queue, started_at=at(today - _dt.timedelta(days=2), 0, 0))
    last_moment = _conversation(csm_queue, started_at=at(today, 23, 59))
    outside = _conversation(csm_queue, started_at=at(today - _dt.timedelta(days=5), 12, 0))

    response = supervisor_client.get(_list_url(), {
        'date_from': (today - _dt.timedelta(days=2)).isoformat(),
        'date_to': today.isoformat(),
    })
    ids = {row['id'] for row in _rows(response)}

    assert ids == {first_moment.id, last_moment.id}
    assert outside.id not in ids


def test_filter_by_agent_matches_across_their_customer_user_rows(
    supervisor_client, user2, csm_queue, project, customer_organisation
):
    second_queue = Queue.objects.create(
        project=project, organisation=customer_organisation, name='Escalations',
        tier='T2', display_order=1, is_active=True,
    )
    t1 = _agent(user2, csm_queue, customer_organisation)
    t2 = _agent(user2, second_queue, customer_organisation)
    first = _conversation(csm_queue, assigned_to=t1)
    second = _conversation(second_queue, assigned_to=t2)
    _conversation(csm_queue)  # unassigned

    response = supervisor_client.get(_list_url(), {'agent': user2.id})

    assert {row['id'] for row in _rows(response)} == {first.id, second.id}


def test_filter_by_unassigned_sentinel(supervisor_client, user2, csm_queue, customer_organisation):
    agent = _agent(user2, csm_queue, customer_organisation)
    _conversation(csm_queue, assigned_to=agent)
    orphan = _conversation(csm_queue, assigned_to=None)

    response = supervisor_client.get(_list_url(), {'agent': 'unassigned'})

    assert [row['id'] for row in _rows(response)] == [orphan.id]


def test_filter_by_queue(supervisor_client, csm_queue, project, customer_organisation):
    second_queue = Queue.objects.create(
        project=project, organisation=customer_organisation, name='Billing',
        tier='T2', display_order=1, is_active=True,
    )
    mine = _conversation(csm_queue)
    _conversation(second_queue)

    response = supervisor_client.get(_list_url(), {'queue': csm_queue.id})

    assert [row['id'] for row in _rows(response)] == [mine.id]


def test_filter_by_channel(supervisor_client, csm_queue):
    email = _conversation(csm_queue, channel='email')
    _conversation(csm_queue, channel='web')

    response = supervisor_client.get(_list_url(), {'channel': 'email'})

    assert [row['id'] for row in _rows(response)] == [email.id]


def test_filter_by_customer_and_by_search(supervisor_client, csm_queue, customer):
    theirs = _conversation(csm_queue, customer=customer)
    _conversation(csm_queue)

    by_id = supervisor_client.get(_list_url(), {'customer': customer.id})
    by_search = supervisor_client.get(_list_url(), {'customer_search': 'Portal'})

    assert [row['id'] for row in _rows(by_id)] == [theirs.id]
    assert [row['id'] for row in _rows(by_search)] == [theirs.id]


def test_filter_by_tag_is_an_exact_element_match(supervisor_client, csm_queue):
    """'vip' must not match 'vip-escalation' — the JSONField icontains trap."""
    vip = _conversation(csm_queue, tags=['Refund query', 'vip'])
    escalation = _conversation(csm_queue, tags=['Refund query', 'vip-escalation'])

    response = supervisor_client.get(_list_url(), {'tag': 'vip'})
    ids = {row['id'] for row in _rows(response)}

    assert ids == {vip.id}
    assert escalation.id not in ids


def test_filters_combine(supervisor_client, user2, csm_queue, customer_organisation):
    agent = _agent(user2, csm_queue, customer_organisation)
    match = _conversation(csm_queue, assigned_to=agent, channel='email', status='closed')
    _conversation(csm_queue, assigned_to=agent, channel='web', status='closed')
    _conversation(csm_queue, assigned_to=None, channel='email', status='closed')

    response = supervisor_client.get(_list_url(), {
        'agent': user2.id, 'channel': 'email', 'status': 'closed',
    })

    assert [row['id'] for row in _rows(response)] == [match.id]


def test_invalid_date_is_rejected_with_a_field_key(supervisor_client):
    response = supervisor_client.get(_list_url(), {'date_from': 'last-tuesday'})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'date_from' in response.data


def test_filter_options_lists_each_customer_once(supervisor_client, csm_queue, customer):
    """Regression: the dropdown listed a customer once per conversation.

    Conversation.Meta.ordering puts started_at into a SELECT DISTINCT, so the
    DISTINCT deduplicated per conversation unless the ordering is cleared.
    """
    for _ in range(4):
        _conversation(csm_queue, customer=customer)

    response = supervisor_client.get(_options_url())
    rows = response.data['customers']
    ids = [row['id'] for row in rows]

    assert ids == [customer.id]
    assert len(ids) == len(set(ids))
    assert rows[0]['conversation_count'] == 4


def test_filter_options_orders_customers_by_volume(
    supervisor_client, csm_queue, customer, customer_organisation, project
):
    """Busiest customer first, so the supervisor sees where the volume is."""
    from customer.models import Customer

    quiet = Customer.objects.create(
        email='quiet@test.com', full_name='Quiet Customer',
        organisation=customer_organisation, project=project,
    )
    _conversation(csm_queue, customer=quiet)
    for _ in range(3):
        _conversation(csm_queue, customer=customer)

    rows = supervisor_client.get(_options_url()).data['customers']

    assert [(r['name'], r['conversation_count']) for r in rows] == [
        (customer.full_name, 3), ('Quiet Customer', 1),
    ]


def test_filter_options_lists_each_agent_once(
    supervisor_client, user2, csm_queue, project, customer_organisation
):
    """One human with a CustomerUser row per queue is still one dropdown entry."""
    second_queue = Queue.objects.create(
        project=project, organisation=customer_organisation, name='Escalations',
        tier='T2', display_order=1, is_active=True,
    )
    _agent(user2, csm_queue, customer_organisation)
    _agent(user2, second_queue, customer_organisation)

    response = supervisor_client.get(_options_url())
    user_ids = [row['user_id'] for row in response.data['agents']]

    assert user_ids.count(user2.id) == 1


def test_filter_options_lists_scope_without_subject_tags(
    supervisor_client, csm_queue, other_queue
):
    """tags[0] doubles as the subject, so it is excluded from the vocabulary."""
    _conversation(csm_queue, tags=['Refund not received', 'vip'])

    response = supervisor_client.get(_options_url())

    assert response.status_code == status.HTTP_200_OK
    assert response.data['tags'] == ['vip']
    assert [q['id'] for q in response.data['queues']] == [csm_queue.id]
    assert {c['value'] for c in response.data['channels']} == {'web', 'email', 'whatsapp'}


# ---------------------------------------------------------------------------
# AC3 — annotation
# ---------------------------------------------------------------------------

def test_annotate_creates_then_updates(supervisor_client, user, csm_queue):
    conversation = _conversation(csm_queue)

    created = supervisor_client.post(_review_url(conversation.id), {
        'rating': 'poor', 'comment': 'Missed the refund policy.',
    }, format='json')

    assert created.status_code == status.HTTP_201_CREATED
    assert created.data['rating'] == 'poor'
    assert created.data['rating_display'] == 'Poor'
    assert created.data['reviewer'] == user.id
    assert created.data['reviewed_at']
    assert created.data['created'] is True

    updated = supervisor_client.post(_review_url(conversation.id), {
        'rating': 'good', 'comment': 'Recovered well.',
    }, format='json')

    assert updated.status_code == status.HTTP_200_OK
    assert updated.data['created'] is False
    assert conversation.quality_reviews.count() == 1


def test_annotate_accepts_a_blank_comment(supervisor_client, csm_queue):
    conversation = _conversation(csm_queue)

    response = supervisor_client.post(
        _review_url(conversation.id), {'rating': 'good'}, format='json')

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data['comment'] == ''


def test_annotate_rejects_an_unknown_rating(supervisor_client, csm_queue):
    conversation = _conversation(csm_queue)

    response = supervisor_client.post(
        _review_url(conversation.id), {'rating': 'excellent'}, format='json')

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'rating' in response.data


def test_agent_cannot_annotate(api_client, user2, csm_queue, customer_organisation):
    _agent(user2, csm_queue, customer_organisation)
    api_client.force_authenticate(user=user2)
    conversation = _conversation(csm_queue)

    response = api_client.post(
        _review_url(conversation.id), {'rating': 'good'}, format='json')

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_supervisor_cannot_annotate_another_organisation(supervisor_client, other_queue):
    foreign = _conversation(other_queue)

    response = supervisor_client.post(
        _review_url(foreign.id), {'rating': 'good'}, format='json')

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_list_exposes_my_review_and_counts(supervisor_client, csm_queue):
    conversation = _conversation(csm_queue)
    supervisor_client.post(
        _review_url(conversation.id), {'rating': 'good', 'comment': 'Nice.'}, format='json')

    row = next(r for r in _rows(supervisor_client.get(_list_url())) if r['id'] == conversation.id)

    assert row['review_count'] == 1
    assert row['my_review']['rating'] == 'good'
    assert row['my_review']['comment'] == 'Nice.'


def test_detail_returns_transcript_and_all_reviews(supervisor_client, user2, csm_queue, customer_organisation):
    conversation = _conversation(csm_queue)
    supervisor_client.post(_review_url(conversation.id), {'rating': 'good'}, format='json')
    # A second supervisor's review must also be visible.
    _supervisor(user2, customer_organisation)
    ConversationQualityReview.objects.create(
        conversation=conversation, reviewer=user2, rating=Rating.POOR,
        reviewer_name='Other Sup', reviewed_at=timezone.now(),
    )

    response = supervisor_client.get(_detail_url(conversation.id))

    assert response.status_code == status.HTTP_200_OK
    assert len(response.data['reviews']) == 2
    assert response.data['my_review']['rating'] == 'good'
    assert 'messages' in response.data


# ---------------------------------------------------------------------------
# AC4 — report
# ---------------------------------------------------------------------------

def test_report_aggregates_by_rating_agent_and_date(
    supervisor_client, user2, csm_queue, customer_organisation
):
    agent = _agent(user2, csm_queue, customer_organisation)
    for rating in ('good', 'good', 'poor'):
        conversation = _conversation(csm_queue, assigned_to=agent)
        supervisor_client.post(_review_url(conversation.id), {'rating': rating}, format='json')

    response = supervisor_client.get(_report_url())

    assert response.status_code == status.HTTP_200_OK
    ratings = {row['rating']: row['count'] for row in response.data['by_rating']}
    assert ratings == {'good': 2, 'needs_improvement': 0, 'poor': 1}

    agent_row = next(r for r in response.data['by_agent'] if r['agent_user_id'] == user2.id)
    assert (agent_row['total'], agent_row['good'], agent_row['poor']) == (3, 2, 1)

    assert sum(row['total'] for row in response.data['by_date']) == 3
    assert response.data['totals']['reviews'] == 3


def test_report_honours_the_filters(supervisor_client, csm_queue):
    email = _conversation(csm_queue, channel='email')
    web = _conversation(csm_queue, channel='web')
    supervisor_client.post(_review_url(email.id), {'rating': 'good'}, format='json')
    supervisor_client.post(_review_url(web.id), {'rating': 'poor'}, format='json')

    response = supervisor_client.get(_report_url(), {'channel': 'email'})
    ratings = {row['rating']: row['count'] for row in response.data['by_rating']}

    assert ratings['good'] == 1
    assert ratings['poor'] == 0


def test_report_echoes_the_applied_filters(supervisor_client, csm_queue):
    today = timezone.localdate().isoformat()

    response = supervisor_client.get(_report_url(), {
        'date_from': today, 'date_to': today, 'bucket': 'day', 'channel': 'email',
    })
    echo = response.data['filters_echo']

    assert echo['date_from'] == today
    assert echo['bucket'] == 'day'
    assert echo['channel'] == ['email']
    assert echo['date_basis'] == 'review'


def test_report_rejects_an_unknown_bucket(supervisor_client):
    response = supervisor_client.get(_report_url(), {'bucket': 'fortnight'})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert 'bucket' in response.data


def test_agent_cannot_read_the_report(api_client, user2, csm_queue, customer_organisation):
    _agent(user2, csm_queue, customer_organisation)
    api_client.force_authenticate(user=user2)

    assert api_client.get(_report_url()).status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# AC5 — CSV export
# ---------------------------------------------------------------------------

def _csv_rows(response):
    """Drain the streaming response AFTER the view returned, as a client would."""
    body = b''.join(response.streaming_content).decode('utf-8')
    return list(csv.reader(io.StringIO(body)))


def test_export_streams_the_report_after_the_view_returns(
    supervisor_client, user2, csm_queue, customer_organisation
):
    """Guards the tenant search_path trap: rows must be materialised eagerly."""
    agent = _agent(user2, csm_queue, customer_organisation)
    for rating in ('good', 'poor'):
        conversation = _conversation(csm_queue, assigned_to=agent)
        supervisor_client.post(_review_url(conversation.id), {'rating': rating}, format='json')

    response = supervisor_client.get(_export_url())

    assert response.status_code == status.HTTP_200_OK
    assert response['Content-Type'] == 'text/csv; charset=utf-8'
    assert response['Content-Disposition'].startswith('attachment; filename="quality-inspection-')

    rows = _csv_rows(response)
    assert rows[0] == [
        'section', 'key', 'label',
        'total', 'good', 'needs_improvement', 'poor',
        'good_pct', 'needs_improvement_pct', 'poor_pct',
        'conversations', 'coverage_pct']
    assert len(rows) > 1, 'streaming must survive the middleware resetting search_path'

    sections = {row[0] for row in rows[1:]}
    assert {'summary', 'agent', 'day'} <= sections

    summary = next(row for row in rows if row[0] == 'summary')
    assert summary[3] == '2'


def test_export_matches_the_on_screen_report(supervisor_client, csm_queue):
    for rating in ('good', 'good', 'needs_improvement'):
        conversation = _conversation(csm_queue)
        supervisor_client.post(_review_url(conversation.id), {'rating': rating}, format='json')

    report = supervisor_client.get(_report_url()).data
    rows = _csv_rows(supervisor_client.get(_export_url()))

    by_rating = {row['rating']: row['count'] for row in report['by_rating']}
    summary = next(row for row in rows if row[0] == 'summary')

    assert int(summary[4]) == by_rating['good']
    assert int(summary[5]) == by_rating['needs_improvement']
    assert int(summary[6]) == by_rating['poor']
    assert int(summary[3]) == report['totals']['reviews']


def test_export_summary_row_carries_the_four_tiles(supervisor_client, csm_queue):
    """AC5: the file must contain everything the screen shows.

    One summary row holds the rating split, its percentages, the population
    and coverage - the four tiles on the report.
    """
    for _ in range(3):
        _conversation(csm_queue)
    good = _conversation(csm_queue)
    needs = _conversation(csm_queue)
    supervisor_client.post(_review_url(good.id), {'rating': 'good'}, format='json')
    supervisor_client.post(
        _review_url(needs.id), {'rating': 'needs_improvement'}, format='json')

    rows = _csv_rows(supervisor_client.get(_export_url()))
    summary = next(row for row in rows if row[0] == 'summary')

    # total, good, needs_improvement, poor
    assert summary[3:7] == ['2', '1', '1', '0']
    # percentages are fractions, so a spreadsheet can format them as percent
    assert summary[7:10] == ['0.500', '0.500', '0.000']
    # conversations in scope, and the share of them reviewed
    assert summary[10] == '5'
    assert summary[11] == '0.400'


def test_export_breakdown_rows_leave_coverage_blank(supervisor_client, csm_queue):
    """Coverage is a whole-population figure, so it is meaningless per agent."""
    conversation = _conversation(csm_queue)
    supervisor_client.post(_review_url(conversation.id), {'rating': 'good'}, format='json')

    rows = _csv_rows(supervisor_client.get(_export_url()))

    for row in rows[1:]:
        if row[0] != 'summary':
            assert row[10] == ''
            assert row[11] == ''


def test_export_lists_date_rows_newest_first(supervisor_client, csm_queue):
    """The report reads the same way round as the conversation list."""
    import datetime as _dt
    from csm.models import ConversationQualityReview

    today = timezone.localdate()
    for offset in (0, 1, 2):
        conversation = _conversation(csm_queue)
        response = supervisor_client.post(
            _review_url(conversation.id), {'rating': 'good'}, format='json')
        review = ConversationQualityReview.objects.get(id=response.data['id'])
        review.reviewed_at = review.reviewed_at - _dt.timedelta(days=offset)
        review.save(update_fields=['reviewed_at'])

    rows = _csv_rows(supervisor_client.get(_export_url(), {
        'date_from': (today - _dt.timedelta(days=2)).isoformat(),
        'date_to': today.isoformat(),
    }))
    buckets = [row[1] for row in rows[1:] if row[0] == 'day']

    assert buckets == sorted(buckets, reverse=True)
    assert buckets[0] == today.isoformat()


def test_export_names_date_rows_after_the_bucket(supervisor_client, csm_queue):
    conversation = _conversation(csm_queue)
    supervisor_client.post(_review_url(conversation.id), {'rating': 'good'}, format='json')

    rows = _csv_rows(supervisor_client.get(_export_url(), {'bucket': 'month'}))

    assert any(row[0] == 'month' for row in rows[1:])


def test_export_honours_the_filters(supervisor_client, csm_queue):
    email = _conversation(csm_queue, channel='email')
    web = _conversation(csm_queue, channel='web')
    supervisor_client.post(_review_url(email.id), {'rating': 'good'}, format='json')
    supervisor_client.post(_review_url(web.id), {'rating': 'poor'}, format='json')

    rows = _csv_rows(supervisor_client.get(_export_url(), {'channel': 'email'}))
    summary = next(row for row in rows if row[0] == 'summary')

    assert summary[3] == '1'


def test_export_escapes_spreadsheet_formulas(supervisor_client, user2, csm_queue, customer_organisation):
    """An agent name starting with '=' must not execute when the file is opened."""
    user2.first_name, user2.last_name = '=HYPERLINK("http://evil")', ''
    user2.save(update_fields=['first_name', 'last_name'])
    agent = _agent(user2, csm_queue, customer_organisation)
    conversation = _conversation(csm_queue, assigned_to=agent)
    supervisor_client.post(_review_url(conversation.id), {'rating': 'good'}, format='json')

    rows = _csv_rows(supervisor_client.get(_export_url()))
    agent_row = next(row for row in rows if row[0] == 'agent')

    assert agent_row[2].startswith("'="), agent_row


def test_agent_cannot_export(api_client, user2, csm_queue, customer_organisation):
    _agent(user2, csm_queue, customer_organisation)
    api_client.force_authenticate(user=user2)

    assert api_client.get(_export_url()).status_code == status.HTTP_403_FORBIDDEN
