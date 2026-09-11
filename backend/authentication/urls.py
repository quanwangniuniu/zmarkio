# authentication/urls.py
from django.urls import path
from .views import (
    RegisterView,
    VerifyEmailView,
    LoginView,
    LogoutView,
    SsoRedirectView,
    SsoCallbackView,
    GoogleOAuthStartView,
    GoogleOAuthCallbackView,
    GoogleSetPasswordView,
    OrganizationTokenRefreshView,
    MeView,
    ChangePasswordView,
    MeProjectsView,
    UserTeamsView,
    ForgotPasswordView,
    ResetPasswordView,
    DeleteAccountView,
    SessionListView,
    SessionRevokeView,
    SessionTokenRefreshView,
)

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('verify/', VerifyEmailView.as_view(), name='verify'),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('token/refresh/', SessionTokenRefreshView.as_view(), name='token-refresh'),
    path('organization-token/refresh/', OrganizationTokenRefreshView.as_view(), name='organization-token-refresh'),
    path('me/', MeView.as_view(), name='me'),
    path('change-password/', ChangePasswordView.as_view(), name='change-password'),
    path('me/delete/', DeleteAccountView.as_view(), name='me-delete'),
    path('me/teams/', UserTeamsView.as_view(), name='user-teams'),
    path('me/projects/', MeProjectsView.as_view(), name='me-projects'),
    
    # SSO endpoints (mock implementation for testing)
    path('sso/redirect/', SsoRedirectView.as_view(), name='sso-redirect'),
    path('sso/callback/', SsoCallbackView.as_view(), name='sso-callback'),
    
    # Google OAuth endpoints
    path('google/start/', GoogleOAuthStartView.as_view(), name='google-oauth-start'),
    path('google/callback/', GoogleOAuthCallbackView.as_view(), name='google-oauth-callback'),
    path('google/set-password/', GoogleSetPasswordView.as_view(), name='google-set-password'),

    # Password reset endpoints
    path('forgot-password/', ForgotPasswordView.as_view(), name='forgot-password'),
    path('reset-password/', ResetPasswordView.as_view(), name='reset-password'),

    # Session management endpoints
    path('sessions/', SessionListView.as_view(), name='session-list'),
    path('sessions/<str:jti>/', SessionRevokeView.as_view(), name='session-revoke'),
]
