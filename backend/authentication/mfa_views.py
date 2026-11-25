"""TOTP MFA enrollment endpoints for org admins (MED-326)."""

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication import mfa
from authentication.login_security import LoginSecurityService
from authentication.models import MfaDevice
from authentication.serializers import MfaCodeSerializer, MfaVerifySerializer
from authentication.views import build_login_response_data
from core.admin_permissions import IsOrgAdmin


def _already_enrolled_response():
    return Response(
        {'error': 'MFA is already set up for this account.', 'errorCode': 'MFA_ALREADY_ENROLLED'},
        status=status.HTTP_409_CONFLICT,
    )


class MfaEnrollStartView(APIView):
    """Create (or replace) a pending authenticator secret and return it for the QR code."""

    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def post(self, request):
        user = request.user
        if mfa.get_confirmed_device(user):
            return _already_enrolled_response()

        secret = mfa.generate_secret()
        device, _ = MfaDevice.objects.get_or_create(user=user)
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
    """Verify the first code from the authenticator app, then issue recovery codes once."""

    permission_classes = [IsAuthenticated, IsOrgAdmin]

    def post(self, request):
        serializer = MfaCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user

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

        return Response({'recovery_codes': recovery_codes}, status=status.HTTP_200_OK)


class MfaVerifyView(APIView):
    """Second login step for enrolled org admins: TOTP code or recovery code -> tokens."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = MfaVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = mfa.read_mfa_token(data['mfa_token'])
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
        return Response(
            build_login_response_data(request, user, mfa_verified=True),
            status=status.HTTP_200_OK,
        )
