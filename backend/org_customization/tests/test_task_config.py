from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, OrganizationMembership, Project, ProjectMember
from meetings.models import Meeting, MeetingActionItem, MeetingTypeDefinition
from org_customization.models import TaskFieldOptionConfig, TaskPriorityConfig, TaskStatusConfig, TaskTypeConfig
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
        self.assertEqual(response.data['task_statuses'][0]['default_category'], 'TO_DO')
        self.assertEqual([p['key'] for p in response.data['task_priorities']], list(Task.Priority.values))
        self.assertEqual(response.data['task_field_options'], [])
        self.assertIn({'key': 'DONE', 'label': 'Done'}, response.data['status_categories'])

    def test_put_saves_rows(self):
        response = self.client.put(TASK_CONFIG_URL, {
            'task_types': [{
                'key': 'budget', 'label': 'Spend Request', 'short_label': 'Spend',
                'color': '#3CCED7', 'enabled': False, 'order': 3,
            }],
            'task_statuses': [{'key': 'UNDER_REVIEW', 'label': 'In QA', 'category': 'DONE'}],
            'task_priorities': [{'key': 'HIGHEST', 'label': 'Urgent'}],
            'task_field_options': [{'task_type': 'alert', 'field': 'severity', 'value': 'low', 'label': 'Minor', 'enabled': False}],
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        task_type = TaskTypeConfig.objects.get(organization=self.org, key='budget')
        self.assertEqual(
            (task_type.label, task_type.short_label, task_type.color, task_type.enabled, task_type.order),
            ('Spend Request', 'Spend', '#3CCED7', False, 3),
        )
        task_status = TaskStatusConfig.objects.get(organization=self.org, key='UNDER_REVIEW')
        self.assertEqual((task_status.label, task_status.category), ('In QA', 'DONE'))
        self.assertEqual(TaskPriorityConfig.objects.get(organization=self.org, key='HIGHEST').label, 'Urgent')
        option = TaskFieldOptionConfig.objects.get(organization=self.org, task_type='alert', field='severity', value='low')
        self.assertEqual((option.label, option.enabled), ('Minor', False))
        self.assertEqual(response.data['task_field_options'][0]['label'], 'Minor')

    def test_put_updates_existing_row(self):
        TaskTypeConfig.objects.create(organization=self.org, key='asset', label='Old', order=1)

        self.client.put(TASK_CONFIG_URL, {
            'task_types': [{'key': 'asset', 'label': '', 'enabled': True, 'order': 0}],
        }, format='json')

        self.assertEqual(TaskTypeConfig.objects.filter(organization=self.org, key='asset').count(), 1)
        self.assertIsNone(TaskTypeConfig.objects.get(organization=self.org, key='asset').label)

    def test_put_rejects_unknown_key_and_bad_color(self):
        response = self.client.put(TASK_CONFIG_URL, {
            'task_types': [{'key': 'not_a_type'}, {'key': 'asset', 'color': 'red'}],
            'task_statuses': [{'key': 'DRAFT', 'category': 'NOT_A_CATEGORY'}],
            'task_priorities': [{'key': 'URGENT'}],
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(TaskTypeConfig.objects.exists())

    def test_member_can_read_but_not_save(self):
        self.client.force_authenticate(self.member)

        self.assertEqual(self.client.get(TASK_CONFIG_URL).status_code, status.HTTP_200_OK)
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


class DisabledTaskTypeTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Enforce Org')
        self.user = make_user('creator@example.com', self.org)
        self.project = Project.objects.create(name='Enforce Project', organization=self.org, owner=self.user)
        ProjectMember.objects.create(user=self.user, project=self.project, is_active=True)
        self.user.active_project = self.project
        self.user.save(update_fields=['active_project'])
        TaskTypeConfig.objects.create(organization=self.org, key='budget', enabled=False)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _create_draft(self, task_type):
        return self.client.post(reverse('task-list'), {
            'summary': 'Draft',
            'type': task_type,
            'project_id': self.project.id,
            'create_as_draft': True,
        }, format='json')

    def test_create_rejects_disabled_type(self):
        response = self._create_draft('budget')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('type', response.data)
        self.assertFalse(Task.objects.filter(type='budget').exists())

    def test_create_allows_enabled_type(self):
        self.assertEqual(self._create_draft('asset').status_code, status.HTTP_201_CREATED)

    def test_meeting_conversion_rejects_disabled_type(self):
        meeting = Meeting.objects.create(
            project=self.project,
            title='Review',
            type_definition=MeetingTypeDefinition.objects.create(project=self.project, slug='planning', label='Planning'),
            objective='actions',
        )
        item = MeetingActionItem.objects.create(meeting=meeting, title='Ship budget', order_index=0)

        response = self.client.post(
            f'/api/projects/{self.project.slug}/meetings/{meeting.slug}/action-items/{item.id}/convert-to-task/',
            {'type': 'budget', 'create_as_draft': True},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Task.objects.filter(origin_action_item=item).exists())


class DashboardStatusCategoryTest(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Dashboard Org')
        self.user = make_user('dash@example.com', self.org)
        self.project = Project.objects.create(name='Dashboard Project', organization=self.org, owner=self.user)
        ProjectMember.objects.create(user=self.user, project=self.project, is_active=True)
        Task.objects.create(summary='Rejected', type='asset', project=self.project, owner=self.user)
        Task.objects.filter(summary='Rejected').update(status=Task.Status.REJECTED)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _breakdown(self):
        response = self.client.get(reverse('dashboard:dashboard-summary'), {'project_id': self.project.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {item['status']: item['count'] for item in response.data['status_overview']['breakdown']}

    def test_uses_default_categories(self):
        self.assertEqual(self._breakdown(), {'RESEARCH': 1})

    def test_uses_configured_category(self):
        TaskStatusConfig.objects.create(organization=self.org, key=Task.Status.REJECTED, category='DONE')

        self.assertEqual(self._breakdown(), {'DONE': 1})
