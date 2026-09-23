"""Conversation quality inspection: filtering, annotation and reporting.

One filter function feeds the list, the report and the CSV export so the three
can never disagree about which conversations are in scope.
"""

import datetime as _dt

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import Trunc
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import PermissionDenied, ValidationError

from csm.models import (
    Conversation,
    ConversationMessage,
    ConversationQualityReview,
    CustomerUser,
)
from csm.services.scope import supervised_queues_for

User = get_user_model()

BUCKETS = ('day', 'week', 'month')
DATE_BASES = ('review', 'conversation')
UNASSIGNED = 'unassigned'

# Cap on rows scanned when deriving the tag vocabulary. tags is a JSON list, so
# there is no index to read distinct values from.
TAG_VOCABULARY_SCAN_LIMIT = 5000


# ---------------------------------------------------------------------------
# filter parsing
# ---------------------------------------------------------------------------

def _parse_day(raw, field):
    """Parse YYYY-MM-DD, raising a field-keyed DRF error."""
    if not raw:
        return None
    day = parse_date(raw)
    if day is None:
        raise ValidationError({field: 'Expected a date as YYYY-MM-DD.'})
    return day


def _day_bounds(date_from, date_to):
    """Return tz-aware [start, end] covering whole local days, both inclusive.

    Deliberately not ``started_at__date__range``: the ``__date`` lookup wraps
    the column in a timezone-converting function, which cannot use an index and
    can place a boundary conversation on the wrong day.
    """
    tz = timezone.get_current_timezone()
    start = end = None
    if date_from:
        start = timezone.make_aware(_dt.datetime.combine(date_from, _dt.time.min), tz)
    if date_to:
        end = timezone.make_aware(_dt.datetime.combine(date_to, _dt.time.max), tz)
    return start, end


def _int_list(values, field):
    out = []
    for raw in values:
        raw = str(raw).strip()
        if not raw:
            continue
        try:
            out.append(int(raw))
        except ValueError:
            raise ValidationError({field: f'"{raw}" is not a valid id.'})
    return out


def parse_filters(query_params):
    """Normalise query params into a filter dict.

    Repeatable params are read with ``getlist``; the frontend axios instance
    serialises arrays as repeated keys (``?agent=1&agent=2``).
    """
    getlist = getattr(query_params, 'getlist', None)

    def many(key):
        if getlist:
            return query_params.getlist(key)
        value = query_params.get(key)
        return [value] if value else []

    agents_raw = many('agent')
    date_from = _parse_day(query_params.get('date_from'), 'date_from')
    date_to = _parse_day(query_params.get('date_to'), 'date_to')
    if date_from and date_to and date_from > date_to:
        raise ValidationError({'date_to': 'date_to must not precede date_from.'})

    bucket = (query_params.get('bucket') or '').strip().lower() or None
    if bucket and bucket not in BUCKETS:
        raise ValidationError({'bucket': f'Expected one of {", ".join(BUCKETS)}.'})

    date_basis = (query_params.get('date_basis') or '').strip().lower() or None
    if date_basis and date_basis not in DATE_BASES:
        raise ValidationError({'date_basis': f'Expected one of {", ".join(DATE_BASES)}.'})

    return {
        'date_from': date_from,
        'date_to': date_to,
        # 'unassigned' is a sentinel, so agent ids are parsed separately.
        'agent_user_ids': _int_list([a for a in agents_raw if a != UNASSIGNED], 'agent'),
        'include_unassigned': UNASSIGNED in agents_raw,
        'queue_ids': _int_list(many('queue'), 'queue'),
        'channels': [c for c in many('channel') if c],
        'customer_ids': _int_list(many('customer'), 'customer'),
        'customer_search': (query_params.get('customer_search') or '').strip(),
        'tags': [t.strip() for t in many('tag') if t.strip()],
        'statuses': [s for s in many('status') if s],
        'bucket': bucket,
        'date_basis': date_basis,
    }


# ---------------------------------------------------------------------------
# conversation scope
# ---------------------------------------------------------------------------

# Which filter keys each dropdown owns. Used to compute a facet's counts with
# every OTHER filter applied but not its own.
FACET_KEYS = {
    'agent': ('agent_user_ids', 'include_unassigned'),
    'queue': ('queue_ids',),
    'channel': ('channels',),
    'customer': ('customer_ids', 'customer_search'),
    'tag': ('tags',),
    'status': ('statuses',),
}


def without_facet(filters, facet):
    """*filters* with the keys owned by *facet* cleared.

    A facet must not narrow its own options: with Email selected, applying the
    channel filter to the channel counts would show Web as 0 and make a second
    channel look pointless to add. Every other filter still applies, which is
    what makes the number mean "pick this as well and you get N".
    """
    if facet is None:
        return filters
    cleared = dict(filters)
    for key in FACET_KEYS[facet]:
        cleared[key] = [] if isinstance(filters.get(key), list) else (
            False if isinstance(filters.get(key), bool) else ''
        )
    return cleared


def filtered_conversations(user, filters, apply_date=True):
    """Conversations the user supervises, narrowed by *filters*.

    Unlike the agent inbox this applies no default status filter: quality
    inspection exists to review closed conversations as well as active ones.
    """
    supervised_ids = supervised_queues_for(user).values_list('id', flat=True)
    qs = (
        Conversation.objects
        .filter(queue_id__in=supervised_ids)
        .select_related('customer', 'queue', 'queue__organisation', 'assigned_to__user')
    )

    if apply_date:
        start, end = _day_bounds(filters.get('date_from'), filters.get('date_to'))
        if start:
            qs = qs.filter(started_at__gte=start)
        if end:
            qs = qs.filter(started_at__lte=end)

    agent_ids = filters.get('agent_user_ids') or []
    if agent_ids or filters.get('include_unassigned'):
        agent_q = Q()
        if agent_ids:
            # Matches on the auth user, never CustomerUser: one person may hold
            # several CustomerUser rows (unique_together is user+queue), so a
            # CustomerUser match would silently drop their other queues.
            agent_q |= Q(assigned_to__user_id__in=agent_ids)
        if filters.get('include_unassigned'):
            agent_q |= Q(assigned_to__isnull=True)
        qs = qs.filter(agent_q)

    if filters.get('queue_ids'):
        qs = qs.filter(queue_id__in=filters['queue_ids'])
    if filters.get('channels'):
        qs = qs.filter(channel__in=filters['channels'])
    if filters.get('customer_ids'):
        qs = qs.filter(customer_id__in=filters['customer_ids'])
    if filters.get('customer_search'):
        term = filters['customer_search']
        qs = qs.filter(
            Q(customer__full_name__icontains=term) | Q(customer__email__icontains=term)
        )
    if filters.get('statuses'):
        qs = qs.filter(status__in=filters['statuses'])

    if filters.get('tags'):
        # jsonb @> with the value wrapped in a list: an exact element match.
        # Never use icontains here — on a JSONField it degrades to LIKE over the
        # serialised JSON, so 'vip' would also match 'vip-escalation'.
        tag_q = Q()
        for tag in filters['tags']:
            tag_q |= Q(tags__contains=[tag])
        qs = qs.filter(tag_q)

    return qs


# ---------------------------------------------------------------------------
# annotation
# ---------------------------------------------------------------------------

def _display_name(user):
    if not user:
        return ''
    full = ' '.join(p for p in [user.first_name, user.last_name] if p).strip()
    return full or getattr(user, 'username', '') or getattr(user, 'email', '')


def resolve_agent(conversation):
    """Best available agent for *conversation*, as (CustomerUser | None).

    Falls back to the most recent agent message author when the conversation is
    unassigned, so a closed-and-unassigned conversation still credits whoever
    actually handled it.
    """
    if conversation.assigned_to_id:
        return conversation.assigned_to
    return (
        CustomerUser.objects
        .filter(
            sent_messages__conversation_id=conversation.id,
            sent_messages__sender_type='agent',
        )
        .order_by('-sent_messages__created_at')
        .first()
    )


@transaction.atomic
def upsert_review(user, conversation, rating, comment=''):
    """Create or update *user*'s review of *conversation*.

    Returns (review, created). Snapshots agent, queue and organisation so a
    later reassignment cannot rewrite quality history.
    """
    if conversation.queue_id is None:
        raise PermissionDenied(
            'This conversation has no queue, so it cannot be reviewed.'
        )

    agent_customer_user = resolve_agent(conversation)
    agent_user = agent_customer_user.user if agent_customer_user else None

    review, created = ConversationQualityReview.objects.update_or_create(
        conversation=conversation,
        reviewer=user,
        defaults={
            'rating': rating,
            'comment': comment or '',
            'reviewer_name': _display_name(user),
            'reviewed_at': timezone.now(),
            'agent_user': agent_user,
            'agent_customer_user': agent_customer_user,
            'agent_name': _display_name(agent_user),
            'queue_id': conversation.queue_id,
            'organisation_id': conversation.queue.organisation_id,
        },
    )
    return review, created


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def _rating_counts():
    Rating = ConversationQualityReview.Rating
    return {
        'total': Count('id'),
        'good': Count('id', filter=Q(rating=Rating.GOOD)),
        'needs_improvement': Count('id', filter=Q(rating=Rating.NEEDS_IMPROVEMENT)),
        'poor': Count('id', filter=Q(rating=Rating.POOR)),
    }


def default_bucket(date_from, date_to):
    """Pick a granularity from the span so the chart stays readable."""
    if not date_from or not date_to:
        return 'day'
    span = (date_to - date_from).days
    if span <= 31:
        return 'day'
    if span <= 26 * 7:
        return 'week'
    return 'month'


def _zero_filled_dates(rows, start, end, granularity, tz):
    """Insert empty buckets so a gap reads as zero, not as missing data."""
    if start is None or end is None:
        return rows

    by_bucket = {row['bucket']: row for row in rows}
    out = []
    cursor = timezone.localtime(start, tz)
    if granularity == 'day':
        step = _dt.timedelta(days=1)
        cursor = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
    elif granularity == 'week':
        step = _dt.timedelta(weeks=1)
        cursor = (cursor - _dt.timedelta(days=cursor.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
    else:
        step = None
        cursor = cursor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    limit = timezone.localtime(end, tz)
    guard = 0
    while cursor <= limit and guard < 1000:
        guard += 1
        existing = by_bucket.get(cursor)
        if existing:
            out.append(existing)
        else:
            out.append({
                'bucket': cursor, 'total': 0,
                'good': 0, 'needs_improvement': 0, 'poor': 0,
            })
        if step:
            cursor = cursor + step
        else:
            cursor = (cursor.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)

    # Keep any bucket the loop could not reach rather than dropping data.
    for bucket, row in by_bucket.items():
        if all(bucket != existing['bucket'] for existing in out):
            out.append(row)
    return sorted(out, key=lambda row: row['bucket'])


def build_quality_report(user, filters):
    """Aggregate annotation counts by rating, agent and date bucket."""
    Rating = ConversationQualityReview.Rating
    tz = timezone.get_current_timezone()
    basis = filters.get('date_basis') or 'review'
    granularity = filters.get('bucket') or default_bucket(
        filters.get('date_from'), filters.get('date_to'))

    # When the range describes review dates, the conversation set is unbounded
    # in time; when it describes conversation dates, the reviews are not.
    conv_qs = filtered_conversations(user, filters, apply_date=(basis == 'conversation'))

    supervised_ids = supervised_queues_for(user).values_list('id', flat=True)
    base = ConversationQualityReview.objects.filter(
        queue_id__in=supervised_ids,
        conversation_id__in=conv_qs.values('pk'),
    )

    start, end = _day_bounds(filters.get('date_from'), filters.get('date_to'))
    if basis == 'review':
        if start:
            base = base.filter(reviewed_at__gte=start)
        if end:
            base = base.filter(reviewed_at__lte=end)

    counts = _rating_counts()

    rating_rows = {r['rating']: r['count'] for r in base.values('rating').annotate(count=Count('id'))}
    total_reviews = sum(rating_rows.values())
    by_rating = [
        {
            'rating': value,
            'rating_display': label,
            'count': rating_rows.get(value, 0),
            'pct': round(rating_rows.get(value, 0) * 100.0 / total_reviews, 1) if total_reviews else 0.0,
        }
        for value, label in Rating.choices
    ]

    # Group on agent_user_id alone. Including agent_name would split one agent
    # into two rows whenever a snapshot name differs between reviews.
    agent_rows = list(base.values('agent_user_id').annotate(**counts).order_by('-total', 'agent_user_id'))
    names = _agent_names(base, agent_rows)
    by_agent = [
        {
            'agent_user_id': row['agent_user_id'],
            'agent_name': names.get(row['agent_user_id'], 'Unassigned'),
            'total': row['total'],
            'good': row['good'],
            'needs_improvement': row['needs_improvement'],
            'poor': row['poor'],
        }
        for row in agent_rows
    ]

    # Bucket by whatever the range is measuring, so a bar can never fall
    # outside the range the supervisor typed. Under 'conversation' the range
    # selects conversations, so the trend is of when those conversations
    # happened, not of when somebody got round to reviewing them.
    bucket_field = 'reviewed_at' if basis == 'review' else 'conversation__started_at'
    date_rows = list(
        base.annotate(bucket=Trunc(bucket_field, granularity, tzinfo=tz))
            .values('bucket').annotate(**counts).order_by('bucket')
    )
    date_rows = _zero_filled_dates(date_rows, start, end, granularity, tz)
    # Newest bucket first, matching the conversation list. The buckets are
    # computed and zero-filled oldest-first because that is the order the
    # gap-filling walks in; only the output is reversed.
    by_date = [
        {
            'bucket': row['bucket'].date().isoformat() if row['bucket'] else None,
            'total': row['total'],
            'good': row['good'],
            'needs_improvement': row['needs_improvement'],
            'poor': row['poor'],
        }
        for row in reversed(date_rows)
    ]

    conversations_reviewed = base.values('conversation_id').distinct().count()
    conversations_in_scope = conv_qs.count()

    return {
        'filters_echo': _echo(filters, basis, granularity),
        'totals': {
            'reviews': total_reviews,
            'conversations_reviewed': conversations_reviewed,
            'conversations_in_scope': conversations_in_scope,
            'coverage_pct': round(
                conversations_reviewed * 100.0 / conversations_in_scope, 1
            ) if conversations_in_scope else 0.0,
        },
        'by_rating': by_rating,
        'by_agent': by_agent,
        'by_date': by_date,
        'generated_at': timezone.now().isoformat(),
    }


def _agent_names(base, agent_rows):
    """Map agent_user_id -> display name, falling back to the snapshot."""
    ids = [row['agent_user_id'] for row in agent_rows if row['agent_user_id']]
    names = {u.id: _display_name(u) for u in User.objects.filter(id__in=ids)}
    # A deleted user leaves agent_user NULL, but a renamed one still resolves;
    # fall back to the stored snapshot so the row never renders blank.
    for review in base.filter(agent_user_id__in=ids).values('agent_user_id', 'agent_name'):
        if not names.get(review['agent_user_id']) and review['agent_name']:
            names[review['agent_user_id']] = review['agent_name']
    return names


def _echo(filters, basis, granularity):
    return {
        'date_from': filters['date_from'].isoformat() if filters.get('date_from') else None,
        'date_to': filters['date_to'].isoformat() if filters.get('date_to') else None,
        'date_basis': basis,
        'bucket': granularity,
        'agent': filters.get('agent_user_ids') or [],
        'include_unassigned': bool(filters.get('include_unassigned')),
        'queue': filters.get('queue_ids') or [],
        'channel': filters.get('channels') or [],
        'customer': filters.get('customer_ids') or [],
        'customer_search': filters.get('customer_search') or '',
        'tag': filters.get('tags') or [],
        'status': filters.get('statuses') or [],
    }


# ---------------------------------------------------------------------------
# filter options
# ---------------------------------------------------------------------------

def _counts_by(queryset, field):
    """*queryset* grouped by *field* -> count.

    .order_by() clears the model's Meta.ordering so the grouping is only the
    field asked for.
    """
    return {
        row[field]: row['n']
        for row in queryset.order_by().values(field).annotate(n=Count('id'))
    }


def _tag_counts(tag_lists):
    """Tally tags across JSON arrays, skipping tags[0].

    tags[0] doubles as the conversation subject (see ConversationViewSet.claim),
    so counting it would fill the dropdown with one-off subject lines. Done in
    Python because a JSON array has nothing to group by.
    """
    counts = {}
    for tag_list in tag_lists:
        if isinstance(tag_list, list):
            for tag in tag_list[1:]:
                if isinstance(tag, str) and tag.strip():
                    counts[tag.strip()] = counts.get(tag.strip(), 0) + 1
    return counts


def build_filter_options(user, filters=None):
    """Values for the six filter controls, within the supervised scope.

    Each option carries two tallies: how many conversations it accounts for,
    and how many annotations. The Conversations tab lists conversations while
    the Report counts annotations, so a single number would be wrong on one of
    them - most conversations are never reviewed.

    The tallies respect the filters already applied, except the facet's own:
    see ``without_facet``. So with Email selected the agent counts are
    email-only, while the channel counts still show what Web would add.
    """
    filters = filters or parse_filters({})

    queues = list(
        supervised_queues_for(user)
        .select_related('organisation')
        .order_by('organisation__name', 'display_order', 'name')
    )

    def scoped(facet):
        """Conversations and their reviews, with every filter but *facet*'s own."""
        conversations = filtered_conversations(user, without_facet(filters, facet))
        reviews = ConversationQualityReview.objects.filter(
            conversation_id__in=conversations.values('pk'))
        return conversations, reviews

    queue_convs, queue_reviews_qs = scoped('queue')
    queue_counts = _counts_by(queue_convs, 'queue_id')
    queue_reviews = _counts_by(queue_reviews_qs, 'conversation__queue_id')

    channel_convs, channel_reviews_qs = scoped('channel')
    channel_counts = _counts_by(channel_convs, 'channel')
    channel_reviews = _counts_by(channel_reviews_qs, 'conversation__channel')

    status_convs, status_reviews_qs = scoped('status')
    status_counts = _counts_by(status_convs, 'status')
    status_reviews = _counts_by(status_reviews_qs, 'conversation__status')

    agent_convs, agent_reviews_qs = scoped('agent')
    agent_counts = _counts_by(agent_convs, 'assigned_to__user_id')
    agent_reviews = _counts_by(agent_reviews_qs, 'conversation__assigned_to__user_id')
    unassigned_count = agent_counts.pop(None, 0)
    unassigned_reviews = agent_reviews.pop(None, 0)

    customer_convs, customer_reviews_qs = scoped('customer')
    customer_reviews = _counts_by(customer_reviews_qs, 'conversation__customer_id')

    tag_convs, tag_reviews_qs = scoped('tag')
    tag_counts = _tag_counts(
        tag_convs.values_list('tags', flat=True)[:TAG_VOCABULARY_SCAN_LIMIT])
    tag_reviews = _tag_counts(
        tag_reviews_qs.values_list('conversation__tags', flat=True)[:TAG_VOCABULARY_SCAN_LIMIT])

    agents = (
        CustomerUser.objects
        .filter(queue_id__in=[q.id for q in queues], is_active=True, user__isnull=False)
        .select_related('user')
    )
    seen, agent_rows = set(), []
    for customer_user in agents:
        if customer_user.user_id in seen:
            continue
        seen.add(customer_user.user_id)
        agent_rows.append({
            'user_id': customer_user.user_id,
            'name': _display_name(customer_user.user),
            'email': customer_user.user.email,
            'conversation_count': agent_counts.get(customer_user.user_id, 0),
            'review_count': agent_reviews.get(customer_user.user_id, 0),
        })
    agent_rows.sort(key=lambda row: (-row['conversation_count'], (row['name'] or '').lower()))

    # GROUP BY rather than DISTINCT, so each customer appears once and carries
    # its tallies. .order_by() clears Conversation.Meta.ordering: left in place
    # it would put started_at into the grouping and split every customer back
    # into one row per conversation.
    customers = sorted(
        (
            {'id': row['customer_id'], 'name': row['customer__full_name'],
             'email': row['customer__email'],
             'conversation_count': row['conversation_count'],
             'review_count': customer_reviews.get(row['customer_id'], 0)}
            for row in customer_convs.filter(customer__isnull=False)
                                     .order_by()
                                     .values('customer_id', 'customer__full_name',
                                             'customer__email')
                                     .annotate(conversation_count=Count('id'))
        ),
        key=lambda row: (-row['conversation_count'], (row['name'] or '').lower()),
    )

    organisations = []
    seen_orgs = set()
    for queue in queues:
        if queue.organisation_id and queue.organisation_id not in seen_orgs:
            seen_orgs.add(queue.organisation_id)
            organisations.append({'id': queue.organisation_id, 'name': queue.organisation.name})

    return {
        'organisations': organisations,
        'queues': [
            {'id': q.id, 'name': q.name, 'organisation': q.organisation_id,
             'is_active': q.is_active,
             'conversation_count': queue_counts.get(q.id, 0),
             'review_count': queue_reviews.get(q.id, 0)}
            for q in queues
        ],
        'agents': agent_rows,
        'unassigned_count': unassigned_count,
        'unassigned_review_count': unassigned_reviews,
        'channels': [
            {'value': v, 'label': l,
             'conversation_count': channel_counts.get(v, 0),
             'review_count': channel_reviews.get(v, 0)}
            for v, l in Conversation.CHANNEL_CHOICES
        ],
        'statuses': [
            {'value': v, 'label': l,
             'conversation_count': status_counts.get(v, 0),
             'review_count': status_reviews.get(v, 0)}
            for v, l in Conversation.STATUS_CHOICES
        ],
        'tags': [
            {'value': tag, 'conversation_count': count,
             'review_count': tag_reviews.get(tag, 0)}
            for tag, count in sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        ],
        'customers': customers,
    }
