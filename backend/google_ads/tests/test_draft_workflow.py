import base64
import json
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from google_ads.models import Ad

User = get_user_model()


class AdDraftWorkflowTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='draft-owner', email='draft-owner@example.test')
        self.client.force_authenticate(user=self.user)
        created = self.client.post('/api/google_ads/ads/', {
            'name': 'Accountless draft', 'type': 'RESPONSIVE_SEARCH_AD',
            'status': 'DRAFT', 'final_urls': ['https://example.com/summer'],
        }, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.ad = Ad.objects.get(pk=created.data['id'])
        self.assertEqual(self.ad.created_by, self.user)
        self.assertIsNone(self.ad.customer_account_id)
        self.detail_url = f'/api/google_ads/ads/{self.ad.id}/'

    def test_owner_can_list_draft_without_customer_account(self):
        response = self.client.get('/api/google_ads/ads/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([ad['id'] for ad in response.data['results']], [self.ad.id])

    def test_owner_can_open_update_and_delete_draft_without_customer_account(self):
        self.assertEqual(self.client.get(self.detail_url).status_code, status.HTTP_200_OK)
        updated = self.client.patch(self.detail_url, {'name': 'Updated draft'}, format='json')
        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.name, 'Updated draft')
        self.assertEqual(self.client.delete(self.detail_url).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Ad.objects.filter(pk=self.ad.id).exists())

    def test_other_user_cannot_access_owned_draft(self):
        other = User.objects.create_user(username='outsider', email='outsider@example.test')
        self.client.force_authenticate(user=other)
        response = self.client.get('/api/google_ads/ads/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'], [])
        for method in ('get', 'patch', 'delete'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.detail_url)
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Ad.objects.filter(pk=self.ad.id).exists())

    def test_share_url_targets_public_preview_page_with_valid_payload(self):
        response = self.client.post(
            f'/api/google_ads/{self.ad.id}/create_preview/',
            {'device_type': 'DESKTOP'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        url = urlsplit(response.data['preview_url'])
        self.assertEqual(url.path, '/google_ads/preview/share')
        encoded = parse_qs(url.query)['share'][0]
        payload = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        self.assertEqual(payload['previewToken'], response.data['token'])
        self.assertEqual(payload['device'], 'DESKTOP')
        self.client.force_authenticate(user=None)
        public = self.client.get(f"/api/google_ads/preview/{payload['previewToken']}/")
        self.assertEqual(public.status_code, status.HTTP_200_OK)
