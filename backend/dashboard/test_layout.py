from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Organization, Project, ProjectMember
from dashboard.models import DashboardLayout
from dashboard.widget_catalog import DEFAULT_WIDGETS


User = get_user_model()


class DashboardLayoutAPITest(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name='Dashboard test organization')
        self.owner = User.objects.create_user(username='dashboard-owner', email='owner@example.test', password='password')
        self.member = User.objects.create_user(username='dashboard-member', email='member@example.test', password='password')
        self.stranger = User.objects.create_user(username='dashboard-stranger', email='stranger@example.test', password='password')
        self.project = Project.objects.create(name='Dashboard project', owner=self.owner, organization=organization)
        self.second_project = Project.objects.create(name='Other project', owner=self.owner, organization=organization)
        ProjectMember.objects.create(project=self.project, user=self.owner, role='owner')
        ProjectMember.objects.create(project=self.second_project, user=self.owner, role='owner')
        ProjectMember.objects.create(project=self.project, user=self.member)
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)
        self.url = f'/api/dashboard/layout/?project_id={self.project.pk}'

    def test_default_layout_and_empty_layout_is_rejected(self):
        initial = self.client.get(self.url)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.data['widgets'], DEFAULT_WIDGETS)

        saved = self.client.put(self.url, {'widgets': []}, format='json')
        self.assertEqual(saved.status_code, 400)
        self.assertEqual(self.client.get(self.url).data['widgets'], DEFAULT_WIDGETS)

    def test_layout_is_scoped_to_user_and_project(self):
        widgets = [{'id': 'meetings', 'x': 0, 'y': 0, 'w': 6, 'h': 8}]
        response = self.client.put(self.url, {'widgets': widgets}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['widgets'], widgets)
        self.assertIsNotNone(response.data['updated_at'])
        saved_widget = DashboardLayout.objects.get(project=self.project, user=self.owner)
        self.assertEqual((saved_widget.widget_id, saved_widget.x, saved_widget.y, saved_widget.w, saved_widget.h),
                         ('meetings', 0, 0, 6, 8))

        self.client.force_authenticate(user=self.member)
        self.assertEqual(self.client.get(self.url).data['widgets'], DEFAULT_WIDGETS)
        self.client.force_authenticate(user=self.owner)
        other_url = f'/api/dashboard/layout/?project_id={self.second_project.pk}'
        self.assertEqual(self.client.get(other_url).data['widgets'], DEFAULT_WIDGETS)

    def test_repeated_put_updates_the_existing_widget_row(self):
        first = [{'id': 'meetings', 'x': 0, 'y': 0, 'w': 6, 'h': 8}]
        second = [{'id': 'meetings', 'x': 6, 'y': 4, 'w': 6, 'h': 10}]

        self.assertEqual(self.client.put(self.url, {'widgets': first}, format='json').status_code, 200)
        first_row = DashboardLayout.objects.get(project=self.project, user=self.owner, widget_id='meetings')
        response = self.client.put(self.url, {'widgets': second}, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['widgets'], second)
        rows = DashboardLayout.objects.filter(project=self.project, user=self.owner, widget_id='meetings')
        self.assertEqual(rows.count(), 1)
        updated_row = rows.get()
        self.assertEqual(updated_row.pk, first_row.pk)
        self.assertEqual((updated_row.x, updated_row.y, updated_row.w, updated_row.h), (6, 4, 6, 10))
        self.assertGreater(updated_row.updated_at, first_row.updated_at)

    def test_new_cards_keep_previous_layout_records(self):
        DashboardLayout.objects.create(
            project=self.project, user=self.owner, widget_id='workspace',
            x=0, y=66, w=12, h=40,
        )
        existing = DashboardLayout.objects.create(
            project=self.project, user=self.owner, widget_id='meetings',
            x=0, y=48, w=6, h=8,
        )

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        widgets = response.data['widgets']
        self.assertNotIn('workspace', {widget['id'] for widget in widgets})
        self.assertEqual(len(widgets), len(DEFAULT_WIDGETS))
        self.assertIn({'id': 'meetings', 'x': 0, 'y': 48, 'w': 6, 'h': 8}, widgets)

        saved = self.client.put(self.url, {'widgets': widgets}, format='json')
        self.assertEqual(saved.status_code, 200)
        self.assertTrue(DashboardLayout.objects.filter(pk=existing.pk).exists())
        self.assertTrue(DashboardLayout.objects.filter(
            project=self.project, user=self.owner, widget_id='workspace',
        ).exists())

    def test_inaccessible_project_is_not_exposed(self):
        self.client.force_authenticate(user=self.stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.put(self.url, {'widgets': []}, format='json').status_code, 404)

    def test_rejects_invalid_layout_without_saving(self):
        invalid_layouts = [
            [{'id': 'unknown', 'x': 0, 'y': 0, 'w': 6, 'h': 8}],
            [{'id': 'meetings', 'x': 0, 'y': 0, 'w': 6, 'h': 8},
             {'id': 'meetings', 'x': 6, 'y': 0, 'w': 6, 'h': 8}],
            [{'id': 'meetings', 'x': 0, 'y': 0, 'w': 6, 'h': 8},
             {'id': 'activity', 'x': 3, 'y': 0, 'w': 6, 'h': 8}],
            [{'id': 'meetings', 'x': 11, 'y': 0, 'w': 6, 'h': 8}],
            [{'id': 'meetings', 'x': 0, 'y': 0, 'w': 2, 'h': 8}],
            [{'id': 'meetings', 'x': 0, 'y': 0, 'w': 6, 'h': 2}],
        ]
        for widgets in invalid_layouts:
            with self.subTest(widgets=widgets):
                response = self.client.put(self.url, {'widgets': widgets}, format='json')
                self.assertEqual(response.status_code, 400)
        self.assertFalse(DashboardLayout.objects.filter(project=self.project, user=self.owner).exists())
