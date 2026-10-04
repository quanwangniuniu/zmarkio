from django.contrib.auth import get_user_model
from django.test import TestCase
from unittest.mock import patch
from rest_framework.test import APIClient

from core.models import Organization, Project, ProjectMember
from core.tenant_config import get_tenant_models
from dashboard.layout import DEFAULT_WIDGETS, WORKSPACE_WIDGET_IDS
from dashboard.models import DashboardLayout


class DashboardLayoutTest(TestCase):
    def test_layout_model_is_provisioned_in_tenant_schemas(self):
        self.assertIn(DashboardLayout, get_tenant_models())

    def setUp(self):
        tracking = patch('tracking.middleware.emit_tracking_event.delay')
        tracking.start()
        self.addCleanup(tracking.stop)
        self.org = Organization.objects.create(name='Layout test org')
        User = get_user_model()
        self.owner = User.objects.create_user(username='layout-owner', email='layout-owner@test.invalid', password='testpass123')
        self.member = User.objects.create_user(username='layout-member', email='layout-member@test.invalid', password='testpass123')
        self.stranger = User.objects.create_user(username='layout-stranger', email='layout-stranger@test.invalid', password='testpass123')
        self.project = Project.objects.create(name='Layout project', owner=self.owner, organization=self.org)
        self.other_project = Project.objects.create(name='Other layout project', owner=self.owner, organization=self.org)
        ProjectMember.objects.create(project=self.project, user=self.member, is_active=True)
        self.client = APIClient()

    def url(self, project=None):
        return f'/api/dashboard/layout/?project_id={(project or self.project).pk}'

    def test_default_and_persistence_are_per_user_and_project(self):
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.get(self.url()).data['widgets'], DEFAULT_WIDGETS)
        edited = [{'id': 'audit', 'x': 3, 'y': 2, 'w': 4, 'h': 6}]
        self.assertEqual(self.client.put(self.url(), {'widgets': edited}, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url()).data['widgets'], edited)
        self.assertEqual(self.client.get(self.url(self.other_project)).data['widgets'], DEFAULT_WIDGETS)
        self.client.force_authenticate(user=self.member)
        self.assertEqual(self.client.get(self.url()).data['widgets'], DEFAULT_WIDGETS)
        self.assertEqual(self.client.put(self.url(), {'widgets': []}, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url()).data['widgets'], [])
        self.assertEqual(DashboardLayout.objects.count(), 2)

    def test_legacy_workspace_expands_without_losing_other_positions(self):
        self.client.force_authenticate(user=self.owner)
        DashboardLayout.objects.create(
            project=self.project, user=self.owner,
            widgets=[
                {'id': 'workspace', 'x': 0, 'y': 0, 'w': 12, 'h': 12},
                {'id': 'audit', 'x': 0, 'y': 12, 'w': 6, 'h': 8},
                {'id': 'activity', 'x': 6, 'y': 12, 'w': 6, 'h': 8},
            ],
        )
        widgets = self.client.get(self.url()).data['widgets']
        self.assertEqual({widget['id'] for widget in widgets}, WORKSPACE_WIDGET_IDS | {'audit', 'activity'})
        self.assertEqual(next(widget for widget in widgets if widget['id'] == 'audit')['y'], 35)
        self.assertEqual(next(widget for widget in widgets if widget['id'] == 'activity')['x'], 6)
        self.assertEqual(self.client.put(self.url(), {'widgets': widgets}, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url()).data['widgets'], widgets)

    def test_membership_and_organization_boundary(self):
        self.assertEqual(self.client.get(self.url()).status_code, 401)
        self.client.force_authenticate(user=self.stranger)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.client.put(self.url(), {'widgets': []}, format='json').status_code, 404)
        self.client.force_authenticate(user=self.member)
        self.assertEqual(self.client.get(self.url(self.other_project)).status_code, 404)
        ProjectMember.objects.filter(user=self.member, project=self.project).update(is_active=False)
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_rejects_invalid_positions_duplicates_and_overlaps(self):
        self.client.force_authenticate(user=self.owner)
        invalid = [
            [{'id': 'audit', 'x': 10, 'y': 0, 'w': 6, 'h': 4}],
            [{'id': 'not-registered', 'x': 0, 'y': 0, 'w': 6, 'h': 4}],
            [{'id': 'audit', 'x': 0, 'y': 0, 'w': 6, 'h': 4}] * 2,
            [
                {'id': 'audit', 'x': 0, 'y': 0, 'w': 6, 'h': 4},
                {'id': 'activity', 'x': 3, 'y': 1, 'w': 6, 'h': 4},
            ],
        ]
        for widgets in invalid:
            with self.subTest(widgets=widgets):
                self.assertEqual(self.client.put(self.url(), {'widgets': widgets}, format='json').status_code, 400)
        self.assertFalse(DashboardLayout.objects.exists())
