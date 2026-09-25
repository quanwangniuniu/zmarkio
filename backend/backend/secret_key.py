"""
Validation for Django's SECRET_KEY.

SECRET_KEY signs every JWT the platform issues (SIMPLE_JWT['SIGNING_KEY']) and
is shared with variations-studio-api and decision-service, which verify those
tokens. Anyone who knows it can mint a valid login for any user in any
organisation, so a value that has ever been committed to this repository must
never be accepted outside local development (MED-393).
"""
import hashlib
import warnings

from django.core.exceptions import ImproperlyConfigured

GENERATE_HINT = (
    'Generate a new key with: '
    'python3 -c "import secrets; print(secrets.token_urlsafe(50))"'
)

# Why this list exists: older versions of settings.py and env.example shipped
# SECRET_KEY values in plain text, and those values were copied into real .env
# files. Anyone who has read this repository knows them, so an environment still
# using one can have tokens forged against it. Rejecting them at boot (DEBUG
# off) makes such environments visible instead of silently staying exposed.
#
# This is a one-off cleanup for those two values, not a general secret scanner:
# it cannot catch a key committed in the future. The template now leaves
# SECRET_KEY empty so no new shared value is introduced.
#
# Stored as SHA-256 digests so this file does not republish the values.
KNOWN_COMMITTED_KEY_DIGESTS = frozenset({
    # Former inline fallback in backend/settings.py
    '74b6bf35804827ee73c32855d00d606b95952159c578eb9c265406cfb7a91355',
    # Former placeholder in env.example (the template now leaves it empty)
    '9185e3300d24e5439503a795c6709e6aadb8f7806a6795c6dcf473634dd1196a',
})


def validate_secret_key(value: str, *, debug: bool) -> str:
    """
    Return *value* if it is usable as SECRET_KEY, otherwise fail.

    - Missing or empty: always raises, so a misconfigured environment stops at
      boot instead of silently signing tokens with a public value.
    - A known committed value: raises when DEBUG is off; with DEBUG on (local
      Docker development) it only warns, so existing local setups keep working.
    """
    if not value:
        raise ImproperlyConfigured(f'SECRET_KEY is not set. {GENERATE_HINT}')

    digest = hashlib.sha256(value.encode()).hexdigest()
    if digest in KNOWN_COMMITTED_KEY_DIGESTS:
        message = (
            'SECRET_KEY is a value that has been committed to this repository '
            'and must not be used outside local development. '
            f'{GENERATE_HINT}'
        )
        if not debug:
            raise ImproperlyConfigured(message)
        warnings.warn(message, RuntimeWarning, stacklevel=2)

    return value
