"""Link and unlink a draft's project from the draft API."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, OrganizationMembership, Project, ProjectMember
from notion_editor.models import Draft, DraftProjectLink
from rag.models import DocumentSourceType

User = get_user_model()


class DraftProjectLinkApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organization = Organization.objects.create(name='Link Org', email_domain='link.test')
        self.user = User.objects.create_user(
            username='link_user',
            email='link@test.com',
            password='pw',
            organization=self.organization,
        )
        self.outsider = User.objects.create_user(
            username='outsider',
            email='outsider@test.com',
            password='pw',
            organization=self.organization,
        )
        self.admin = User.objects.create_user(
            username='org_admin',
            email='admin@test.com',
            password='pw',
            organization=self.organization,
        )
        OrganizationMembership.objects.create(
            user=self.admin,
            organization=self.organization,
            role='admin',
            is_active=True,
        )
        self.project = Project.objects.create(
            name='Link Project',
            organization=self.organization,
            owner=self.user,
            objectives=['awareness'],
            kpis={'ctr': {'target': 0.02}},
        )
        self.other_project = Project.objects.create(
            name='Other Project',
            organization=self.organization,
            owner=self.user,
            objectives=['awareness'],
            kpis={'ctr': {'target': 0.02}},
        )
        ProjectMember.objects.create(user=self.user, project=self.project, role='owner', is_active=True)
        ProjectMember.objects.create(user=self.user, project=self.other_project, role='owner', is_active=True)
        self.draft = Draft.objects.create(title='Linked later', user=self.user, status='draft')
        self.url = reverse('notion_editor:draft-project', kwargs={'pk': self.draft.slug})

    def test_detail_reports_linked_project(self):
        self.client.force_authenticate(user=self.user)
        detail_url = reverse('notion_editor:draft-detail', kwargs={'pk': self.draft.slug})

        unlinked = self.client.get(detail_url)
        self.assertEqual(unlinked.status_code, status.HTTP_200_OK)
        self.assertIsNone(unlinked.data['project_id'])
        self.assertIsNone(unlinked.data['project_slug'])

        DraftProjectLink.objects.create(draft=self.draft, project=self.project)
        linked = self.client.get(detail_url)
        self.assertEqual(linked.status_code, status.HTTP_200_OK)
        self.assertEqual(linked.data['project_id'], self.project.id)
        self.assertEqual(linked.data['project_slug'], self.project.slug)

    def test_member_can_link_and_enqueue_index(self):
        self.client.force_authenticate(user=self.user)
        with patch('rag.tasks.index_document_task.delay') as mock_delay:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(self.url, {'project_id': self.project.id}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['project_id'], self.project.id)
        self.assertEqual(response.data['project_slug'], self.project.slug)
        self.assertTrue(
            DraftProjectLink.objects.filter(draft=self.draft, project=self.project).exists()
        )
        mock_delay.assert_called_once()
        self.assertEqual(mock_delay.call_args.kwargs['project_id'], self.project.id)
        self.assertEqual(mock_delay.call_args.kwargs['source_type'], DocumentSourceType.NOTION_DRAFT)
        self.assertEqual(mock_delay.call_args.kwargs['source_id'], str(self.draft.id))

    def test_org_admin_can_link_without_membership(self):
        draft = Draft.objects.create(title='Admin draft', user=self.admin, status='draft')
        url = reverse('notion_editor:draft-project', kwargs={'pk': draft.slug})
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(url, {'project_id': self.project.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(DraftProjectLink.objects.get(draft=draft).project_id, self.project.id)
        self.assertFalse(
            ProjectMember.objects.filter(user=self.admin, project=self.project, is_active=True).exists()
        )

    def test_non_member_is_rejected(self):
        draft = Draft.objects.create(title='Outsider draft', user=self.outsider, status='draft')
        url = reverse('notion_editor:draft-project', kwargs={'pk': draft.slug})
        self.client.force_authenticate(user=self.outsider)
        response = self.client.post(url, {'project_id': self.project.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(DraftProjectLink.objects.filter(draft=draft).exists())

    def test_other_users_draft_is_hidden(self):
        self.client.force_authenticate(user=self.outsider)
        other_draft = Draft.objects.create(title='Not mine', user=self.user, status='draft')
        url = reverse('notion_editor:draft-project', kwargs={'pk': other_draft.slug})
        ProjectMember.objects.create(
            user=self.outsider, project=self.project, role='member', is_active=True,
        )
        response = self.client.post(url, {'project_id': self.project.id}, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_move_clears_old_project_and_enqueues_both(self):
        DraftProjectLink.objects.create(draft=self.draft, project=self.project)
        self.client.force_authenticate(user=self.user)
        with patch('rag.tasks.index_document_task.delay') as mock_delay:
            with patch('rag.indexing.clear_source_index') as mock_clear:
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(
                        self.url, {'project_id': self.other_project.id}, format='json',
                    )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            DraftProjectLink.objects.get(draft=self.draft).project_id,
            self.other_project.id,
        )
        mock_clear.assert_called_once_with(self.project.id, DocumentSourceType.NOTION_DRAFT, str(self.draft.id))
        enqueued_projects = [call.kwargs['project_id'] for call in mock_delay.call_args_list]
        self.assertEqual(enqueued_projects, [self.project.id, self.other_project.id])

    def test_unlink_clears_index(self):
        DraftProjectLink.objects.create(draft=self.draft, project=self.project)
        self.client.force_authenticate(user=self.user)
        with patch('rag.indexing.clear_source_index') as mock_clear:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(DraftProjectLink.objects.filter(draft=self.draft).exists())
        mock_clear.assert_called_once_with(self.project.id, DocumentSourceType.NOTION_DRAFT, str(self.draft.id))
        self.assertTrue(Draft.objects.filter(pk=self.draft.pk).exists())
