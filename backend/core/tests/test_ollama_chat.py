"""Tests for core.services.ollama_chat. HTTP is mocked."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from core.services.ollama_chat import OllamaChatResponseError, chat


def _response(status_code, payload):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


@override_settings(RAG_OLLAMA_BASE_URL='http://ollama.test:11434', RAG_OLLAMA_TIMEOUT_SECONDS=5)
def test_chat_posts_api_chat_with_think_disabled():
    with patch('core.services.ollama_chat.requests.post') as mock_post:
        mock_post.return_value = _response(200, {
            'message': {'role': 'assistant', 'content': 'Why vendor A?'},
            'prompt_eval_count': 11,
            'eval_count': 4,
        })
        result = chat(
            model='qwen3:4b',
            system_prompt='sys',
            user_prompt='user',
            temperature=0,
            max_output_tokens=256,
            timeout=15,
        )

    assert result == {
        'text': 'Why vendor A?',
        'prompt_eval_count': 11,
        'eval_count': 4,
    }
    args, kwargs = mock_post.call_args
    assert args[0] == 'http://ollama.test:11434/api/chat'
    assert kwargs['timeout'] == 15
    assert kwargs['json']['model'] == 'qwen3:4b'
    assert kwargs['json']['stream'] is False
    assert kwargs['json']['think'] is False
    assert kwargs['json']['messages'] == [
        {'role': 'system', 'content': 'sys'},
        {'role': 'user', 'content': 'user'},
    ]
    assert kwargs['json']['options'] == {'temperature': 0, 'num_predict': 256}
    assert 'format' not in kwargs['json']


@override_settings(RAG_OLLAMA_BASE_URL='http://ollama.test:11434', RAG_OLLAMA_TIMEOUT_SECONDS=5)
def test_chat_includes_format_only_when_the_caller_passes_one():
    schema = {'type': 'object', 'properties': {'question': {'type': 'string'}}, 'required': ['question']}
    with patch('core.services.ollama_chat.requests.post') as mock_post:
        mock_post.return_value = _response(200, {
            'message': {'content': '{"question": "Why?"}'},
            'prompt_eval_count': 1,
            'eval_count': 1,
        })
        chat(
            model='qwen3:4b',
            system_prompt='s',
            user_prompt='u',
            temperature=0,
            max_output_tokens=64,
            format=schema,
        )

    assert mock_post.call_args.kwargs['json']['format'] == schema


@override_settings(RAG_OLLAMA_BASE_URL='http://ollama.test:11434')
def test_chat_retries_503_then_reads_the_message():
    with patch('core.services.ollama_chat.requests.post') as mock_post, patch(
        'core.services.ollama_chat.time.sleep',
    ) as mock_sleep:
        mock_post.side_effect = [
            _response(503, {}),
            _response(200, {'message': {'content': 'ok'}, 'prompt_eval_count': 1, 'eval_count': 1}),
        ]
        result = chat(
            model='qwen3:4b',
            system_prompt='s',
            user_prompt='u',
            temperature=0.3,
            max_output_tokens=32,
        )

    assert result['text'] == 'ok'
    assert mock_post.call_count == 2
    mock_sleep.assert_called_once_with(1.0)


@override_settings(RAG_OLLAMA_BASE_URL='http://ollama.test:11434')
def test_chat_rejects_a_body_without_message_content():
    with patch('core.services.ollama_chat.requests.post') as mock_post:
        mock_post.return_value = _response(200, {'message': {'thinking': 'secret reasoning'}})
        with pytest.raises(OllamaChatResponseError, match='message content'):
            chat(
                model='qwen3:4b',
                system_prompt='s',
                user_prompt='u',
                temperature=0,
                max_output_tokens=32,
            )
