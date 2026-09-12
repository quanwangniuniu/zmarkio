"""
URL configuration for backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse
from user_preferences import views as user_pref_views
from spreadsheet import views as spreadsheet_views


def health_check(request):
    return HttpResponse("OK", content_type="text/plain")


urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', health_check, name='health_check'),
    path('api/facebook_integration/', include('facebook_integration.urls')),
    path('api/meta_ads/', include('meta_ads.urls')),
    path('api/access_control/', include('access_control.urls')),
    path('api/audit/', include('audit.urls')),
    path('api/teams/', include('teams.urls')),
    path('api/csm/', include('csm.urls')),
    path('auth/', include('authentication.urls')),
    path('users/', include('user_preferences.urls')),
    path('api/assets/', include('asset.urls')),
    path('api/metric_upload/', include('metric_upload.urls')),
    path('notifications/mock-task-alert/', user_pref_views.mock_task_alert, name='mock-task-alert'),
    path('budgets/', include('budget_approval.urls')),
    path('api/budgets/', include('budget_approval.urls')),  # Add API prefix for frontend compatibility
    path('api/retrospective/', include('retrospective.urls')),
    path('api/behavioral-tracking/', include('behavioral_tracking.urls')),
    path('api/core/', include('core.urls')),
    path('api/alerting/', include('alerting.urls')),
    path('api/report/', include('report.urls')),
    path('api/', include('comments.urls')),
    # Must be before any path("api/", include(...)) so /api/google-*/ is not routed to task.urls.
    path("api/google-docs/", include("google_docs_integration.urls")),
    path("api/google-calendar/", include("google_calendar_integration.urls")),
    path("api/v1/linear/", include("linear_integration.urls")),
    path('api/', include('task.urls')),
    path('api/policy/', include('policy.urls')),
    path('api/dashboard/', include('dashboard.urls')),
    path('api/optimization/', include('optimization.urls')),
    path('api/notion/', include('notion_editor.urls')),
    path('api/tiktok/', include('tiktok.urls')),
    path('api/facebook_meta/', include('facebook_meta.urls')),
    path('api/google_ads/', include('google_ads.urls')),
    path('api/mailchimp/', include('mailchimp.urls')),
    path('api/stripe/', include('stripe_meta.urls')),
    path('api/klaviyo/', include('klaviyo.urls')),
    path('api/spreadsheet/', include('spreadsheet.urls')),
    path(
        'api/projects/<str:project_id>/spreadsheets/<str:spreadsheet_id>/sheets/<int:sheet_id>/',
        spreadsheet_views.ProjectSheetDeleteView.as_view(),
        name='project-sheet-delete'
    ),
    path('api/miro/', include('miro.urls')),
    path('api/', include('client_communication.urls')),
    path('api/', include('automationWorkflow.urls')),
    path('api/chat/', include('chat.urls')),
    path('api/experiment/', include('experiment.urls')),
    path('api/', include('calendars.urls')),
    path('api/ad_copy_variation/', include('ad_copy_variation.urls')),
    path('api/', include('campaign.urls')),
    path('api/slack/', include('slack_integration.urls')),
    path('api/agent/', include('agent.urls')),
    path('api/', include('meetings.urls')),
    path('api/', include('notifications.urls')),
    path("", include("django_prometheus.urls")),
    path("api/v1/zoom/", include("zoom_integration.urls")),
    path('api/', include('experience_group.urls')),
    path('api/', include('customer.urls')),
    path('api/tracking/', include('tracking.urls')),
    path('api/portal/', include('portal.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
