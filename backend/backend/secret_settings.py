"""
Validation for secret settings read from the environment.

Each secret validated here signs or protects credentials the platform issues:
SECRET_KEY signs every JWT (SIMPLE_JWT['SIGNING_KEY']) and is shared with
variations-studio-api and decision-service, which verify those tokens. Anyone
who knows such a secret can forge credentials, so a value that has ever been
committed to this repository must never be accepted outside local development
(MED-393).
"""
import hashlib
import warnings

from django.core.exceptions import ImproperlyConfigured

TOKEN_GENERATE_HINT = (
    'Generate a new key with: '
    'python3 -c "import secrets; print(secrets.token_urlsafe(50))"'
)

# Why these lists exist: older versions of settings.py and env.example shipped
# secret values in plain text, and those values were copied into real .env
# files. Anyone who has read this repository knows them, so an environment still
# using one can have tokens forged against it. Rejecting them at boot (DEBUG
# off) makes such environments visible instead of silently staying exposed.
#
# This is a one-off cleanup for those values, not a general secret scanner:
# it cannot catch a key committed in the future. The template now leaves each
# secret empty so no new shared value is introduced.
#
# Stored as SHA-256 digests so this file does not republish the values.
COMMITTED_SECRET_KEY_DIGESTS = frozenset({
    # Former inline fallback in backend/settings.py
    '74b6bf35804827ee73c32855d00d606b95952159c578eb9c265406cfb7a91355',
    # Former placeholder in env.example (the template now leaves it empty)
    '9185e3300d24e5439503a795c6709e6aadb8f7806a6795c6dcf473634dd1196a',
})


def validate_secret_setting(
    name: str,
    value: str,
    *,
    debug: bool,
    committed_digests: frozenset[str] = frozenset(),
    hint: str = TOKEN_GENERATE_HINT,
) -> str:
    """
    Return *value* if it is usable as the secret setting *name*, otherwise fail.

    - Missing or empty: always raises, so a misconfigured environment stops at
      boot instead of silently falling back to a public value.
    - A value whose digest is in *committed_digests*: raises when DEBUG is off;
      with DEBUG on (local Docker development) it only warns, so existing local
      setups keep working.
    """
    if not value:
        raise ImproperlyConfigured(f'{name} is not set. {hint}')

    digest = hashlib.sha256(value.encode()).hexdigest()
    if digest in committed_digests:
        message = (
            f'{name} is a value that has been committed to this repository '
            'and must not be used outside local development. '
            f'{hint}'
        )
        if not debug:
            raise ImproperlyConfigured(message)
        warnings.warn(message, RuntimeWarning, stacklevel=2)

    return value
