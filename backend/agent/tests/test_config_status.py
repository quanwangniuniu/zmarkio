"""GET /api/agent/config/status/ reports whether the LLM server is configured."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase


class AgentConfigStatusTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='config-status', email='config-status@test.com', password='x',
        )
        self.client.force_authenticate(user=self.user)
        self.url = reverse('agent-config-status')

    @override_settings(OLLAMA_BASE_URL='http://ollama.test:11434')
    def test_reports_ollama_configured(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.json()['ollama'], True)
        self.assertNotIn('gemini', resp.json())

    @override_settings(OLLAMA_BASE_URL='')
    @patch.dict('os.environ', {'OLLAMA_BASE_URL': ''})
    def test_reports_ollama_missing(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.json()['ollama'], False)
