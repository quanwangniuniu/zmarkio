from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from audit.models import AdminAuditEvent
from core.models import Organization, ProjectMember
from org_customization.models import OptionConfig, TaskTypeConfig
from org_customization.tests.test_task_config import make_project, make_user

SYSTEM_CONFIG_PATH = reverse('org-customization-system-config')
TASK_CONFIG_PATH = reverse('org-customization-task-config')
NAVIGATION_CHANGE = {'navigation': {'options': [{'field': 'manage', 'value': 'meta_ads', 'enabled': False, 'order': 1}]}}


def navigation_item(data, value):
    return next(o for o in data['navigation']['options'] if o['value'] == value)


class ConfigAuditCachePermissionTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Audit Org')
        self.admin = make_user('admin@example.com', self.org, role='admin')
        self.owner = make_user('owner@example.com', self.org)
        self.project_admin = make_user('lead@example.com', self.org)
        self.project = make_project('Audit Project', self.org, self.owner)
        ProjectMember.objects.create(user=self.project_admin, project=self.project, role='Super Administrator', is_active=True)
        self.url = f'{SYSTEM_CONFIG_PATH}?project_id={self.project.id}'
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_save_records_only_changed_items(self):
        response = self.client.put(self.url, NAVIGATION_CHANGE, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        event = AdminAuditEvent.objects.get(action='project.config_updated')
        self.assertEqual((event.actor, event.organization, event.project_id), (self.admin, self.org, self.project.id))
        self.assertEqual((event.target_type, event.target_id), ('Project', str(self.project.id)))
        key = 'navigation · options · manage · meta_ads'
        self.assertEqual(event.before, {key: {'enabled': True}})
        self.assertEqual(event.after, {key: {'enabled': False}})

    def test_full_page_save_records_only_the_changed_item(self):
        config = self.client.get(self.url).data
        navigation_item(config, 'meta_ads')['enabled'] = False
        payload = {
            'task': {key: config['task'][key] for key in ('task_types', 'task_statuses', 'task_priorities', 'task_field_options')},
            **{module: {'options': config[module]['options']} for module in ('csm', 'decisions', 'campaigns', 'navigation')},
        }

        response = self.client.put(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        event = AdminAuditEvent.objects.get(action='project.config_updated')
        self.assertEqual(event.after, {'navigation · options · manage · meta_ads': {'enabled': False}})

    def test_task_config_save_is_audited(self):
        response = self.client.put(
            f'{TASK_CONFIG_PATH}?project_id={self.project.id}',
            {'task_types': [{'key': 'budget', 'label': 'Spend', 'enabled': False}]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = AdminAuditEvent.objects.get(action='project.config_updated')
        self.assertEqual(event.before, {'task_types · budget': {'label': 'Budget', 'enabled': True}})
        self.assertEqual(event.after, {'task_types · budget': {'label': 'Spend', 'enabled': False}})

    def test_rejected_save_is_not_audited(self):
        self.client.force_authenticate(self.project_admin)

        self.client.put(self.url, NAVIGATION_CHANGE, format='json')

        self.assertFalse(AdminAuditEvent.objects.exists())

    def test_reads_are_cached_until_a_save(self):
        self.client.get(self.url)
        OptionConfig.objects.create(
            organization=self.org, project_id=self.project.id, module='navigation', field='manage', value='tasks', label='Jobs',
        )

        self.assertIsNone(navigation_item(self.client.get(self.url).data, 'tasks')['label'])

        self.client.put(self.url, NAVIGATION_CHANGE, format='json')

        data = self.client.get(self.url).data
        self.assertFalse(navigation_item(data, 'meta_ads')['enabled'])
        self.assertIsNone(navigation_item(data, 'tasks')['label'])

    def test_task_config_save_clears_cache(self):
        self.client.get(self.url)

        self.client.put(
            f'{TASK_CONFIG_PATH}?project_id={self.project.id}',
            {'task_types': [{'key': 'budget', 'label': 'Spend'}]},
            format='json',
        )

        task_type = next(t for t in self.client.get(self.url).data['task']['task_types'] if t['key'] == 'budget')
        self.assertEqual(task_type['label'], 'Spend')

    def test_cached_read_skips_config_queries(self):
        self.client.get(self.url)

        with self.assertNumQueries(4):
            self.client.get(self.url)

    def test_project_admin_role_can_read_but_not_edit(self):
        self.client.force_authenticate(self.project_admin)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['can_edit'])
        self.assertEqual(self.client.put(self.url, NAVIGATION_CHANGE, format='json').status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(OptionConfig.objects.exists())

    def test_owner_and_org_admin_can_edit(self):
        for user in (self.owner, self.admin):
            self.client.force_authenticate(user)
            response = self.client.put(self.url, NAVIGATION_CHANGE, format='json')
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertTrue(response.data['can_edit'])

    def test_admin_of_another_org_cannot_edit(self):
        other_admin = make_user('other-admin@example.com', Organization.objects.create(name='Other Org'), role='admin')
        ProjectMember.objects.create(user=other_admin, project=self.project, is_active=True)
        self.client.force_authenticate(other_admin)

        self.assertEqual(self.client.put(self.url, NAVIGATION_CHANGE, format='json').status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(TaskTypeConfig.objects.get(project_id=self.project.id, key='budget').enabled)
