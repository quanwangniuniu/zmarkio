"""
Central log redaction for the agent app.

Provider clients (llm_client.py, gemini_client.py) embed API keys in request
URLs and headers. The `requests`/`urllib3` libraries can log full URLs and
headers at DEBUG level, and raw exception text can leak keys into tracebacks.
This module scrubs both structured logging args and free-text log messages
before they reach any handler (console, Loki, etc).
"""
import logging
import re

REDACTED = "***REDACTED***"

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "openai-api-key",
    "anthropic-api-key",
    "x-goog-api-key",
    "cookie",
    "set-cookie",
}

_BEARER_RE = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_QUERY_PARAM_RE = re.compile(r"([?&](?:key|token)=)([^&\s\"']+)", re.IGNORECASE)
_OPENAI_ANTHROPIC_KEY_RE = re.compile(r"sk-[A-Za-z0-9\-_]{6,}")
_GOOGLE_KEY_RE = re.compile(r"AIzaSy[A-Za-z0-9_\-]{10,}")


def _is_sensitive_key(key) -> bool:
    return isinstance(key, str) and key.lower() in SENSITIVE_HEADER_NAMES


def redact_headers(headers: dict) -> dict:
    """Return a copy of `headers` with sensitive values masked."""
    if not isinstance(headers, dict):
        return headers
    return {
        key: (REDACTED if _is_sensitive_key(key) else value)
        for key, value in headers.items()
    }


def redact_string(text: str) -> str:
    """Scrub known secret patterns (Bearer tokens, sk-*, AIzaSy*, ?key=/?token=) from free text."""
    if not isinstance(text, str) or not text:
        return text
    text = _BEARER_RE.sub(f"Bearer {REDACTED}", text)
    text = _QUERY_PARAM_RE.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _OPENAI_ANTHROPIC_KEY_RE.sub(REDACTED, text)
    text = _GOOGLE_KEY_RE.sub(REDACTED, text)
    return text


def _redact_value(value):
    if isinstance(value, dict):
        return {
            key: (REDACTED if _is_sensitive_key(key) else _redact_value(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, str):
        return redact_string(value)
    return value


class RedactSecretsFilter(logging.Filter):
    """Sanitises log records in place. Never suppresses a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_string(record.msg)
        if record.args:
            record.args = _redact_value(record.args)
        return True
