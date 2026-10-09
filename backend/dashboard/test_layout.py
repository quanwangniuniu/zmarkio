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
        self.assertEqual(self.client.put(self.url, {'widgets': widgets}, format='json').status_code, 200)
        saved_widget = DashboardLayout.objects.get(project=self.project, user=self.owner)
        self.assertEqual((saved_widget.widget_id, saved_widget.x, saved_widget.y, saved_widget.w, saved_widget.h),
                         ('meetings', 0, 0, 6, 8))

        self.client.force_authenticate(user=self.member)
        self.assertEqual(self.client.get(self.url).data['widgets'], DEFAULT_WIDGETS)
        self.client.force_authenticate(user=self.owner)
        other_url = f'/api/dashboard/layout/?project_id={self.second_project.pk}'
        self.assertEqual(self.client.get(other_url).data['widgets'], DEFAULT_WIDGETS)

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
            [{'id': 'meetings', 'x': 0, 'y': 0, 'w': 6, 'h': 2}],
        ]
        for widgets in invalid_layouts:
            with self.subTest(widgets=widgets):
                response = self.client.put(self.url, {'widgets': widgets}, format='json')
                self.assertEqual(response.status_code, 400)
        self.assertFalse(DashboardLayout.objects.filter(project=self.project, user=self.owner).exists())
