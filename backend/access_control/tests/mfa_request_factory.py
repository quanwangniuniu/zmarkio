from django.test import RequestFactory


class MfaVerifiedRequestFactory(RequestFactory):
    """RequestFactory for users who have passed MFA this session (MED-326).

    AuthorizationMiddleware only grants org-admin privileges to requests whose
    token carries mfa_verified. Tests built on this factory are about role
    permissions, so they start from an MFA-verified session.
    """

    def generic(self, *args, **kwargs):
        request = super().generic(*args, **kwargs)
        request.auth = {"mfa_verified": True}
        return request
