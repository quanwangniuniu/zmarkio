"""API tests for Quick Start preview endpoint (qs-1.3)."""

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework import status

from core.services.quick_start.constants import MAX_PROMPT_LENGTH, MAX_SUPPLEMENT_LENGTH
from core.services.quick_start.exceptions import QuickStartLLMError
from core.tests.test_quick_start_llm import DEFAULT_MODULES, SAMPLE_LLM_PAYLOAD

PREVIEW_URL = 'quick-start-preview'


def _preview_payload(**overrides):
    base = {
        'step': 2,
        'prompt': 'US Meta Q2 campaign with $30k monthly budget for six weeks',
        'selected_modules': DEFAULT_MODULES,
    }
    base.update(overrides)
    return base


@pytest.mark.django_db
class TestQuickStartPreviewAPI:
    def test_requires_authentication(self, api_client):
        url = reverse(PREVIEW_URL)
        response = api_client.post(url, _preview_payload(), format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_step_one_returns_without_blueprint(self, authenticated_client):
        url = reverse(PREVIEW_URL)
        response = authenticated_client.post(
            url,
            {
                'step': 1,
                'prompt': 'US Meta Q2 campaign with $30k monthly budget for six weeks',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['step'] == 1
        assert response.data['blueprint'] is None
        assert response.data['outline'] is None

    @patch('core.quick_start_views.QuickStartPreviewService.generate_preview')
    def test_step_two_success(self, mock_generate, authenticated_client):
        mock_generate.return_value = {
            'step': 2,
            'outline': {
                'project': {'title': 'Q1 Meta Launch', 'description': 'Test'},
                'modules': {'tasks': {'enabled': True, 'count': 5}},
                'tasks': [{'summary': 'Task 1'}],
                'spreadsheets': [],
                'calendar_events': [],
            },
            'blueprint': SAMPLE_LLM_PAYLOAD,
        }
        url = reverse(PREVIEW_URL)
        response = authenticated_client.post(url, _preview_payload(), format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['outline']['project']['title'] == 'Q1 Meta Launch'
        assert response.data['blueprint']['project']['name'] == 'Q1 Meta Launch'
        mock_generate.assert_called_once()

    def test_short_prompt_returns_400(self, authenticated_client):
        url = reverse(PREVIEW_URL)
        response = authenticated_client.post(
            url,
            {'step': 2, 'prompt': 'short'},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error'] == 'validation_failed'
        assert 'too short' in response.data['detail'].lower()

    @patch('core.quick_start_views.QuickStartPreviewService.generate_preview')
    def test_llm_error_returns_502(self, mock_generate, authenticated_client):
        mock_generate.side_effect = QuickStartLLMError('Ollama unavailable')
        url = reverse(PREVIEW_URL)
        response = authenticated_client.post(url, _preview_payload(), format='json')

        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert response.data['error'] == 'llm_generation_failed'

    @patch('core.quick_start_views.QuickStartPreviewService.generate_preview')
    def test_rate_limited_returns_friendly_payload(self, mock_generate, authenticated_client):
        mock_generate.side_effect = QuickStartLLMError(
            'Ollama rate limited (HTTP 429).',
            error_code='rate_limited',
            user_message='AI requests are coming in too quickly. Please wait 30 seconds, then try again.',
            retry_after_seconds=30,
        )
        url = reverse(PREVIEW_URL)
        response = authenticated_client.post(url, _preview_payload(), format='json')

        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert response.data['error'] == 'rate_limited'
        assert response.data['retry_after_seconds'] == 30
        assert '30 seconds' in response.data['detail']
        assert '429' not in response.data['detail']

    def test_prompt_over_max_length_returns_400(self, authenticated_client):
        url = reverse(PREVIEW_URL)
        response = authenticated_client.post(
            url,
            {
                'step': 1,
                'prompt': 'x' * (MAX_PROMPT_LENGTH + 1),
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error'] == 'validation_failed'

    def test_supplement_over_max_length_returns_400(self, authenticated_client):
        url = reverse(PREVIEW_URL)
        response = authenticated_client.post(
            url,
            {
                'step': 2,
                'prompt': 'US Meta Q2 campaign with $30k monthly budget for six weeks',
                'supplement': 'x' * (MAX_SUPPLEMENT_LENGTH + 1),
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error'] == 'validation_failed'

    def test_invalid_step_serializer_error(self, authenticated_client):
        url = reverse(PREVIEW_URL)
        response = authenticated_client.post(
            url,
            {
                'step': 3,
                'prompt': 'US Meta Q2 campaign with $30k monthly budget for six weeks',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error'] == 'validation_failed'
