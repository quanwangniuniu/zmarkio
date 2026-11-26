"""TOTP MFA enrollment endpoints for org admins (MED-326)."""

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication import mfa
from authentication.login_security import LoginSecurityService
from authentication.models import MfaDevice
from authentication.serializers import MfaCodeSerializer, MfaVerifySerializer
from authentication.views import build_login_response_data


class CanEnrollMfa(BasePermission):
    """Org admins may enroll with either a normal JWT (already signed in) or the
    mfa_token from /auth/login/ or the Google sign-in cookie (MFA not yet set up)."""

    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            user = request.user
        else:
            user = mfa.read_mfa_token(mfa.mfa_token_from_request(request))
        if user is None or not mfa.requires_mfa(user):
            return False
        request.mfa_user = user
        return True


def _already_enrolled_response():
    return Response(
        {'error': 'MFA is already set up for this account.', 'errorCode': 'MFA_ALREADY_ENROLLED'},
        status=status.HTTP_409_CONFLICT,
    )


class MfaEnrollStartView(APIView):
    """Create (or replace) a pending authenticator secret and return it for the QR code."""

    permission_classes = [CanEnrollMfa]

    def post(self, request):
        user = request.mfa_user
        secret = mfa.generate_secret()
        with transaction.atomic():
            # Lock the row so a concurrent confirm cannot be turned back into pending.
            device, _ = MfaDevice.objects.select_for_update().get_or_create(user=user)
            if device.is_confirmed:
                return _already_enrolled_response()
            device.set_secret(secret)
            device.confirmed_at = None
            device.save()

        return Response(
            {
                'secret': secret,
                'otpauth_uri': mfa.provisioning_uri(user, secret),
            },
            status=status.HTTP_200_OK,
        )


class MfaEnrollConfirmView(APIView):
    """Verify the first code from the authenticator app, then issue recovery codes once.

    Enrolling also completes sign-in: the response includes MFA-verified tokens.
    """

    permission_classes = [CanEnrollMfa]

    def post(self, request):
        serializer = MfaCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.mfa_user

        with transaction.atomic():
            device = MfaDevice.objects.select_for_update().filter(user=user).first()
            if device is None:
                return Response(
                    {'error': 'Start MFA setup first.', 'errorCode': 'MFA_ENROLLMENT_NOT_STARTED'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if device.is_confirmed:
                return _already_enrolled_response()
            if not mfa.verify_totp(device.get_secret(), serializer.validated_data['code']):
                return Response(
                    {'error': 'Invalid verification code.', 'errorCode': 'MFA_INVALID_CODE'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            device.confirmed_at = timezone.now()
            device.save(update_fields=['confirmed_at', 'updated_at'])
            recovery_codes = mfa.generate_recovery_codes(user)

        LoginSecurityService.clear_successful_login(
            LoginSecurityService.build_identifiers(request, user.email)
        )
        response_data = build_login_response_data(request, user, mfa_verified=True)
        response_data['recovery_codes'] = recovery_codes
        response = Response(response_data, status=status.HTTP_200_OK)
        mfa.clear_mfa_cookie(response)
        return response


class MfaVerifyView(APIView):
    """Second login step for enrolled org admins: TOTP code or recovery code -> tokens."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = MfaVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = mfa.read_mfa_token(mfa.mfa_token_from_request(request))
        if user is None:
            return Response(
                {'error': 'Your sign-in has expired. Please log in again.', 'errorCode': 'MFA_TOKEN_INVALID'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Wrong codes count towards the same backoff/lockout as wrong passwords.
        identifiers = LoginSecurityService.build_identifiers(request, user.email)
        blocked = LoginSecurityService.check_request_allowed(identifiers)
        if blocked:
            return Response(blocked.payload, status=blocked.status_code)

        device = mfa.get_confirmed_device(user)
        if device is None:
            return Response(
                {'error': 'Set up MFA first.', 'errorCode': 'MFA_ENROLLMENT_REQUIRED'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if data.get('code'):
            verified = mfa.verify_totp(device.get_secret(), data['code'])
        else:
            verified = mfa.consume_recovery_code(user, data['recovery_code'])

        if not verified:
            blocked = LoginSecurityService.record_failed_login(identifiers)
            if blocked:
                return Response(blocked.payload, status=blocked.status_code)
            return Response(
                {'error': 'Invalid verification code.', 'errorCode': 'MFA_INVALID_CODE'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        LoginSecurityService.clear_successful_login(identifiers)
        response = Response(build_login_response_data(request, user, mfa_verified=True), status=status.HTTP_200_OK)
        mfa.clear_mfa_cookie(response)
        return response
