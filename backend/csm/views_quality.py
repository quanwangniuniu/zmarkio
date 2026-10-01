"""Supervisor-facing conversation quality inspection endpoints.

Kept out of ``csm/views.py`` for two reasons: ``ConversationViewSet`` defaults
its list to active+pending conversations, which is the opposite of what quality
inspection needs, and it is an agent-facing ModelViewSet whose write methods
should not sit behind a supervisor permission.
"""

import csv
import datetime as _dt

from django.db.models import Count, Max, Prefetch, TextChoices
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.admin_permissions import IsCsmSupervisor
from core.tenant_streaming import TenantAwareStreamingResponse
from csm.models import ConversationQualityReview
from csm.serializers import (
    ConversationQualityReviewSerializer,
    ConversationQualityReviewWriteSerializer,
    QualityConversationDetailSerializer,
    QualityConversationSerializer,
)
from csm.services.quality import (
    build_filter_options,
    build_quality_report,
    filtered_conversations,
    parse_filters,
    upsert_review,
)

# One row per entity, each fully described, so the file opens as a clean
# rectangle rather than a sparse one. Sections:
#   summary        — the whole filtered set: rating split, coverage, population
#   agent          — one row per agent
#   day/week/month — one row per bucket, named for the granularity in use
#
# The percent columns hold fractions (0.667, not 66.7). That is how a
# spreadsheet stores a percentage: format the column as a percentage and it
# displays 66.7%, and the values stay usable in arithmetic.
QUALITY_SUMMARY_CSV_HEADER = (
    'Section', 'Key', 'Label',
    'Total', 'Good', 'Needs Improvement', 'Poor',
    'Good %', 'Needs Improvement %', 'Poor %',
    'Conversations', 'Coverage %',
)

# Cells beginning with these execute as formulas when the file is opened in a
# spreadsheet. The report carries free text (agent names, bucket labels), so
# every value is escaped before it is written.
_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


class QualityConversationOrdering(TextChoices):
    """The fields the conversation list may be sorted by (?ordering=, with an
    optional leading '-'). The one place to add a sortable field."""
    STARTED_AT = 'started_at', 'Started'
    ENDED_AT = 'ended_at', 'Ended'


class QualityPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 200


class QualityConversationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    - GET  /quality/conversations/              list within the supervised scope
    - GET  /quality/conversations/{id}/         detail with transcript + reviews
    - POST /quality/conversations/{id}/review/  create or update an annotation
    """
    permission_classes = [IsAuthenticated, IsCsmSupervisor]
    pagination_class = QualityPagination
    http_method_names = ['get', 'post', 'head', 'options']
    # Filtering is done by parse_filters/filtered_conversations, so only the
    # ordering backend applies. ordering_fields must stay explicit: left unset,
    # OrderingFilter accepts any serializer field.
    filter_backends = [OrderingFilter]
    ordering_fields = QualityConversationOrdering.values
    ordering = [f'-{QualityConversationOrdering.STARTED_AT.value}']

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return QualityConversationDetailSerializer
        return QualityConversationSerializer

    def get_queryset(self):
        filters = parse_filters(self.request.query_params)
        qs = filtered_conversations(self.request.user, filters)

        user = self.request.user
        qs = qs.annotate(
            message_count=Count('messages', distinct=True),
            review_count=Count('quality_reviews', distinct=True),
            latest_rating=Max('quality_reviews__rating'),
        ).prefetch_related(
            Prefetch(
                'quality_reviews',
                queryset=ConversationQualityReview.objects.filter(reviewer=user),
                to_attr='my_reviews',
            )
        )
        return qs

    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        conversation = self.get_object()
        serializer = ConversationQualityReviewWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        instance, created = upsert_review(
            user=request.user,
            conversation=conversation,
            rating=serializer.validated_data['rating'],
            comment=serializer.validated_data.get('comment', ''),
        )
        payload = ConversationQualityReviewSerializer(instance).data
        payload['created'] = created
        return Response(
            payload,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class QualityFilterOptionsView(APIView):
    """Values for the six filter controls, scoped to what the user supervises."""
    permission_classes = [IsAuthenticated, IsCsmSupervisor]

    def get(self, request):
        # The tallies respect the filters already applied, so the same query
        # params the list takes are read here too.
        return Response(build_filter_options(request.user, parse_filters(request.query_params)))


class QualityReportView(APIView):
    """Annotation counts by rating, agent and date bucket."""
    permission_classes = [IsAuthenticated, IsCsmSupervisor]

    def get(self, request):
        filters = parse_filters(request.query_params)
        return Response(build_quality_report(request.user, filters))


def _csv_cell(value):
    """Stringify, and neutralise anything a spreadsheet would treat as a formula."""
    if value is None:
        return ''
    text = str(value)
    if text[:1] in _FORMULA_PREFIXES:
        return "'" + text
    return text


def _fraction(part, whole):
    """Share of a whole, as a spreadsheet-friendly 0-1 value."""
    return f'{part / whole:.3f}' if whole else '0.000'


def _rating_split(total, good, needs_improvement, poor):
    return (
        total, good, needs_improvement, poor,
        _fraction(good, total),
        _fraction(needs_improvement, total),
        _fraction(poor, total),
    )


def _summary_rows(report):
    """Flatten the report into CSV rows, carrying everything the screen shows."""
    totals = report['totals']
    by_rating = {entry['rating']: entry['count'] for entry in report['by_rating']}
    granularity = report['filters_echo']['bucket']

    rows = [
        # The four tiles in one row: the rating split plus coverage against the
        # filtered population. conversations and coverage_pct are whole-set
        # figures, so they are blank on the breakdown rows below.
        (
            'summary', 'all', 'All annotations',
            *_rating_split(
                totals['reviews'],
                by_rating.get('good', 0),
                by_rating.get('needs_improvement', 0),
                by_rating.get('poor', 0),
            ),
            totals['conversations_in_scope'],
            _fraction(totals['conversations_reviewed'], totals['conversations_in_scope']),
        ),
    ]

    for entry in report['by_agent']:
        rows.append((
            'agent', entry['agent_user_id'] or 'unassigned', entry['agent_name'],
            *_rating_split(
                entry['total'], entry['good'], entry['needs_improvement'], entry['poor'],
            ),
            '', '',
        ))

    for entry in report['by_date']:
        rows.append((
            granularity, entry['bucket'], entry['bucket'],
            *_rating_split(
                entry['total'], entry['good'], entry['needs_improvement'], entry['poor'],
            ),
            '', '',
        ))

    return rows


class _Echo:
    """A write-only file: csv.writer returns each row as a string instead."""

    def write(self, value):
        return value


class QualityReportCsvView(APIView):
    """CSV of the on-screen report, streamed.

    The report is built inside the stream, after the view has returned, so the
    response is a TenantAwareStreamingResponse: it re-selects the tenant schema
    that TenantSchemaMiddleware resets before a streaming body is iterated.
    UserLocaleMiddleware likewise deactivates the viewer's timezone, so the
    stream re-activates it or the date buckets would fall back to UTC. Filters
    are parsed up front so a bad query string is still a 400.
    """
    permission_classes = [IsAuthenticated, IsCsmSupervisor]

    def get(self, request):
        filters = parse_filters(request.query_params)
        user = request.user
        tz = timezone.get_current_timezone()
        writer = csv.writer(_Echo())

        def rows():
            yield writer.writerow(QUALITY_SUMMARY_CSV_HEADER)
            with timezone.override(tz):
                report = build_quality_report(user, filters)
            for row in _summary_rows(report):
                yield writer.writerow([_csv_cell(cell) for cell in row])

        ts = _dt.datetime.now(_dt.timezone.utc).strftime('%Y%m%d-%H%M%S')
        span = '_'.join(
            filters[key].isoformat() if filters.get(key) else 'all'
            for key in ('date_from', 'date_to')
        )
        filename = f'quality-inspection-{span}-{ts}.csv'

        response = TenantAwareStreamingResponse(rows(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
