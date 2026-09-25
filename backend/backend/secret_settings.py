"""
Validation for secret settings read from the environment.

Each secret validated here signs or protects credentials the platform issues:
SECRET_KEY signs every JWT (SIMPLE_JWT['SIGNING_KEY']) and is shared with
variations-studio-api and decision-service, which verify those tokens. Anyone
who knows such a secret can forge credentials, so a value that has ever been
committed to this repository must never be accepted outside local development
(MED-393). The organization access-token keys sign and encrypt the org-scoped
token used by billing endpoints and tenant resolution (MED-394).
"""
import hashlib
import warnings

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

TOKEN_GENERATE_HINT = (
    'Generate a new key with: '
    'python3 -c "import secrets; print(secrets.token_urlsafe(50))"'
)
FERNET_GENERATE_HINT = (
    'Generate a new key with: '
    'python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
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
    # Earlier env.example placeholders (2025-06 to 2025-09)
    '55a2aa619c852bbc2a756b83696b60ab1b91ab7163f3e0d8463da815fb93a268',
    '021ed2c9c90f9eac77e634a56b5c357389e3fc394668fc3296beed353494d089',
})
COMMITTED_ORG_TOKEN_SECRET_KEY_DIGESTS = frozenset({
    # Former inline fallback in backend/settings.py, also the former env.example value
    'd387fa62271c46f334930945e46dfe24eb6a0c237de06c1a9fd7b8775e0878ee',
})
COMMITTED_ORG_TOKEN_ENCRYPTION_KEY_DIGESTS = frozenset({
    # Former inline fallback in backend/settings.py, also the former env.example value
    '2d17c6fb1a2dda212fc2d82187a772d06d9964e7a55c3a2544e16130311c01f8',
})


def _normalize(value: str) -> str:
    """Strip surrounding whitespace and one layer of matching quotes."""
    candidate = value.strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in '"\'':
        candidate = candidate[1:-1].strip()
    return candidate


def validate_secret_setting(
    name: str,
    value: str,
    *,
    debug: bool,
    committed_digests: frozenset[str] = frozenset(),
    hint: str = TOKEN_GENERATE_HINT,
    stacklevel: int = 2,
) -> str:
    """
    Return *value* if it is usable as the secret setting *name*, otherwise fail.

    - Missing, empty or whitespace-only: always raises, so a misconfigured
      environment stops at boot instead of silently falling back to a public
      value.
    - A value whose digest is in *committed_digests*: raises when DEBUG is off;
      with DEBUG on (local Docker development) it only warns, so existing local
      setups keep working.

    Both checks look at the value with surrounding whitespace and quotes
    removed. docker compose strips these when it reads an env file, but other
    ways of injecting environment variables do not, and a committed value
    padded with a space or wrapped in quotes is still a public value. The
    value itself is returned unchanged.
    """
    candidate = _normalize(value)
    if not candidate:
        raise ImproperlyConfigured(f'{name} is not set. {hint}')

    digest = hashlib.sha256(candidate.encode()).hexdigest()
    if digest in committed_digests:
        message = (
            f'{name} is a value that has been committed to this repository '
            'and must not be used outside local development. '
            f'{hint}'
        )
        if not debug:
            raise ImproperlyConfigured(message)
        warnings.warn(message, RuntimeWarning, stacklevel=stacklevel)

    return value


def validate_fernet_key_setting(
    name: str,
    value: str,
    *,
    debug: bool,
    committed_digests: frozenset[str] = frozenset(),
) -> str:
    """
    Validate a secret setting that is used as a Fernet key.

    Runs the same checks as validate_secret_setting, then confirms Fernet
    accepts the value. A malformed key would otherwise only fail at first use,
    where the caller swallows the error (login succeeds without an org token
    and billing endpoints return 403), so it is rejected at boot instead.
    """
    validate_secret_setting(
        name,
        value,
        debug=debug,
        committed_digests=committed_digests,
        hint=FERNET_GENERATE_HINT,
        stacklevel=3,  # point the warning at settings.py, not this wrapper
    )
    try:
        Fernet(value.encode())
    except ValueError as exc:
        raise ImproperlyConfigured(
            f'{name} is not a valid Fernet key (32 url-safe base64-encoded bytes). '
            f'{FERNET_GENERATE_HINT}'
        ) from exc
    return value
