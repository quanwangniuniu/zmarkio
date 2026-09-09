from django.urls import path
# SMP-407
from .views import CalendarEventListView

from .views import (
    CalendarViewSet,
    SubscriptionListCreateView,
    SubscriptionDetailView,
    CalendarShareListCreateView,
    CalendarShareDetailView,
    EventViewSet,
    EventSearchView,
    EventAttendeeListCreateView,
    EventAttendeeDetailView,
    EventRSVPView,
    EventInstancesView,
    EventInstanceModifyView,
    EventInstanceModifyFutureView,
    EventInstanceCancelView,
    DayView,
    WeekView,
    MonthView,
    AgendaView,
    FreeBusyView,
    EventReminderListCreateView,
    PublicBookingLinkAvailabilityView,
    PublicBookingCancelView,
    PublicBookingLookupView,
    PublicBookingCreateView,
    PublicBookingFeedView,
    BookingLinkViewSet,
)


calendar_list = CalendarViewSet.as_view({"get": "list", "post": "create"})
calendar_detail = CalendarViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
)

event_list = EventViewSet.as_view({"get": "list", "post": "create"})
event_detail = EventViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
)

urlpatterns = [
    # Calendar management
    path("calendars/", calendar_list, name="calendar-list"),
    path("calendars/<uuid:pk>/", calendar_detail, name="calendar-detail"),

    # Calendar sharing
    path(
        "calendars/<uuid:calendar_id>/shares/",
        CalendarShareListCreateView.as_view(),
        name="calendar-share-list",
    ),
    path(
        "calendars/<uuid:calendar_id>/shares/<uuid:share_id>/",
        CalendarShareDetailView.as_view(),
        name="calendar-share-detail",
    ),

    # Subscriptions
    path("subscriptions/", SubscriptionListCreateView.as_view(), name="subscription-list"),
    path(
        "subscriptions/<uuid:subscription_id>/",
        SubscriptionDetailView.as_view(),
        name="subscription-detail",
    ),

    # Event management
    path("events/", event_list, name="event-list"),
    path("events/<uuid:pk>/", event_detail, name="event-detail"),

    # Event search
    path("events/search/", EventSearchView.as_view(), name="event-search"),

    # Event attendees
    path(
        "events/<uuid:event_id>/attendees/",
        EventAttendeeListCreateView.as_view(),
        name="event-attendee-list",
    ),
    path(
        "events/<uuid:event_id>/attendees/<uuid:attendee_id>/",
        EventAttendeeDetailView.as_view(),
        name="event-attendee-detail",
    ),

    # RSVP
    path(
        "events/<uuid:event_id>/rsvp/",
        EventRSVPView.as_view(),
        name="event-rsvp",
    ),

    # Recurring event instances
    path(
        "events/<uuid:event_id>/instances/",
        EventInstancesView.as_view(),
        name="event-instances",
    ),
    path(
        "events/<uuid:event_id>/instances/modify/",
        EventInstanceModifyView.as_view(),
        name="event-instance-modify",
    ),
    path(
        "events/<uuid:event_id>/instances/modify-future/",
        EventInstanceModifyFutureView.as_view(),
        name="event-instance-modify-future",
    ),
    path(
        "events/<uuid:event_id>/instances/cancel/",
        EventInstanceCancelView.as_view(),
        name="event-instance-cancel",
    ),

    # Calendar views
    path("views/day/", DayView.as_view(), name="calendar-view-day"),
    path("views/week/", WeekView.as_view(), name="calendar-view-week"),
    path("views/month/", MonthView.as_view(), name="calendar-view-month"),
    path("views/agenda/", AgendaView.as_view(), name="calendar-view-agenda"),

    # Free/busy
    path("freebusy/", FreeBusyView.as_view(), name="calendar-freebusy"),

    # Event reminders
    path(
        "events/<uuid:event_id>/reminders/",
        EventReminderListCreateView.as_view(),
        name="event-reminder-list",
    ),

    # Calendar derived events (read-only, system-generated) SMP-407
    path(
        'derived-events/',
        CalendarEventListView.as_view(),
        name='calendar-derived-events',
    ),

    # Owner-facing booking link management.
    path(
        "booking-links/",
        BookingLinkViewSet.as_view({"get": "list", "post": "create"}),
        name="booking-link-list",
    ),
    path(
        "booking-links/<uuid:pk>/",
        BookingLinkViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "put": "update", "delete": "destroy"}
        ),
        name="booking-link-detail",
    ),

    # Public booking links. Unauthenticated; the org slug in the path
    # is what lets these resolve the tenant schema, since there is no user to
    # resolve it from.
    path(
        "public/book/<slug:org_slug>/<slug:link_slug>/",
        PublicBookingLinkAvailabilityView.as_view(),
        name="public-booking-availability",
    ),
    path(
        "public/book/<slug:org_slug>/<slug:link_slug>/bookings/",
        PublicBookingCreateView.as_view(),
        name="public-booking-create",
    ),
    path(
        "public/book/<slug:org_slug>/<slug:link_slug>/cancel/",
        PublicBookingCancelView.as_view(),
        name="public-booking-cancel",
    ),
    path(
        "public/book/<slug:org_slug>/<slug:link_slug>/lookup/",
        PublicBookingLookupView.as_view(),
        name="public-booking-lookup",
    ),
    # Named *.ics rather than a trailing-slash route: calendar clients key
    # off the extension when deciding to subscribe.
    #
    # Outlook desktop drops query strings on internet calendars, so the
    # token lives in the path. The old ?token= URL stays so already-sent
    # mail still resolves.
    path(
        "public/book/<slug:org_slug>/<slug:link_slug>/calendar.ics",
        PublicBookingFeedView.as_view(),
        name="public-booking-feed-query",
    ),
    path(
        "public/book/<slug:org_slug>/<slug:link_slug>/<str:token>.ics",
        PublicBookingFeedView.as_view(),
        name="public-booking-feed",
    ),
]
