from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, OrganizationMembership
from org_customization.models import TaskStatusConfig, TaskTypeConfig
from task.models import Task

User = get_user_model()

TASK_CONFIG_URL = reverse('org-customization-task-config')
TASK_TYPES_URL = reverse('task-types')


def make_user(email, organization, role='member'):
    user = User.objects.create_user(
        username=email.split('@')[0],
        email=email,
        password='testpass123',
        current_organization=organization,
    )
    OrganizationMembership.objects.create(user=user, organization=organization, role=role, is_active=True)
    return user


class TaskConfigViewTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Config Org')
        self.admin = make_user('admin@example.com', self.org, role='admin')
        self.member = make_user('member@example.com', self.org)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_get_returns_defaults_when_no_rows(self):
        response = self.client.get(TASK_CONFIG_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task_types = response.data['task_types']
        self.assertEqual([t['key'] for t in task_types], [c[0] for c in Task._meta.get_field('type').choices])
        self.assertTrue(all(t['enabled'] and t['label'] is None for t in task_types))
        self.assertEqual([s['key'] for s in response.data['task_statuses']], list(Task.Status.values))

    def test_put_saves_rows(self):
        response = self.client.put(TASK_CONFIG_URL, {
            'task_types': [{'key': 'budget', 'label': 'Spend Request', 'enabled': False, 'order': 3}],
            'task_statuses': [{'key': 'UNDER_REVIEW', 'label': 'In QA', 'color': '#F59E0B', 'order': 1}],
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task_type = TaskTypeConfig.objects.get(organization=self.org, key='budget')
        self.assertEqual((task_type.label, task_type.enabled, task_type.order), ('Spend Request', False, 3))
        task_status = TaskStatusConfig.objects.get(organization=self.org, key='UNDER_REVIEW')
        self.assertEqual((task_status.label, task_status.color, task_status.order), ('In QA', '#F59E0B', 1))

    def test_put_updates_existing_row(self):
        TaskTypeConfig.objects.create(organization=self.org, key='asset', label='Old', order=1)

        self.client.put(TASK_CONFIG_URL, {
            'task_types': [{'key': 'asset', 'label': '', 'enabled': True, 'order': 0}],
        }, format='json')

        self.assertEqual(TaskTypeConfig.objects.filter(organization=self.org, key='asset').count(), 1)
        self.assertIsNone(TaskTypeConfig.objects.get(organization=self.org, key='asset').label)

    def test_put_rejects_unknown_key_and_bad_color(self):
        response = self.client.put(TASK_CONFIG_URL, {
            'task_types': [{'key': 'not_a_type'}],
            'task_statuses': [{'key': 'DRAFT', 'color': 'red'}],
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(TaskTypeConfig.objects.exists())

    def test_non_admin_is_forbidden(self):
        self.client.force_authenticate(self.member)

        self.assertEqual(self.client.get(TASK_CONFIG_URL).status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.put(TASK_CONFIG_URL, {'task_types': [{'key': 'budget', 'enabled': False}]}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(TaskTypeConfig.objects.exists())


class TaskTypesEndpointTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Types Org')
        self.user = make_user('user@example.com', self.org)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_returns_all_types_without_config(self):
        response = self.client.get(TASK_TYPES_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data['task_types'],
            [{'value': value, 'label': label} for value, label in Task._meta.get_field('type').choices],
        )

    def test_applies_org_config(self):
        TaskTypeConfig.objects.create(organization=self.org, key='budget', enabled=False)
        TaskTypeConfig.objects.create(organization=self.org, key='report', label='Weekly Report', order=0)
        TaskTypeConfig.objects.create(organization=self.org, key='asset', order=50)

        task_types = self.client.get(TASK_TYPES_URL).data['task_types']
        values = [t['value'] for t in task_types]

        self.assertNotIn('budget', values)
        self.assertEqual(task_types[0], {'value': 'report', 'label': 'Weekly Report'})
        self.assertEqual(values[-1], 'asset')

    def test_other_org_config_is_ignored(self):
        other_org = Organization.objects.create(name='Other Org')
        TaskTypeConfig.objects.create(organization=other_org, key='budget', enabled=False)

        values = [t['value'] for t in self.client.get(TASK_TYPES_URL).data['task_types']]

        self.assertIn('budget', values)
