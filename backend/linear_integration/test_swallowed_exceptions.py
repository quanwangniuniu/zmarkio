"""
Linear token error parsing falls back to the raw body when it is not JSON,
and now catches only that case (MED-401).
"""
from unittest.mock import MagicMock

import pytest
import requests

from linear_integration.services import _token_error_message


def test_non_json_error_body_falls_back_to_raw_text():
    response = MagicMock(text="<html>502 Bad Gateway</html>")
    response.json.side_effect = requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)

    assert _token_error_message(response) == "<html>502 Bad Gateway</html>"


def test_unexpected_error_is_no_longer_swallowed():
    response = MagicMock(text="")
    response.json.side_effect = RuntimeError("bug")

    with pytest.raises(RuntimeError):
        _token_error_message(response)
