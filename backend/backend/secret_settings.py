"""
Validation for secret settings read from the environment.

Each secret validated here signs or protects credentials the platform issues:
SECRET_KEY signs every JWT (SIMPLE_JWT['SIGNING_KEY']) and is shared with
variations-studio-api, which verifies those tokens. Anyone who knows such a
secret can forge credentials (MED-393). The organization access-token keys sign
and encrypt the org-scoped token used by billing endpoints and tenant
resolution (MED-394).

Order of checks for each key (the first failing check stops the boot):

1. Missing, empty or whitespace-only -> always rejected.
2. A value that was committed to this repository -> rejected, unless legacy
   keys are explicitly allowed for local development (see ALLOW_LEGACY_ENV).
   An allowed legacy value only warns and skips the remaining checks.
3. General rules for weak values (Rules 1-4 below) -> rejected.

After all keys are read, validate_distinct_secrets() rejects any two keys that
share a value (Rule 5).
"""
import hashlib
import os
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

# Explicit, local-only switch that lets a DEBUG environment keep booting with a
# legacy committed key (it still warns). It is off unless set to a true value,
# and settings.py only honours it when DEBUG is also on, so a shared or deployed
# environment cannot be opened up by DEBUG alone or by this flag alone.
ALLOW_LEGACY_ENV = 'ALLOW_LEGACY_LOCAL_KEYS'
LEGACY_HINT = (
    f'For local development only, you can set {ALLOW_LEGACY_ENV}=true together '
    'with DEBUG=True to keep booting with a warning.'
)

# Why these lists exist: older versions of settings.py and env.example shipped
# secret values in plain text, and those values were copied into real .env
# files. Anyone who has read this repository knows them, so an environment still
# using one can have tokens forged against it. Rejecting them at boot makes such
# environments visible instead of silently staying exposed.
#
# This list only covers values that were actually in this repository. Weak
# values in general are caught by the weak-value rules below.
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

# General rules for weak values. They apply to every new value and are never
# relaxed by ALLOW_LEGACY_ENV.
#
# Rule 1 - Django's generated development key. `startproject` writes a key that
#          starts with this prefix and is explicitly marked insecure; it is
#          often copied as-is into other environments. Applies to all keys.
INSECURE_PREFIXES = ('django-insecure-',)
#
# Rule 2 - Placeholder words from templates and examples, such as
#          "your-secret-key-here" or "changeme". Matched case-insensitively
#          anywhere in the value. Applies to the two signing keys.
PLACEHOLDER_WORDS = (
    'changeme',
    'change-me',
    'change_me',
    'your-secret',
    'your_secret',
    'placeholder',
    'example',
    'dummy',
)
#
# Rule 3 - Minimum length for the two signing keys. The generate command in
#          TOKEN_GENERATE_HINT produces about 67 characters, CI uses 63, and
#          Django recommends at least 50. Hand-typed passwords are shorter.
MIN_SIGNING_KEY_LENGTH = 50
#
# Rule 4 - Minimum number of distinct characters for the two signing keys, so a
#          value cannot reach the minimum length by repetition ("aaaa...",
#          "abcabc..."). A random 67-character key has 40+ distinct characters.
MIN_DISTINCT_CHARACTERS = 10
#
# Rule 5 - The three keys must all differ (validate_distinct_secrets). Reusing
#          one value means leaking one key leaks all of them.
#
# The encryption key does not use rules 2-4: it must be a valid Fernet key,
# which is always 32 random bytes encoded as 44 characters.


def read_secret_env(name: str) -> str:
    """
    Read a secret from the process environment only.

    python-decouple would also search parent directories for a .env file, so the
    same code could find a key on one machine and not on another. Secrets come
    from the environment (Docker env_file, CI, deployment) and nowhere else.
    """
    return os.environ.get(name, '')


def read_bool_env(name: str) -> bool:
    """Read a boolean flag from the process environment only (default False)."""
    return os.environ.get(name, '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _normalize(value: str) -> str:
    """Strip surrounding whitespace and one layer of matching quotes."""
    candidate = value.strip()
    if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in '"\'':
        candidate = candidate[1:-1].strip()
    return candidate


def _check_weak_value(name: str, candidate: str, *, signing_key: bool, hint: str) -> None:
    """Apply weak-value Rules 1-4 to a value that is not a legacy key."""
    lowered = candidate.lower()
    if lowered.startswith(INSECURE_PREFIXES):
        raise ImproperlyConfigured(
            f'{name} is a Django development key (starts with "django-insecure-"). {hint}'
        )
    if not signing_key:
        return
    word = next((w for w in PLACEHOLDER_WORDS if w in lowered), None)
    if word:
        raise ImproperlyConfigured(
            f'{name} looks like a placeholder (contains "{word}"). {hint}'
        )
    if len(candidate) < MIN_SIGNING_KEY_LENGTH:
        raise ImproperlyConfigured(
            f'{name} is too short ({len(candidate)} characters, at least '
            f'{MIN_SIGNING_KEY_LENGTH} required). {hint}'
        )
    if len(set(candidate)) < MIN_DISTINCT_CHARACTERS:
        raise ImproperlyConfigured(
            f'{name} has too few distinct characters (at least '
            f'{MIN_DISTINCT_CHARACTERS} required). {hint}'
        )


def validate_secret_setting(
    name: str,
    value: str,
    *,
    allow_committed: bool,
    committed_digests: frozenset[str] = frozenset(),
    hint: str = TOKEN_GENERATE_HINT,
    signing_key: bool = True,
    stacklevel: int = 2,
) -> str:
    """
    Return *value* if it is usable as the secret setting *name*, otherwise fail.

    See the module docstring for the order of checks. *allow_committed* is True
    only for local development with the explicit legacy switch on; it lets a
    committed value boot with a warning. *signing_key* enables rules 2-4, which
    do not apply to the Fernet encryption key.

    The checks look at the value with surrounding whitespace and one pair of
    matching quotes removed, so a padded or quoted legacy value is still
    recognised. The value itself is returned unchanged.
    """
    candidate = _normalize(value)
    if not candidate:
        raise ImproperlyConfigured(f'{name} is not set. {hint}')

    digest = hashlib.sha256(candidate.encode()).hexdigest()
    if digest in committed_digests:
        message = (
            f'{name} is a value that has been committed to this repository '
            'and must not be used. '
            f'{hint}'
        )
        if not allow_committed:
            raise ImproperlyConfigured(f'{message} {LEGACY_HINT}')
        warnings.warn(message, RuntimeWarning, stacklevel=stacklevel)
        return value

    _check_weak_value(name, candidate, signing_key=signing_key, hint=hint)
    return value


def validate_fernet_key_setting(
    name: str,
    value: str,
    *,
    allow_committed: bool,
    committed_digests: frozenset[str] = frozenset(),
) -> str:
    """
    Validate a secret setting that is used as a Fernet key.

    Runs the same checks as validate_secret_setting (without the signing-key
    rules 2-4), then confirms Fernet accepts the value. A malformed key would
    otherwise only fail at first use, where the caller swallows the error (login
    succeeds without an org token and billing endpoints return 403), so it is
    rejected at boot instead.
    """
    validate_secret_setting(
        name,
        value,
        allow_committed=allow_committed,
        committed_digests=committed_digests,
        hint=FERNET_GENERATE_HINT,
        signing_key=False,
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


def validate_distinct_secrets(**secrets: str) -> None:
    """Rule 5: reject any two secret settings that share a value."""
    names = list(secrets)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            if secrets[first] == secrets[second]:
                raise ImproperlyConfigured(
                    f'{first} and {second} have the same value. Each key must be '
                    'generated separately.'
                )
