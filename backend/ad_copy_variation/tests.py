import uuid
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from facebook_integration.models import FacebookConnection, MetaAdAccount
from core.models import Organization, Project, ProjectMember
from meta_ads.models import MetaAdCreative

from . import services
from .models import AdCopyVariation


def _http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    exc = requests.HTTPError(f'{status_code} Client Error', response=response)
    return exc


def _make_user(username='copy_user', email='copy_user@example.com'):
    User = get_user_model()
    return User.objects.create_user(username=username, email=email, password='x')


def _make_project(user, *, name='Copy Project'):
    org, _ = Organization.objects.get_or_create(
        name=f'Org {user.id}',
        defaults={'slug': f'org-{user.id}'},
    )
    project = Project.objects.create(name=name, organization=org, owner=user)
    ProjectMember.objects.create(user=user, project=project, role='owner')
    user.active_project = project
    user.save(update_fields=['active_project'])
    return project


def _make_creative(user, *, project=None, meta_creative_id='cra-1', title='Headline A', body='Hook line\nDescription line', cta='SHOP_NOW'):
    connection = FacebookConnection.objects.create(
        user=user, fb_user_id=f'fb-{user.id}', is_active=True,
    )
    ad_account = MetaAdAccount.objects.create(
        connection=connection,
        meta_account_id=f'acc-{user.id}',
        name='Test Account',
        currency='USD',
        project=project,
    )
    return MetaAdCreative.objects.create(
        ad_account=ad_account,
        meta_creative_id=meta_creative_id,
        name=meta_creative_id,
        title=title,
        body=body,
        call_to_action_type=cta,
    )


_FAKE_GEMINI_RESPONSE = {
    'hook': 'Generated hook line',
    'headline': 'Generated headline',
    'description': 'Generated description',
    'cta': 'LEARN_MORE',
}


class AdCopyVariationCRUDTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _make_user()
        cls.project = _make_project(cls.user)
        cls.creative = _make_creative(cls.user, project=cls.project)

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def _payload(self, **overrides):
        base = {
            'creative': self.creative.id,
            'project': self.project.id,
            'source_mode': 'existing',
            'hook': 'h',
            'headline': 'hl',
            'description': 'd',
            'cta': 'SHOP_NOW',
            'instruction': 'rewrite',
        }
        base.update(overrides)
        return base

    def test_create_then_list(self):
        url = reverse('ad-copy-variation-list')
        resp = self.client.post(url, self._payload(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

        list_resp = self.client.get(url)
        self.assertEqual(list_resp.status_code, status.HTTP_200_OK)
        body = list_resp.data
        results = body['results'] if isinstance(body, dict) and 'results' in body else body
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['hook'], 'h')

    def test_retrieve(self):
        row = AdCopyVariation.objects.create(
            project=self.project,
            creative=self.creative,
            source_mode='custom',
            hook='retrieve hook',
            created_by=self.user,
        )
        url = reverse('ad-copy-variation-detail', args=[row.slug])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['hook'], 'retrieve hook')
        self.assertEqual(resp.data['source_mode'], 'custom')

    def test_numeric_id_url_is_rejected(self):
        """Resource lookups are slug-only — a numeric id must 404 (not resolve by pk)."""
        row = AdCopyVariation.objects.create(
            project=self.project,
            creative=self.creative,
            source_mode='custom',
            hook='numeric reject',
            created_by=self.user,
        )
        url = reverse('ad-copy-variation-detail', args=[row.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_patch(self):
        row = AdCopyVariation.objects.create(
            project=self.project,
            creative=self.creative,
            source_mode='existing',
            hook='before',
            created_by=self.user,
        )
        url = reverse('ad-copy-variation-detail', args=[row.slug])
        resp = self.client.patch(url, {'hook': 'after'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        row.refresh_from_db()
        self.assertEqual(row.hook, 'after')

    def test_delete(self):
        row = AdCopyVariation.objects.create(
            project=self.project,
            creative=self.creative,
            source_mode='existing',
            created_by=self.user,
        )
        url = reverse('ad-copy-variation-detail', args=[row.slug])
        resp = self.client.delete(url)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(AdCopyVariation.objects.filter(pk=row.id).exists())

    def test_create_sets_created_by_to_request_user(self):
        url = reverse('ad-copy-variation-list')
        resp = self.client.post(url, self._payload(), format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        row = AdCopyVariation.objects.get(pk=resp.data['id'])
        self.assertEqual(row.created_by_id, self.user.id)

    def test_create_requires_membership_in_project(self):
        other_user = _make_user(username='outside_member', email='outside_member@example.com')
        other_project = _make_project(other_user, name='Restricted Project')
        url = reverse('ad-copy-variation-list')

        resp = self.client.post(
            url,
            self._payload(project=other_project.id),
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN, resp.data)
        self.assertEqual(AdCopyVariation.objects.count(), 0)

    def test_create_rejects_null_project(self):
        url = reverse('ad-copy-variation-list')

        resp = self.client.post(
            url,
            self._payload(project=None),
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(AdCopyVariation.objects.count(), 0)

    def test_create_rejects_cross_project_creative(self):
        other_user = _make_user(username='creative_other', email='creative_other@example.com')
        other_project = _make_project(other_user, name='Creative Other Project')
        other_creative = _make_creative(
            other_user,
            project=other_project,
            meta_creative_id='cross-project-creative',
        )
        url = reverse('ad-copy-variation-list')

        resp = self.client.post(
            url,
            self._payload(creative=other_creative.id),
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        self.assertEqual(resp.data['error'], 'creative does not belong to project')
        self.assertEqual(AdCopyVariation.objects.count(), 0)

    def test_filter_by_creative(self):
        other_user = _make_user(username='other', email='other@example.com')
        other_project = _make_project(other_user, name='Other Project')
        other_creative = _make_creative(
            other_user,
            project=other_project,
            meta_creative_id='cra-2',
        )
        AdCopyVariation.objects.create(
            project=self.project, creative=self.creative, source_mode='existing', created_by=self.user,
        )
        AdCopyVariation.objects.create(
            project=other_project, creative=other_creative, source_mode='existing',
        )
        url = reverse('ad-copy-variation-list')
        resp = self.client.get(url, {'creative': self.creative.id})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        body = resp.data
        results = body['results'] if isinstance(body, dict) and 'results' in body else body
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['creative'], self.creative.id)

    def test_patch_rejects_project_change(self):
        other_user = _make_user(username='patch_other', email='patch_other@example.com')
        other_project = _make_project(other_user, name='Patch Other Project')
        row = AdCopyVariation.objects.create(
            project=self.project,
            creative=self.creative,
            source_mode='existing',
            hook='before',
            created_by=self.user,
        )
        url = reverse('ad-copy-variation-detail', args=[row.slug])

        resp = self.client.patch(
            url,
            {'project': other_project.id, 'hook': 'after'},
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, resp.data)
        row.refresh_from_db()
        self.assertEqual(row.project_id, self.project.id)
        self.assertEqual(row.hook, 'before')


class AdCopyVariationDraftLifecycleTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _make_user(username='draft_lifecycle')
        cls.project = _make_project(cls.user, name='Draft Project')
        cls.creative = _make_creative(cls.user, project=cls.project, meta_creative_id='draft-cra')

        cls.other_user = _make_user(username='draft_other', email='draft_other@example.com')
        cls.other_project = _make_project(cls.other_user, name='Other Draft Project')

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def _row(self, *, batch_id=None, status_value='draft', project=None, creative=None, position=0):
        return AdCopyVariation.objects.create(
            project=project or self.project,
            creative=creative if creative is not None else self.creative,
            source_mode='existing',
            hook=f'hook {position}',
            headline=f'headline {position}',
            description='desc',
            cta='SHOP_NOW',
            batch_id=batch_id or uuid.uuid4(),
            batch_position=position,
            status=status_value,
            created_by=self.user,
        )

    def test_list_filters_by_project_status_source_creative_and_paginates(self):
        batch_id = uuid.uuid4()
        wanted = self._row(batch_id=batch_id, status_value='draft', position=1)
        self._row(batch_id=batch_id, status_value='reviewed', position=2)
        self._row(status_value='draft', project=self.other_project, creative=None, position=3)

        resp = self.client.get(reverse('ad-copy-variation-list'), {
            'project_id': self.project.slug,
            'status': 'draft',
            'source_mode': 'existing',
            'creative': self.creative.id,
            'page': 1,
            'page_size': 1,
        })

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['page'], 1)
        self.assertEqual(resp.data['page_size'], 1)
        self.assertEqual(resp.data['results'][0]['id'], wanted.id)

    def test_list_filters_by_batch_id(self):
        wanted_batch = uuid.uuid4()
        other_batch = uuid.uuid4()
        wanted = self._row(batch_id=wanted_batch, position=1)
        self._row(batch_id=other_batch, position=2)

        resp = self.client.get(reverse('ad-copy-variation-list'), {
            'project_id': self.project.slug,
            'batch_id': str(wanted_batch),
        })

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['count'], 1)
        self.assertEqual(resp.data['results'][0]['id'], wanted.id)

    def test_latest_batch_returns_most_recent_batch_for_project(self):
        older_batch = uuid.uuid4()
        latest_batch = uuid.uuid4()
        other_project_batch = uuid.uuid4()
        self._row(batch_id=older_batch, position=1)
        latest_first = self._row(batch_id=latest_batch, position=1)
        latest_second = self._row(batch_id=latest_batch, position=2)
        self._row(batch_id=other_project_batch, project=self.other_project, creative=None, position=1)

        resp = self.client.get(reverse('ad-copy-variation-latest-batch'), {
            'project_id': self.project.slug,
        })

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['batch_id'], str(latest_batch))
        self.assertEqual(resp.data['count'], 2)
        self.assertEqual(
            [row['id'] for row in resp.data['results']],
            [latest_first.id, latest_second.id],
        )

    def test_latest_batch_returns_empty_when_project_has_no_batches(self):
        resp = self.client.get(reverse('ad-copy-variation-latest-batch'), {
            'project_id': self.project.slug,
        })

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertIsNone(resp.data['batch_id'])
        self.assertEqual(resp.data['count'], 0)
        self.assertEqual(resp.data['results'], [])

    def test_review_batch_reviews_selected_and_deletes_unselected_current_batch_drafts(self):
        batch_id = uuid.uuid4()
        selected = self._row(batch_id=batch_id, position=1)
        unselected = self._row(batch_id=batch_id, position=2)
        already_reviewed = self._row(batch_id=batch_id, status_value='reviewed', position=3)
        previous_batch = self._row(batch_id=uuid.uuid4(), position=4)
        other_project_row = self._row(batch_id=batch_id, project=self.other_project, creative=None, position=5)

        resp = self.client.post(
            reverse('ad-copy-variation-review-batch'),
            {
                'project_id': self.project.id,
                'batch_id': str(batch_id),
                'selected_ids': [selected.id],
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['reviewed_count'], 1)
        self.assertEqual(resp.data['deleted_count'], 1)
        self.assertEqual(
            set(resp.data.keys()),
            {'batch_id', 'reviewed_count', 'deleted_count', 'results'},
        )
        self.assertEqual(
            {row['id'] for row in resp.data['results']},
            {selected.id, already_reviewed.id},
        )

        selected.refresh_from_db()
        already_reviewed.refresh_from_db()
        previous_batch.refresh_from_db()
        other_project_row.refresh_from_db()

        self.assertEqual(selected.status, 'reviewed')
        self.assertFalse(AdCopyVariation.objects.filter(pk=unselected.id).exists())
        self.assertEqual(already_reviewed.status, 'reviewed')
        self.assertEqual(previous_batch.status, 'draft')
        self.assertEqual(other_project_row.status, 'draft')

    def test_review_batch_rejects_reviewed_selected_id_without_changing_data(self):
        batch_id = uuid.uuid4()
        draft = self._row(batch_id=batch_id, position=1)
        reviewed = self._row(batch_id=batch_id, status_value='reviewed', position=2)

        resp = self.client.post(
            reverse('ad-copy-variation-review-batch'),
            {
                'project_id': self.project.id,
                'batch_id': str(batch_id),
                'selected_ids': [reviewed.id],
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        draft.refresh_from_db()
        reviewed.refresh_from_db()
        self.assertEqual(draft.status, 'draft')
        self.assertEqual(reviewed.status, 'reviewed')

    def test_review_batch_rejects_invalid_selected_ids_without_reviewing(self):
        batch_id = uuid.uuid4()
        draft = self._row(batch_id=batch_id, position=1)
        unselected = self._row(batch_id=batch_id, position=2)

        resp = self.client.post(
            reverse('ad-copy-variation-review-batch'),
            {
                'project_id': self.project.id,
                'batch_id': str(batch_id),
                'selected_ids': [999999],
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        draft.refresh_from_db()
        unselected.refresh_from_db()
        self.assertEqual(draft.status, 'draft')
        self.assertEqual(unselected.status, 'draft')

    def test_bulk_review_updates_draft_rows(self):
        first = self._row(position=1)
        second = self._row(position=2)

        resp = self.client.post(
            reverse('ad-copy-variation-bulk-review'),
            {
                'project_id': self.project.id,
                'selected_ids': [first.id, second.id],
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['reviewed_count'], 2)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, 'reviewed')
        self.assertEqual(second.status, 'reviewed')

    def test_bulk_review_rejects_reviewed_rows_without_changing_data(self):
        draft = self._row(position=1)
        reviewed = self._row(status_value='reviewed', position=2)

        resp = self.client.post(
            reverse('ad-copy-variation-bulk-review'),
            {
                'project_id': self.project.id,
                'selected_ids': [draft.id, reviewed.id],
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        draft.refresh_from_db()
        reviewed.refresh_from_db()
        self.assertEqual(draft.status, 'draft')
        self.assertEqual(reviewed.status, 'reviewed')

    def test_bulk_delete_deletes_draft_rows(self):
        first = self._row(position=1)
        second = self._row(position=2)

        resp = self.client.post(
            reverse('ad-copy-variation-bulk-delete'),
            {
                'project_id': self.project.id,
                'selected_ids': [first.id, second.id],
                'status': 'draft',
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['deleted_count'], 2)
        self.assertFalse(AdCopyVariation.objects.filter(pk__in=[first.id, second.id]).exists())

    def test_bulk_delete_deletes_reviewed_rows(self):
        reviewed = self._row(status_value='reviewed', position=1)

        resp = self.client.post(
            reverse('ad-copy-variation-bulk-delete'),
            {
                'project_id': self.project.id,
                'selected_ids': [reviewed.id],
                'status': 'reviewed',
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['deleted_count'], 1)
        self.assertFalse(AdCopyVariation.objects.filter(pk=reviewed.id).exists())

    def test_bulk_delete_rejects_cross_project_ids_without_deleting(self):
        local = self._row(position=1)
        other_project_row = self._row(project=self.other_project, creative=None, position=2)

        resp = self.client.post(
            reverse('ad-copy-variation-bulk-delete'),
            {
                'project_id': self.project.id,
                'selected_ids': [local.id, other_project_row.id],
                'status': 'draft',
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(AdCopyVariation.objects.filter(pk=local.id).exists())
        self.assertTrue(AdCopyVariation.objects.filter(pk=other_project_row.id).exists())

    def test_bulk_delete_rejects_mixed_status_when_status_requested(self):
        draft = self._row(position=1)
        reviewed = self._row(status_value='reviewed', position=2)

        resp = self.client.post(
            reverse('ad-copy-variation-bulk-delete'),
            {
                'project_id': self.project.id,
                'selected_ids': [draft.id, reviewed.id],
                'status': 'draft',
            },
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(AdCopyVariation.objects.filter(pk=draft.id).exists())
        self.assertTrue(AdCopyVariation.objects.filter(pk=reviewed.id).exists())


class GenerateFromExistingTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _make_user(username='gen_existing')
        cls.project = _make_project(cls.user, name='Existing Project')
        cls.creative = _make_creative(cls.user, project=cls.project, meta_creative_id='gex-1')

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.url = reverse('ad-copy-variation-generate')

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_returns_json_shape(self, mock_call):
        mock_call.return_value = _FAKE_GEMINI_RESPONSE
        resp = self.client.post(
            self.url,
            {
                'source_mode': 'existing',
                'project_id': self.project.id,
                'creative_id': self.creative.id,
                'instruction': 'shorten the hook',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['count_succeeded'], 1)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['hook'], 'Generated hook line')
        self.assertEqual(resp.data['results'][0]['status'], 'draft')
        self.assertEqual(mock_call.call_count, 1)

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_persists_draft(self, mock_call):
        mock_call.return_value = _FAKE_GEMINI_RESPONSE
        before = AdCopyVariation.objects.count()
        resp = self.client.post(
            self.url,
            {
                'source_mode': 'existing',
                'project_id': self.project.id,
                'creative_id': self.creative.id,
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(AdCopyVariation.objects.count(), before + 1)
        row = AdCopyVariation.objects.latest('id')
        self.assertEqual(row.project_id, self.project.id)
        self.assertEqual(row.status, 'draft')
        self.assertEqual(str(row.batch_id), resp.data['batch_id'])

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_template_is_built_from_creative(self, mock_call):
        mock_call.return_value = _FAKE_GEMINI_RESPONSE
        self.client.post(
            self.url,
            {
                'source_mode': 'existing',
                'project_id': self.project.id,
                'creative_id': self.creative.id,
            },
            format='json',
        )
        _, user_prompt = mock_call.call_args.args
        self.assertIn('Hook line', user_prompt)
        self.assertIn('Headline A', user_prompt)
        self.assertIn('SHOP_NOW', user_prompt)


class GenerateFromCustomTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _make_user(username='gen_custom')
        cls.project = _make_project(cls.user, name='Custom Project')

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.url = reverse('ad-copy-variation-generate')

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_returns_json_shape(self, mock_call):
        mock_call.return_value = _FAKE_GEMINI_RESPONSE
        resp = self.client.post(
            self.url,
            {
                'source_mode': 'custom',
                'project_id': self.project.id,
                'base_copy': {
                    'hook': 'My hook',
                    'headline': 'My headline',
                    'description': 'My desc',
                    'cta': 'SUBSCRIBE',
                },
                'instruction': 'make it punchier',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['results'][0]['status'], 'draft')
        self.assertEqual(resp.data['results'][0]['project'], self.project.id)
        self.assertEqual(mock_call.call_count, 1)
        _, user_prompt = mock_call.call_args.args
        self.assertIn('My hook', user_prompt)
        self.assertIn('SUBSCRIBE', user_prompt)


class GenerateFromExternalUrlTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _make_user(username='gen_external')
        cls.project = _make_project(cls.user, name='External Project')

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.url = reverse('ad-copy-variation-generate')

    @patch('ad_copy_variation.services.call_aistudio_json')
    @patch('ad_copy_variation.services.fetch_url_text')
    def test_happy_path(self, mock_fetch, mock_llm):
        mock_fetch.return_value = 'rendered ad page text with hook + body + cta'
        mock_llm.return_value = _FAKE_GEMINI_RESPONSE
        resp = self.client.post(
            self.url,
            {
                'source_mode': 'external_url',
                'project_id': self.project.id,
                'url': 'https://example.com/test',
                'instruction': 'shorter',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['results'][0]['source_ref'], 'https://example.com/test')
        self.assertEqual(resp.data['results'][0]['status'], 'draft')
        self.assertEqual(mock_fetch.call_count, 1)
        self.assertEqual(mock_llm.call_count, 1)
        self.assertTrue(
            AdCopyVariation.objects.filter(
                project=self.project,
                source_mode='external_url',
                status='draft',
            ).exists()
        )

    @patch('ad_copy_variation.services.call_aistudio_json')
    @patch('ad_copy_variation.services.fetch_url_text')
    def test_missing_url(self, mock_fetch, mock_llm):
        resp = self.client.post(
            self.url,
            {'source_mode': 'external_url', 'project_id': self.project.id},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        mock_fetch.assert_not_called()
        mock_llm.assert_not_called()

    @patch('ad_copy_variation.services.call_aistudio_json')
    @patch('ad_copy_variation.services.fetch_url_text')
    def test_invalid_url_scheme(self, mock_fetch, mock_llm):
        resp = self.client.post(
            self.url,
            {
                'source_mode': 'external_url',
                'project_id': self.project.id,
                'url': 'ftp://example.com/x',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        mock_fetch.assert_not_called()
        mock_llm.assert_not_called()

    @patch('ad_copy_variation.services.call_aistudio_json')
    @patch('ad_copy_variation.services.fetch_url_text')
    def test_fetch_failure_returns_502(self, mock_fetch, mock_llm):
        mock_fetch.side_effect = RuntimeError('Browserless fetch failed: status=500')
        resp = self.client.post(
            self.url,
            {
                'source_mode': 'external_url',
                'project_id': self.project.id,
                'url': 'https://example.com/test',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(resp.data['count_succeeded'], 0)
        self.assertNotIn('error', resp.data)
        mock_llm.assert_not_called()

    @patch('ad_copy_variation.services.call_aistudio_json')
    @patch('ad_copy_variation.services.fetch_url_text')
    def test_llm_failure_returns_502(self, mock_fetch, mock_llm):
        mock_fetch.return_value = 'rendered text'
        mock_llm.side_effect = _http_error(429)
        resp = self.client.post(
            self.url,
            {
                'source_mode': 'external_url',
                'project_id': self.project.id,
                'url': 'https://example.com/test',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(resp.data['count_succeeded'], 0)
        self.assertEqual(resp.data['error'], services.AI_QUOTA_MESSAGE)


class GenerateBadInputTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _make_user(username='gen_bad')
        cls.project = _make_project(cls.user, name='Bad Input Project')

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.url = reverse('ad-copy-variation-generate')

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_missing_creative_id_for_existing(self, mock_call):
        resp = self.client.post(
            self.url,
            {'source_mode': 'existing', 'project_id': self.project.id},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        mock_call.assert_not_called()

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_unknown_source_mode(self, mock_call):
        resp = self.client.post(
            self.url,
            {'source_mode': 'banana', 'project_id': self.project.id},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        mock_call.assert_not_called()


class PermissionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _make_user(username='perm_user')
        cls.project = _make_project(cls.user, name='Permission Project')
        cls.creative = _make_creative(cls.user, project=cls.project, meta_creative_id='perm-1')
        cls.row = AdCopyVariation.objects.create(
            project=cls.project,
            creative=cls.creative,
            source_mode='existing',
            created_by=cls.user,
        )

    def test_list_unauthenticated(self):
        resp = self.client.get(reverse('ad-copy-variation-list'))
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_detail_unauthenticated(self):
        resp = self.client.get(reverse('ad-copy-variation-detail', args=[self.row.slug]))
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_create_unauthenticated(self):
        resp = self.client.post(
            reverse('ad-copy-variation-list'),
            {'source_mode': 'existing', 'creative': self.creative.id},
            format='json',
        )
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_generate_unauthenticated(self):
        resp = self.client.post(
            reverse('ad-copy-variation-generate'),
            {'source_mode': 'custom', 'base_copy': {}},
            format='json',
        )
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class GenerateBatchTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _make_user(username='gen_batch')
        cls.project = _make_project(cls.user, name='Batch Project')

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.url = reverse('ad-copy-variation-generate')

    def _custom_payload(self, **overrides):
        base = {
            'source_mode': 'custom',
            'project_id': self.project.id,
            'base_copy': {
                'hook': 'h', 'headline': 'hl',
                'description': 'd', 'cta': 'SHOP_NOW',
            },
        }
        base.update(overrides)
        return base

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_batch_custom_happy_path(self, mock_llm):
        mock_llm.return_value = _FAKE_GEMINI_RESPONSE
        resp = self.client.post(
            self.url,
            self._custom_payload(count=5),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertIn('batch_id', resp.data)
        # batch_id is uuid v4 string with 4 hyphens
        self.assertEqual(resp.data['batch_id'].count('-'), 4)
        self.assertEqual(resp.data['count_requested'], 5)
        self.assertEqual(resp.data['count_succeeded'], 5)
        self.assertEqual(resp.data['count_failed'], 0)
        self.assertEqual(len(resp.data['results']), 5)
        self.assertEqual(resp.data['failed_indices'], [])
        for r in resp.data['results']:
            self.assertEqual(r['status'], 'draft')
            self.assertEqual(r['project'], self.project.id)
        self.assertEqual(AdCopyVariation.objects.filter(project=self.project, status='draft').count(), 5)

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_batch_partial_failure(self, mock_llm):
        # Mix of success + RuntimeError; mock side_effect is consumed in call order
        # (Mock is thread-safe for side_effect popping).
        mock_llm.side_effect = [
            _FAKE_GEMINI_RESPONSE,
            RuntimeError('mocked transient'),
            _FAKE_GEMINI_RESPONSE,
            _FAKE_GEMINI_RESPONSE,
        ]
        resp = self.client.post(
            self.url,
            self._custom_payload(count=4),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(resp.data['count_requested'], 4)
        self.assertEqual(resp.data['count_succeeded'], 3)
        self.assertEqual(resp.data['count_failed'], 1)
        self.assertEqual(len(resp.data['results']), 3)
        self.assertEqual(len(resp.data['failed_indices']), 1)

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_batch_all_fail_returns_502(self, mock_llm):
        mock_llm.side_effect = RuntimeError('always fail')
        resp = self.client.post(
            self.url,
            self._custom_payload(count=3),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(resp.data['count_succeeded'], 0)
        self.assertEqual(resp.data['count_failed'], 3)
        self.assertEqual(resp.data['results'], [])

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_count_too_large_returns_400(self, mock_llm):
        resp = self.client.post(
            self.url,
            self._custom_payload(count=51),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        mock_llm.assert_not_called()

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_count_zero_returns_400(self, mock_llm):
        resp = self.client.post(
            self.url,
            self._custom_payload(count=0),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        mock_llm.assert_not_called()

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_count_non_integer_returns_400(self, mock_llm):
        resp = self.client.post(
            self.url,
            self._custom_payload(count='abc'),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        mock_llm.assert_not_called()

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_count_one_returns_persisted_batch_shape(self, mock_llm):
        mock_llm.return_value = _FAKE_GEMINI_RESPONSE
        # Send count=1 explicitly
        resp = self.client.post(
            self.url,
            self._custom_payload(count=1),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('batch_id', resp.data)
        self.assertEqual(resp.data['count_succeeded'], 1)
        self.assertEqual(len(resp.data['results']), 1)
        self.assertEqual(resp.data['results'][0]['status'], 'draft')

    @patch('ad_copy_variation.services.call_aistudio_json')
    def test_count_omitted_returns_persisted_batch_shape(self, mock_llm):
        mock_llm.return_value = _FAKE_GEMINI_RESPONSE
        # Omit count entirely
        resp = self.client.post(
            self.url,
            self._custom_payload(),
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn('batch_id', resp.data)
        self.assertEqual(resp.data['count_succeeded'], 1)
        self.assertEqual(len(resp.data['results']), 1)


class TenantRegistrationTests(SimpleTestCase):
    def test_ad_copy_variation_is_a_tenant_model(self):
        from core.tenant_config import get_tenant_models

        self.assertIn(AdCopyVariation, get_tenant_models())
