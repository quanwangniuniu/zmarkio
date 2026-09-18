import logging

from core.services.log_redaction import RedactSecretsFilter, redact_headers, redact_string


def test_redact_headers_masks_sensitive_keys():
    headers = {
        "Authorization": "Bearer sk-live-abc123",
        "Content-Type": "application/json",
        "authorization": "Bearer sk-live-xyz789",
        "X-Api-Key": "abc123",
    }
    redacted = redact_headers(headers)

    assert redacted["Authorization"] == "***REDACTED***"
    assert redacted["authorization"] == "***REDACTED***"
    assert redacted["X-Api-Key"] == "***REDACTED***"
    assert redacted["Content-Type"] == "application/json"


def test_redact_string_bearer_token():
    assert redact_string("Bearer sk-proj-abc123xyz") == "Bearer ***REDACTED***"


def test_redact_string_query_param_key():
    result = redact_string("?key=AIzaSyD_secretkey123456789012345678")
    assert "AIzaSyD_secretkey123456789012345678" not in result
    assert "?key=***REDACTED***" in result


def test_redact_string_sk_pattern():
    assert redact_string("sk-ant-api03-longkey123") == "***REDACTED***"


def test_redact_string_preserves_safe_text():
    text = "Calling Gemini model=gemini-2.5-flash-lite system_chars=120 user_chars=40"
    assert redact_string(text) == text


def test_filter_redacts_structured_args():
    record = logging.LogRecord(
        name="agent.test",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg="headers=%s",
        args=({"Authorization": "Bearer sk-live-xxx"},),
        exc_info=None,
    )
    RedactSecretsFilter().filter(record)
    formatted = record.getMessage()

    assert "sk-live" not in formatted
    assert "***REDACTED***" in formatted


def test_filter_redacts_via_caplog(caplog):
    logger = logging.getLogger("agent.test_log_redaction")
    logger.addFilter(RedactSecretsFilter())

    with caplog.at_level(logging.DEBUG, logger="agent.test_log_redaction"):
        logger.debug("headers=%s", {"Authorization": "Bearer sk-test-key123"})

    assert "***REDACTED***" in caplog.text
    assert "sk-test-key123" not in caplog.text


def test_url_key_param_redacted():
    url = "https://example.com/v1/model:generate?key=AIzaSyDsecretkey12345678901234567890"
    result = redact_string(url)

    assert "AIzaSyDsecretkey12345678901234567890" not in result
    assert "?key=***REDACTED***" in result
