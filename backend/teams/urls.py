# campaigns/team_api/urls.py
from django.urls import path
from .views import TeamListCreateView, TeamMembersView, TeamMemberDetailView, TeamDetailView

urlpatterns = [
    path('', TeamListCreateView.as_view(), name='team-list-create'),
    path('<int:team_id>/', TeamDetailView.as_view(), name='team-detail'),
    path('<int:team_id>/members/', TeamMembersView.as_view(), name='team-members'),  # This handles both GET and POST
    path('<int:team_id>/members/<int:user_id>/', TeamMemberDetailView.as_view(), name='team-member-detail'),
]