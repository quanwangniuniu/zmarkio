from rest_framework_simplejwt.tokens import RefreshToken


def build_user_refresh_token(user):
    refresh = RefreshToken.for_user(user)
    refresh["auth_token_version"] = getattr(user, "auth_token_version", 0)
    # Store refresh JTI on the refresh token itself. SimpleJWT auto-copies all
    # non-reserved claims to the access token, so refresh_jti will appear in
    # every access token derived from this refresh token — including after a
    # token refresh. This lets the session blacklist check work correctly.
    refresh["refresh_jti"] = str(refresh["jti"])
    return refresh
