"""TOTP MFA enrollment endpoints for org admins (MED-326)."""

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication import mfa
from authentication.models import MfaDevice
from authentication.serializers import MfaCodeSerializer
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
