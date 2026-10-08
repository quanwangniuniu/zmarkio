from django.conf import settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.utils import aware_utcnow, datetime_from_epoch


def build_user_refresh_token(user):
    refresh = RefreshToken.for_user(user)
    refresh["auth_token_version"] = getattr(user, "auth_token_version", 0)
    # Store refresh JTI on the refresh token itself. SimpleJWT auto-copies all
    # non-reserved claims to the access token, so refresh_jti will appear in
    # every access token derived from this refresh token — including after a
    # token refresh. This lets the session blacklist check work correctly.
    refresh["refresh_jti"] = str(refresh["jti"])
    return refresh


def set_refresh_cookie(response, refresh):
    """Hand the refresh token to the browser as an HttpOnly cookie (never in the JSON body).

    The cookie expires with the token, not a fresh REFRESH_TOKEN_LIFETIME: the
    refresh view re-sets the same (unrotated) token, which may have hours left.
    """
    if isinstance(refresh, str):
        # Already validated by the caller; only the exp claim is needed here.
        refresh = RefreshToken(refresh, verify=False)
    remaining = datetime_from_epoch(refresh["exp"]) - aware_utcnow()
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        str(refresh),
        max_age=max(int(remaining.total_seconds()), 0),
        path=settings.REFRESH_COOKIE_PATH,
        secure=settings.REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )
    return response


def clear_refresh_cookie(response):
    response.delete_cookie(
        settings.REFRESH_COOKIE_NAME,
        path=settings.REFRESH_COOKIE_PATH,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )
    return response


def read_refresh_token(request):
    """The refresh token from the cookie, or None.

    TRANSITION: also accepts it in the request body from sessions that logged
    in before the cookie existed (they still hold it in localStorage). Remove
    the body fallback one REFRESH_TOKEN_LIFETIME (4 days) after deploying.
    """
    cookie = request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
    if cookie:
        return cookie
    data = getattr(request, "data", None)
    if not hasattr(data, "get"):
        return None
    return data.get("refresh") or data.get("refresh_token") or None
