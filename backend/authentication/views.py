from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.shortcuts import redirect
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.contrib.auth import get_user_model
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from rest_framework_simplejwt.tokens import RefreshToken
from .serializers import UserProfileSerializer, OrganizationTokenRefreshSerializer
from .login_security import LoginSecurityService
from .password_rotation import get_password_rotation_status
from .services import refresh_organization_access_token
from .throttles import LoginIPThrottle, LoginUsernameThrottle
from core.admin_utils import assign_org_admin
from core.models import Team, Organization, Role
from core.services.oauth_state import OAuthStateExpired, OAuthStateInvalid, create_oauth_state, validate_oauth_state
from core.services.audit_events import safe_emit_audit_event
from access_control.models import UserRole
from stripe_meta.permissions import generate_organization_access_token
from django.conf import settings
from django.db import transaction, connection
from django.db.models import F
from django.contrib.sessions.models import Session
from core.services.tenant import slug_to_schema_name
from google_auth_oauthlib.flow import Flow  # For OAuth start (generating auth URL)
from requests_oauthlib import OAuth2Session  # For OAuth callback (token exchange)
from django.core.mail import send_mail
from django.utils import timezone
from core.services.auth_tokens import build_user_refresh_token
import datetime
import requests
import jwt
import uuid
import secrets
import hashlib
import logging

logger = logging.getLogger(__name__)


User = get_user_model()

# OAuth Clock Tolerance Configuration
OAUTH_CLOCK_TOLERANCE_SECONDS = 10  # 10 seconds tolerance for JWT validation
GOOGLE_AUTH_STATE_FLOW = "authentication-google-oauth-state"
GOOGLE_AUTH_STATE_TTL_SECONDS = 600
GOOGLE_OAUTH_STATE_SALT = GOOGLE_AUTH_STATE_FLOW


def revoke_user_sessions(user):
    user_id = str(user.id)
    for session in Session.objects.all().iterator():
        try:
            if session.get_decoded().get("_auth_user_id") == user_id:
                session.delete()
        except Exception:
            logger.exception("Failed to inspect session %s during password rotation", session.session_key)


def rotate_user_auth_token_version(user):
    User.objects.filter(pk=user.pk).update(auth_token_version=F("auth_token_version") + 1)
    user.refresh_from_db(fields=["auth_token_version"])


def build_google_oauth_state() -> str:
    return create_oauth_state(
        flow=GOOGLE_AUTH_STATE_FLOW,
        payload={"provider": "google"},
        ttl_seconds=GOOGLE_AUTH_STATE_TTL_SECONDS,
    )

@method_decorator(csrf_exempt, name='dispatch')
class RegisterView(APIView):
    permission_classes = []  
    
    def post(self, request):
        data = request.data
        email = data.get("email")
        password = data.get("password")
        username = data.get("username")
        organization_id = data.get("organization_id")

        if not email or not password or not username:
            return Response({"error": "Missing fields"}, status=400)

        # Check if the email is already registered (unique across all users)
        if User.objects.filter(email=email).exists():
            return Response({"error": "Email already registered"}, status=400)
        
        # Validate password using Django's password validators
        # Create a temporary user object for validation context
        temp_user = User(email=email, username=username)
        try:
            validate_password(password, user=temp_user)
        except ValidationError as e:
            return Response({
                "error": "Password validation failed",
                "details": list(e.messages)
            }, status=400)

        # MULTI-ORG: Users must create or join an organization during onboarding
        # No longer auto-create organization during registration
        organization = None
        if organization_id:
            try:
                organization = Organization.objects.get(id=organization_id)
            except Organization.DoesNotExist:
                return Response({"error": "Organization not found"}, status=400)

        print(f"[DEBUG] Creating user with is_verified=True")
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            organization=organization,  # Legacy field - will be None for new users
            current_organization=organization  # New field - will be None for new users
        )
        print(f"[DEBUG] User created with is_verified={user.is_verified}")

        # Set user flags for email/password registration
        user.is_verified = True
        user.password_set = True  # Explicitly mark password as set (for consistency with Google OAuth flow)
        user.save()

        # If joining existing organization via organization_id, set up membership and roles
        if organization:
            # Joining existing organization - create membership with member role
            from core.models import OrganizationMembership
            OrganizationMembership.objects.get_or_create(
                user=user,
                organization=organization,
                defaults={
                    'role': 'member',
                    'is_active': True,
                }
            )

            # Assign default Media Buyer role in tenant schema
            from django.db import connection
            from core.services.tenant import slug_to_schema_name
            schema_name = slug_to_schema_name(organization.slug)

            # Temporarily switch to tenant schema to create Role and UserRole
            with connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO {schema_name}, public')

            try:
                default_role, _ = Role.objects.get_or_create(
                    organization=organization,
                    name="Media Buyer",
                    defaults={"level": 30}
                )
                UserRole.objects.get_or_create(user=user, role=default_role)
            finally:
                # Reset to public schema
                with connection.cursor() as cursor:
                    cursor.execute('SET search_path TO public')

            # Create CustomerOrganisation + admin CustomerUser so CSM features work
            from customer.models import CustomerOrganisation
            from csm.models import CustomerUser
            cust_org, _ = CustomerOrganisation.objects.get_or_create(
                organization=organization,
                defaults={'name': organization.name},
            )
            CustomerUser.objects.get_or_create(
                user=user,
                organisation=cust_org,
                defaults={
                    'user_type': 'admin',
                    'is_active': True,
                    'is_creator': True,
                },
            )

        # Auto-login: generate JWT tokens so the frontend can log in immediately
        refresh = build_user_refresh_token(user)
        profile_data = UserProfileSerializer(user, context={'request': request}).data
        custom_access_token = generate_organization_access_token(user)

        response_data = {
            "message": "User registered successfully. Account is ready to use.",
            "token": str(refresh.access_token),
            "refresh": str(refresh),
            "user": profile_data,
        }

        if custom_access_token:
            response_data["organization_access_token"] = custom_access_token

        return Response(response_data, status=201)
    
class VerifyEmailView(APIView):
    def get(self, request):
        token = request.GET.get("token")
        if not token:
            return Response({"error": "Missing token"}, status=400)

        try:
            user = User.objects.get(verification_token=token)
            if user.is_verified:
                return Response({"message": "Email already verified."})
            user.is_verified = True
            user.verification_token = None
            user.save()
            return Response({"message": "Email successfully verified."})
        except User.DoesNotExist:
            return Response({"error": "Invalid token"}, status=400)

@method_decorator(csrf_exempt, name='dispatch')
class LoginView(APIView):
    login_throttle_classes = [LoginIPThrottle, LoginUsernameThrottle]

    def check_login_throttles(self, request, username):
        identifiers = [
            identifier
            for throttle_class in self.login_throttle_classes
            if (identifier := throttle_class().get_login_identifier(request, username)) is not None
        ]
        return LoginSecurityService.check_request_allowed(identifiers)

    def post(self, request):
        # Parse request data
        if hasattr(request, 'data'):
            data = request.data
        else:
            import json
            data = json.loads(request.body.decode('utf-8'))
        
        email = data.get('email')
        password = data.get('password')
        if not email or not password:
            return Response(
                {'error': 'Email and password required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        login_identifiers = LoginSecurityService.build_identifiers(request, email)
        security_response = self.check_login_throttles(request, email)
        if security_response:
            return Response(security_response.payload, status=security_response.status_code)

        # Check if user exists before authentication to provide specific error
        # message for unregistered emails.
        #
        # Security note: Returning 404 with a specific "email not registered"
        # message is intentional here for UX. In a hardened production setup,
        # you may want to return a generic 401 response instead to avoid user
        # enumeration risks.
        try:
            User.objects.get(email=email)
        except User.DoesNotExist:
            security_response = LoginSecurityService.record_failed_login(login_identifiers)
            if security_response:
                return Response(security_response.payload, status=security_response.status_code)
            return Response(
                {
                    'error': 'This email is not registered. Please sign up first.',
                    'errorCode': 'USER_NOT_FOUND',
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        user = authenticate(request, username=email, password=password)

        if user is None:
            security_response = LoginSecurityService.record_failed_login(login_identifiers)
            if security_response:
                return Response(security_response.payload, status=security_response.status_code)
            return Response(
                {
                    'error': 'Invalid credentials',
                    'errorCode': 'INVALID_PASSWORD',
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Email verification gate is disabled while no email service is wired
        # up in this environment. Restore the check below once SMTP / SES /
        # Mailgun are configured. See 00_Auth issues B-01.
        # if not user.is_verified:
        #     return Response(
        #         {
        #             'error': 'User not verified',
        #             'errorCode': 'EMAIL_NOT_VERIFIED',
        #         },
        #         status=status.HTTP_403_FORBIDDEN,
        #     )
        
        # Check if password is set (for Google OAuth users)
        if not user.password_set:
            return Response(
                {
                    'error': 'Password not set. Please complete password setup.',
                    'errorCode': 'PASSWORD_NOT_SET',
                    'requires_password_setup': True,
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        
        refresh = build_user_refresh_token(user)
        profile_data = UserProfileSerializer(user, context={'request': request}).data
        
        # Generate organization access token if user belongs to an organization
        custom_access_token = generate_organization_access_token(user)
        
        response_data = {
            'message': 'Login successful',
            'token': str(refresh.access_token),
            'refresh': str(refresh),
            'user': profile_data
        }
        
        # Add organization access token if user belongs to an organization
        if custom_access_token:
            response_data['organization_access_token'] = custom_access_token

        LoginSecurityService.clear_successful_login(login_identifiers)
        
        return Response(response_data, status=status.HTTP_200_OK)


class OrganizationTokenRefreshView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            new_token = refresh_organization_access_token(request.user)
        except ValidationError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = OrganizationTokenRefreshSerializer(
            {"organization_access_token": new_token}
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

class SsoRedirectView(APIView):
    """
    Mock SSO Redirect View
    Returns a mock SSO provider redirect URL for testing purposes
    """
    permission_classes = []
    
    def get(self, request):
        """Return mock SSO redirect URL"""
        return Response({
            'redirect_url': 'https://mock-sso-provider.com/auth?state=mockstate'
        }, status=status.HTTP_200_OK)


class SsoCallbackView(APIView):
    """
    Mock SSO Callback View
    Handles SSO callback by creating/updating users based on email domain
    """
    permission_classes = []
    
    def get(self, request):
        """Handle SSO callback with email parameter"""
        try:
            # Get email from query params (default to buyer@agencyX.com)
            email = request.GET.get('email', 'buyer@agencyX.com').strip()
            
            # Validate email format
            if not email or '@' not in email:
                return Response({
                    'error': 'Invalid email format'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Extract domain from email (case insensitive)
            domain = email.split('@')[-1].lower()
            
            # Find organization by domain (case insensitive)
            organization = Organization.objects.filter(email_domain__iexact=domain).first()
            
            if not organization:
                return Response({
                    'error': 'No organization found for this email domain.'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Use transaction to prevent race conditions
            with transaction.atomic():
                # Check if user exists
                user = User.objects.filter(email=email).first()
                
                if user:
                    # Update existing user
                    user.organization = organization
                    user.is_verified = True
                    user.is_active = True
                    user.save()
                else:
                    # Create new user with hash-based username
                    import hashlib
                    username = f"user_{hashlib.md5(email.encode()).hexdigest()[:8]}"

                    user = User.objects.create(
                        email=email,
                        username=username,
                        organization=organization,  # Legacy field
                        current_organization=organization,  # New multi-org field
                        is_verified=True,
                        is_active=True
                    )
                    user.set_unusable_password()
                    user.save()
                
                # Assign role to user — both Role and UserRole must be resolved
                # inside the tenant schema context so the FK in the tenant
                # schema's access_control_userrole resolves to the tenant's
                # core_role table (not the public one).
                _schema = slug_to_schema_name(organization.slug)
                with connection.cursor() as _cur:
                    _cur.execute(f'SET search_path TO {_schema}, public')
                try:
                    default_role, _ = Role.objects.get_or_create(
                        organization=organization,
                        name="Media Buyer",
                        defaults={"level": 30}
                    )
                    UserRole.objects.get_or_create(user=user, role=default_role)
                finally:
                    with connection.cursor() as _cur:
                        _cur.execute('SET search_path TO public')
                
                # Generate JWT tokens
                refresh = build_user_refresh_token(user)
                profile_data = UserProfileSerializer(user).data
                
                return Response({
                    'message': 'SSO authentication successful',
                    'token': str(refresh.access_token),
                    'refresh': str(refresh),
                    'user': profile_data
                }, status=status.HTTP_200_OK)
                
        except Exception as e:
            print(f"[SSO ERROR] {str(e)}")
            import traceback
            traceback.print_exc()
            return Response({
                'error': 'SSO callback failed',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GoogleOAuthStartView(APIView):

    # Redirects user to Google's authorization page

    permission_classes = []
    
    def get(self, request):
        try:
            missing_settings = []
            if not settings.GOOGLE_OAUTH_CLIENT_ID:
                missing_settings.append("GOOGLE_CLIENT_ID")
            if not settings.GOOGLE_OAUTH_CLIENT_SECRET:
                missing_settings.append("GOOGLE_CLIENT_SECRET")
            if not settings.GOOGLE_OAUTH_REDIRECT_URI:
                missing_settings.append("GOOGLE_OAUTH_REDIRECT_URI")
            if missing_settings:
                return Response({
                    'error': 'Google OAuth is not configured',
                    'details': f"Missing settings: {', '.join(missing_settings)}"
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Create flow instance for managing OAuth 2.0 Authorization Grant Flow
            flow = Flow.from_client_config(
                {
                    "web": {
                        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                        "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": [settings.GOOGLE_OAUTH_REDIRECT_URI]
                    }
                },
                scopes=[
                    'openid',
                    'https://www.googleapis.com/auth/userinfo.email',
                    'https://www.googleapis.com/auth/userinfo.profile'
                ]
            )
            
            flow.redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI
            state = build_google_oauth_state()
            
            # Generate authorization URL
            authorization_url, _ = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true',
                prompt='consent',
                state=state,
            )
            
            print(f"[GOOGLE OAUTH] Redirecting to: {authorization_url}")
            
            # Return redirect URL to frontend
            return Response({
                'authorization_url': authorization_url,
                'state': state
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            print(f"[GOOGLE OAUTH ERROR] {str(e)}")
            return Response({
                'error': 'Failed to initiate Google OAuth',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GoogleOAuthCallbackView(APIView):
    """
    Step 2: Handle Google OAuth callback
    Exchanges authorization code for user info and creates/updates user
    """
    permission_classes = []
    
    def get(self, request):
        try:
            # If auth_data is present, this is a frontend redirect that hit backend.
            # Send the user to the frontend callback handler.
            auth_data = request.GET.get('auth_data')
            if auth_data:
                redirect_url = f"{settings.FRONTEND_URL}/google/callback?auth_data={auth_data}"
                return redirect(redirect_url)

            # Get authorization code from query params
            code = request.GET.get('code')
            if not code:
                return Response({
                    'error': 'Missing authorization code'
                }, status=status.HTTP_400_BAD_REQUEST)

            state = request.GET.get('state')
            if not state:
                return Response({
                    'error': 'Missing OAuth state. Please try signing in again.',
                    'errorCode': 'OAUTH_STATE_INVALID',
                }, status=status.HTTP_400_BAD_REQUEST)

            try:
                validate_oauth_state(
                    state,
                    expected_flow=GOOGLE_AUTH_STATE_FLOW,
                    ttl_seconds=GOOGLE_AUTH_STATE_TTL_SECONDS,
                )
            except OAuthStateExpired:
                return Response({
                    'error': 'OAuth state expired. Please try signing in again.',
                    'errorCode': 'OAUTH_STATE_EXPIRED',
                }, status=status.HTTP_400_BAD_REQUEST)
            except OAuthStateInvalid:
                # Deployment grace path: OAuth flows started before strict signed
                # state shipped stored a single state value in the Django session.
                legacy_state = request.session.get('google_oauth_state')
                if legacy_state and legacy_state == state:
                    del request.session['google_oauth_state']
                    request.session.modified = True
                else:
                    return Response({
                        'error': 'Invalid OAuth state. Please try signing in again.',
                        'errorCode': 'OAUTH_STATE_INVALID',
                    }, status=status.HTTP_400_BAD_REQUEST)

            missing_settings = []
            if not settings.GOOGLE_OAUTH_CLIENT_ID:
                missing_settings.append("GOOGLE_CLIENT_ID")
            if not settings.GOOGLE_OAUTH_CLIENT_SECRET:
                missing_settings.append("GOOGLE_CLIENT_SECRET")
            if not settings.GOOGLE_OAUTH_REDIRECT_URI:
                missing_settings.append("GOOGLE_OAUTH_REDIRECT_URI")
            if missing_settings:
                return Response({
                    'error': 'Google OAuth is not configured',
                    'details': f"Missing settings: {', '.join(missing_settings)}"
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
            # Create OAuth2Session with clock tolerance configuration
            # This is the core fix: configure session with leeway for JWT validation
            oauth_session = OAuth2Session(
                client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
                redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
                scope=[
                    'openid',
                    'https://www.googleapis.com/auth/userinfo.email',
                    'https://www.googleapis.com/auth/userinfo.profile'
                ]
            )
            
            # Exchange authorization code for token
            # Retry logic to handle transient clock skew issues
            import time
            max_retries = 3
            retry_delay = 2  # seconds
            
            token = None
            last_error = None
            
            for attempt in range(max_retries):
                try:
                    print(f"[GOOGLE OAUTH] Attempting token exchange (attempt {attempt + 1}/{max_retries})")
                    token = oauth_session.fetch_token(
                        token_url='https://oauth2.googleapis.com/token',
                        code=code,
                        client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
                        include_client_id=True
                    )
                    print(f"[GOOGLE OAUTH] Token exchange successful on attempt {attempt + 1}")
                    break  # Success, exit retry loop
                    
                except Exception as token_error:
                    last_error = token_error
                    error_msg = str(token_error).lower()
                    print(f"[GOOGLE OAUTH ERROR] Token fetch failed (attempt {attempt + 1}/{max_retries}): {str(token_error)}")
                    
                    # Check if it's a clock-related error that we can retry
                    is_clock_error = 'used too early' in error_msg or 'clock' in error_msg
                    is_retryable = is_clock_error and attempt < max_retries - 1
                    
                    if is_retryable:
                        print(f"[GOOGLE OAUTH] Clock skew detected. Waiting {retry_delay}s before retry...")
                        time.sleep(retry_delay)
                        continue  # Retry
                    
                    # If not retryable or max retries reached, handle the error
                    if 'invalid_grant' in error_msg:
                        return Response({
                            'error': 'Authorization code expired or already used',
                            'details': 'The authorization code has expired, been used, or is invalid. This can happen due to clock synchronization issues or if you clicked the login button multiple times.',
                            'solution': 'Please close this page and try signing in with Google again from the beginning.'
                        }, status=status.HTTP_400_BAD_REQUEST)
                    
                    if is_clock_error:
                        return Response({
                            'error': 'Clock synchronization issue',
                            'details': str(token_error),
                            'solution': 'Server time is not synchronized. Please contact your administrator to restart the backend service: docker-compose restart backend'
                        }, status=status.HTTP_400_BAD_REQUEST)
                    
                    # Re-raise other errors
                    raise
            
            if token is None:
                # Should not reach here, but just in case
                raise last_error or Exception("Failed to fetch token after retries")
            
            # Verify and decode ID token with clock skew tolerance
            # Core fix: Use PyJWT with explicit leeway parameter for clock drift tolerance
            try:
                # First, decode without verification to get the key ID
                unverified_header = jwt.get_unverified_header(token['id_token'])
                
                # Get Google's public keys for signature verification
                certs_url = 'https://www.googleapis.com/oauth2/v1/certs'
                certs_response = requests.get(certs_url)
                certs = certs_response.json()
                
                # Get the public key
                key_id = unverified_header.get('kid')
                public_key_pem = certs.get(key_id)
                
                if not public_key_pem:
                    raise ValueError(f"Unable to find public key with kid: {key_id}")
                
                # Load the X.509 certificate and extract the public key
                # Google's certs endpoint returns X.509 certificates, not raw public keys
                from cryptography.x509 import load_pem_x509_certificate
                from cryptography.hazmat.backends import default_backend
                
                # Load the certificate and extract the public key from it
                cert = load_pem_x509_certificate(
                    public_key_pem.encode('utf-8'),
                    default_backend()
                )
                public_key = cert.public_key()
                
                # Decode and verify JWT with leeway (clock tolerance)
                # leeway: Allows tokens with timestamps slightly in the future or past
                id_info = jwt.decode(
                    token['id_token'],
                    key=public_key,
                    algorithms=['RS256'],
                    audience=settings.GOOGLE_OAUTH_CLIENT_ID,
                    leeway=OAUTH_CLOCK_TOLERANCE_SECONDS,  # Core fix: 10 seconds clock tolerance
                    options={
                        'verify_signature': True,
                        'verify_aud': True,
                        'verify_iat': True,  # Verify issued-at time (with leeway)
                        'verify_exp': True,  # Verify expiration time (with leeway)
                    }
                )
                
                print(f"[GOOGLE OAUTH] Token verified successfully with {OAUTH_CLOCK_TOLERANCE_SECONDS}s clock tolerance")
                
            except jwt.ExpiredSignatureError as exp_error:
                print(f"[GOOGLE OAUTH ERROR] Token expired: {str(exp_error)}")
                return Response({
                    'error': 'Google token has expired. Please try logging in again.',
                    'details': str(exp_error)
                }, status=status.HTTP_400_BAD_REQUEST)
            except jwt.InvalidTokenError as jwt_error:
                print(f"[GOOGLE OAUTH ERROR] Invalid token: {str(jwt_error)}")
                return Response({
                    'error': 'Invalid Google token. Please try logging in again.',
                    'details': str(jwt_error)
                }, status=status.HTTP_400_BAD_REQUEST)
            except Exception as verify_error:
                print(f"[GOOGLE OAUTH ERROR] Token verification failed: {str(verify_error)}")
                error_msg = str(verify_error).lower()
                if 'clock' in error_msg or 'time' in error_msg or 'used too early' in error_msg:
                    return Response({
                        'error': 'Clock synchronization issue detected',
                        'details': str(verify_error),
                        'solution': f'Token validation failed due to time difference. Backend configured with {OAUTH_CLOCK_TOLERANCE_SECONDS}s tolerance.'
                    }, status=status.HTTP_400_BAD_REQUEST)
                raise
            
            # Extract user information
            google_id = id_info.get('sub')
            email = id_info.get('email')
            email_verified = id_info.get('email_verified', False)
            name = id_info.get('name', '')
            
            print(f"[GOOGLE OAUTH] User info - email: {email}, verified: {email_verified}, google_id: {google_id}")
            
            # Security check: Only allow verified emails
            if not email_verified:
                return Response({
                    'error': 'Email not verified by Google. Please use a verified Google account.'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Use transaction to prevent race conditions
            with transaction.atomic():
                # Check if user exists by email
                user = User.objects.select_for_update().filter(email=email).first()
                
                if user:
                    # Existing user - link Google account if not already linked
                    print(f"[GOOGLE OAUTH] Existing user found: {email}")
                    
                    # Check if Google ID conflicts
                    if user.google_id and user.google_id != google_id:
                        return Response({
                            'error': 'This email is already linked to a different Google account.'
                        }, status=status.HTTP_400_BAD_REQUEST)
                    
                    # Update Google ID if not set
                    if not user.google_id:
                        user.google_id = google_id
                        user.google_registered = True
                        # Ensure password_set reflects actual password state
                        # If user has a usable password, ensure password_set is True
                        if user.has_usable_password():
                            user.password_set = True
                        user.save()
                        print(f"[GOOGLE OAUTH] Linked Google account to existing user")
                    
                    # Check if password is set
                    if not user.password_set:
                        # Generate temporary token for password setup
                        temp_token = secrets.token_urlsafe(32)
                        user.verification_token = temp_token
                        user.save()
                        
                        print(f"[GOOGLE OAUTH] Password not set, redirecting to setup")
                        
                        # HTTP redirect to frontend set-password page
                        redirect_url = f"{settings.FRONTEND_URL}/set-password?token={temp_token}"
                        return redirect(redirect_url)
                    
                    # User exists and has password - log them in
                    refresh = build_user_refresh_token(user)
                    profile_data = UserProfileSerializer(user).data
                    custom_access_token = generate_organization_access_token(user)
                    
                    print(f"[GOOGLE OAUTH] Login successful for existing user")
                    
                    # Generate a temporary token for secure auth data transfer
                    import json
                    import base64
                    auth_data = {
                        'token': str(refresh.access_token),
                        'refresh': str(refresh),
                        'user': profile_data,
                        'organization_access_token': custom_access_token
                    }
                    
                    # Encode auth data as base64 for URL transmission
                    auth_data_json = json.dumps(auth_data)
                    auth_data_encoded = base64.urlsafe_b64encode(auth_data_json.encode()).decode()
                    
                    # HTTP redirect to frontend auth callback handler with encoded auth data
                    redirect_url = f"{settings.FRONTEND_URL}/auth/google/callback?auth_data={auth_data_encoded}"
                    return redirect(redirect_url)
                
                else:
                    # New user - create account
                    print(f"[GOOGLE OAUTH] Creating new user: {email}")
                    
                    # Generate username from email or name
                    base_username = email.split('@')[0] if email else name.replace(' ', '_').lower()
                    username = base_username
                    
                    # Ensure username is unique
                    counter = 1
                    while User.objects.filter(username=username).exists():
                        username = f"{base_username}_{counter}"
                        counter += 1
                    
                    # Create new user (without password)
                    user = User.objects.create(
                        email=email,
                        username=username,
                        google_id=google_id,
                        google_registered=True,
                        password_set=False,  # Must set password before next login
                        is_verified=True,  # Google verified the email
                        is_active=True
                    )

                    # Set unusable password (will be set during password setup)
                    user.set_unusable_password()

                    # Generate temporary token for password setup
                    temp_token = secrets.token_urlsafe(32)
                    user.verification_token = temp_token

                    # Auto-create Organization + CSM records
                    # MULTI-ORG: No longer auto-create organization for Google OAuth users
                    # Users must create or join an organization during onboarding
                    user.organization = None  # Legacy field
                    user.current_organization = None  # New multi-org field
                    user.save()

                    print(f"[GOOGLE OAUTH] New user created: {email}, username: {username} (no organization)")

                    # HTTP redirect to frontend set-password page
                    redirect_url = f"{settings.FRONTEND_URL}/set-password?token={temp_token}"
                    return redirect(redirect_url)
        
        except Exception as e:
            print(f"[GOOGLE OAUTH ERROR] {str(e)}")
            import traceback
            traceback.print_exc()
            return Response({
                'error': 'Google OAuth callback failed',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GoogleSetPasswordView(APIView):
    """
    Step 3: Set password for Google OAuth users
    Required before users can access the system
    """
    permission_classes = []
    
    def post(self, request):
        try:
            token = request.data.get('token')
            password = request.data.get('password')
            
            if not token or not password:
                return Response({
                    'error': 'Token and password are required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Find user by verification token
            try:
                user = User.objects.get(verification_token=token)
            except User.DoesNotExist:
                return Response({
                    'error': 'Invalid or expired token'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Check if this is a Google registered user
            if not user.google_registered:
                return Response({
                    'error': 'This endpoint is only for Google OAuth users'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Validate password using Django's password validators
            try:
                validate_password(password, user=user)
            except ValidationError as e:
                return Response({
                    'error': 'Password validation failed',
                    'details': list(e.messages)
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Set password
            user.set_password(password)
            user.password_set = True
            user.verification_token = None  # Clear the token
            user.save(update_fields=['password', 'password_set', 'password_last_changed_at', 'verification_token'])
            
            print(f"[GOOGLE OAUTH] Password set successfully for user: {user.email}")
            
            # Generate auth tokens
            refresh = build_user_refresh_token(user)
            profile_data = UserProfileSerializer(user).data
            custom_access_token = generate_organization_access_token(user)
            
            response_data = {
                'message': 'Password set successfully. You can now log in.',
                'token': str(refresh.access_token),
                'refresh': str(refresh),
                'user': profile_data
            }
            
            if custom_access_token:
                response_data['organization_access_token'] = custom_access_token
            
            return Response(response_data, status=status.HTTP_200_OK)
        
        except Exception as e:
            print(f"[GOOGLE OAUTH ERROR] {str(e)}")
            return Response({
                'error': 'Failed to set password',
                'details': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MeView(APIView):
    """Get/Update current logged-in user's data"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    
    def get(self, request):
        profile_data = UserProfileSerializer(request.user, context={'request': request}).data
        return Response(profile_data, status=status.HTTP_200_OK)
    
    def patch(self, request):
        """Update user profile (username, first_name, last_name, avatar)"""
        from authentication.serializers import ProfileUpdateSerializer
        
        serializer = ProfileUpdateSerializer(
            request.user, 
            data=request.data, 
            partial=True,
            context={'request': request}
        )
        
        if serializer.is_valid():
            serializer.save()
            # Return the updated profile data with request context for avatar URL
            profile_data = UserProfileSerializer(request.user, context={'request': request}).data
            return Response(profile_data, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        current_password = request.data.get("current_password")
        new_password = request.data.get("new_password")

        if not current_password or not new_password:
            return Response(
                {"error": "Current password and new password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not request.user.check_password(current_password):
            return Response(
                {"error": "Current password is incorrect.", "errorCode": "INVALID_CURRENT_PASSWORD"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_password(new_password, user=request.user)
        except ValidationError as e:
            return Response(
                {"error": "Password validation failed", "details": list(e.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            request.user.set_password(new_password)
            request.user.password_set = True
            request.user.save(update_fields=["password", "password_set", "password_last_changed_at"])
            rotate_user_auth_token_version(request.user)
            revoke_user_sessions(request.user)

            safe_emit_audit_event(
                event_type="authentication.password_rotation.password_changed",
                actor=request.user,
                organization=getattr(request.user, "current_organization", None),
                project=getattr(request.user, "active_project", None),
                target_type="user",
                target_id=request.user.id,
                after={
                    "password_last_changed_at": request.user.password_last_changed_at,
                    "auth_token_version": request.user.auth_token_version,
                    "sessions_revoked": True,
                },
                context={"source": "change_password", "reauthentication_required": True},
                request=request,
            )
        profile_data = UserProfileSerializer(request.user, context={"request": request}).data

        return Response(
            {
                "message": "Password changed successfully.",
                "user": profile_data,
                "password_rotation": get_password_rotation_status(request.user).as_dict(),
                "reauthentication_required": True,
            },
            status=status.HTTP_200_OK,
        )


class MeProjectsView(APIView):
    """GET /api/auth/me/projects/ — list user's project memberships with roles."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from core.models import Project, ProjectMember
        from core.admin_utils import get_org_admin_org_ids

        memberships = (
            ProjectMember.objects
            .filter(user=request.user, is_active=True)
            .select_related('project')
            .order_by('project__name')
        )
        data = [
            {
                'project_id': m.project.id,
                'project_name': m.project.name,
                'role': m.role,
            }
            for m in memberships
        ]
        seen_ids = {m.project.id for m in memberships}

        org_ids = get_org_admin_org_ids(request.user)
        if org_ids:
            for p in Project.objects.filter(
                organization_id__in=org_ids,
            ).exclude(id__in=seen_ids).order_by('name'):
                data.append({
                    'project_id': p.id,
                    'project_name': p.name,
                    'role': 'org_admin',
                })

        return Response(data, status=status.HTTP_200_OK)


class UserTeamsView(APIView):
    """Get current user's team memberships"""
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        from core.models import TeamMember
        
        user_teams = TeamMember.objects.filter(
            user=request.user,
            is_deleted=False
        ).select_related('team')
        
        team_ids = [membership.team.id for membership in user_teams]
        
        return Response({
            'user_id': request.user.id,
            'team_ids': team_ids,
            'team_count': len(team_ids)
        }, status=status.HTTP_200_OK)

@method_decorator(csrf_exempt, name='dispatch')
class ForgotPasswordView(APIView):
    permission_classes = []
    def post(self, request):
        email = request.data.get('email')
        if not email:
            return Response({"error": "Email is required"}, status=status.HTTP_400_BAD_REQUEST)
        generic_response = Response({"message": "If this email exists, a reset link has been sent."}, status=status.HTTP_200_OK)
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return generic_response
        # Generate a random token; store only its hash to protect against DB reads
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        user.password_reset_token = token_hash
        user.password_reset_token_expires_at = timezone.now() + datetime.timedelta(hours=1)
        user.save()

        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
        reset_link = f"{frontend_url}/reset-password?token={token}"
        try:
            send_mail(
                subject='Reset Password',
                message=f"Click the link below to reset your password:\n\n{reset_link}\n\nThis link expires in 1 hour.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
        except Exception:
            logger.exception("Failed to send password reset email to %s", email)
        return generic_response
    
@method_decorator(csrf_exempt, name='dispatch')
class ResetPasswordView(APIView):
    permission_classes = []
    def post(self, request):
        token = request.data.get('token')
        new_password = request.data.get('new_password')
        if not token or not new_password:
            return Response({"error":"Token and new password are required"}, status=status.HTTP_400_BAD_REQUEST)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        try:
            user = User.objects.get(password_reset_token=token_hash)
        except User.DoesNotExist:
            return Response({"error":"Invalid or expired token"}, status=status.HTTP_400_BAD_REQUEST)
        
        #validate token expiration
        if user.password_reset_token_expires_at is None:
            return Response({"error":"Invalid or expired token"}, status=status.HTTP_400_BAD_REQUEST)
        if timezone.now() > user.password_reset_token_expires_at: 
            return Response({"error":"Token has expired"}, status=status.HTTP_400_BAD_REQUEST)

        #validate new password
        try:
            validate_password(new_password, user=user)
        except ValidationError as e:
            return Response({"error":"Password validation failed", "details": list(e.messages)}, status=status.HTTP_400_BAD_REQUEST)
        
        #set new pwd
        user.set_password(new_password)
        user.password_reset_token = None
        user.password_reset_token_expires_at = None
        user.password_set = True
        user.save(update_fields=['password', 'password_set', 'password_last_changed_at', 'password_reset_token', 'password_reset_token_expires_at'])
        
        return Response({"message":"Password reset successfully"}, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """POST /auth/logout/ — best-effort token blacklist plus websocket session close."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh_token')
        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()
            except Exception:
                logger.exception("Failed to blacklist refresh token during logout for user %s", request.user.id)

        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'chat_user_{request.user.id}',
                {
                    'type': 'user_session_revoked',
                    'reason': 'logout',
                },
            )
        except Exception:
            logger.exception("Failed to emit logout websocket revoke for user %s", request.user.id)

        return Response({'message': 'Logged out successfully.'}, status=status.HTTP_200_OK)


class DeleteAccountView(APIView):
    """
    DELETE /auth/me/delete/
    Permanently removes all personal data for the authenticated user.
    Projects and tasks created by the user are kept; owner/current_approver set to null.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        user = request.user
        audit_context = {
            "user_id": user.id,
            "email": user.email,
            "username": user.username,
            "current_organization_id": user.current_organization_id,
            "active_project_id": user.active_project_id,
        }

        # Require the user to confirm deletion by typing the exact phrase below.
        confirm = request.data.get('confirm', '')
        if confirm != 'DELETE MY ACCOUNT':
            return Response(
                {'error': 'Please type "DELETE MY ACCOUNT" to confirm.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            from core.models import TeamMember, ProjectMember, Project
            from access_control.models import UserRole, ModuleApprover
            from task.models import Task

            # A deleted project can leave a stale cross-schema reference on the user.
            try:
                active_project = user.active_project
            except Project.DoesNotExist:
                active_project = None

            safe_emit_audit_event(
                event_type="authentication.account_deleted",
                actor=user,
                organization=getattr(user, "current_organization", None),
                project=active_project,
                target_type="user",
                target_id=user.id,
                before=audit_context,
                after={"is_active": False, "is_deleted": True},
                context={"confirm": "DELETE MY ACCOUNT"},
                request=request,
            )

            # 1. Remove user from all teams
            TeamMember.objects.filter(user=user).delete()

            # 2. Remove all role assignments
            UserRole.objects.filter(user=user).delete()

            # 3. Remove all project memberships
            ProjectMember.objects.filter(user=user).delete()

            # 4. Remove module approver assignments
            ModuleApprover.objects.filter(user=user).delete()

            # 5. Detach user from owned projects (keep the projects intact)
            Project.objects.filter(owner=user).update(owner=None)

            # 6. Detach user from tasks they own or are approving (keep the tasks intact)
            Task.objects.filter(owner=user).update(owner=None)
            Task.objects.filter(current_approver=user).update(current_approver=None)

            # 6. Delete avatar file if it exists
            if user.avatar:
                try:
                    user.avatar.delete(save=False)
                except Exception:
                    pass

            # 7. Blacklist the refresh token supplied in the request (best-effort)
            refresh_token = request.data.get('refresh_token')
            if refresh_token:
                try:
                    token = RefreshToken(refresh_token)
                    token.blacklist()
                except Exception:
                    pass

            # 8. Anonymise and soft-delete the user record
            #    (keeps FK integrity for audit logs / chat messages etc.)
            anon_id = user.id
            user.email = f'deleted_{anon_id}@removed.invalid'
            user.username = f'deleted_{anon_id}'
            user.first_name = ''
            user.last_name = ''
            user.google_id = None
            user.verification_token = None
            user.password_reset_token = None
            user.password_reset_token_expires_at = None
            user.is_active = False
            user.is_deleted = True
            user.set_unusable_password()
            user.save()

        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'chat_user_{user.id}',
                {
                    'type': 'user_session_revoked',
                    'reason': 'account_deleted',
                },
            )
        except Exception:
            logger.exception("Failed to emit account-delete websocket revoke for user %s", user.id)

        return Response({'message': 'Account deleted successfully.'}, status=status.HTTP_200_OK)
