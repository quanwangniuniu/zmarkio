from django.contrib.auth import get_user_model
from django.test import TestCase
from unittest.mock import patch
from rest_framework.test import APIClient

from core.models import Organization, Project, ProjectMember
from core.tenant_config import get_tenant_models
from dashboard.layout import DEFAULT_WIDGETS
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
        initial = self.client.get(self.url())
        self.assertEqual(initial.data['widgets'], DEFAULT_WIDGETS)
        self.assertEqual(initial.data['project_id'], self.project.pk)
        self.assertEqual(initial.data['project_slug'], self.project.slug)
        self.assertEqual(initial['Cache-Control'], 'private, no-store')
        edited = [{'id': 'audit', 'x': 3, 'y': 2, 'w': 4, 'h': 6}]
        self.assertEqual(self.client.put(self.url(), {'widgets': edited}, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url()).data['widgets'], edited)
        self.assertEqual(self.client.get(self.url(self.other_project)).data['widgets'], DEFAULT_WIDGETS)
        other_edited = [{'id': 'activity', 'x': 0, 'y': 0, 'w': 6, 'h': 5}]
        self.assertEqual(self.client.put(self.url(self.other_project), {'widgets': other_edited}, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url()).data['widgets'], edited)
        self.assertEqual(self.client.get(self.url(self.other_project)).data['widgets'], other_edited)
        self.client.force_authenticate(user=self.member)
        self.assertEqual(self.client.get(self.url()).data['widgets'], DEFAULT_WIDGETS)
        self.assertEqual(self.client.put(self.url(), {'widgets': []}, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url()).data['widgets'], [])
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.get(self.url()).data['widgets'], edited)
        self.assertEqual(self.client.get(self.url(self.other_project)).data['widgets'], other_edited)
        self.assertEqual(DashboardLayout.objects.count(), 3)

    def test_reads_saved_group_document_without_mutating_it(self):
        self.client.force_authenticate(user=self.owner)
        document = {
            'version': 3,
            'items': [
                {'kind': 'group', 'id': 'group-older', 'title': 'Tasks', 'x': 0, 'y': 7, 'w': 12, 'h': 8,
                 'children': [
                     {'kind': 'widget', 'id': 'task-priority', 'x': 0, 'y': 0, 'w': 6, 'h': 4},
                     {'kind': 'widget', 'id': 'task-types', 'x': 6, 'y': 0, 'w': 6, 'h': 4},
                 ]},
                {'kind': 'widget', 'id': 'audit', 'x': 0, 'y': 15, 'w': 6, 'h': 4},
            ],
        }
        DashboardLayout.objects.create(project=self.project, user=self.owner, widgets=document)
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['widgets'], [
            {'id': 'section-title-group-older', 'title': 'Tasks', 'x': 0, 'y': 7, 'w': 12, 'h': 1},
            {'id': 'task-priority', 'x': 0, 'y': 8, 'w': 6, 'h': 4},
            {'id': 'task-types', 'x': 6, 'y': 8, 'w': 6, 'h': 4},
            {'id': 'audit', 'x': 0, 'y': 15, 'w': 6, 'h': 4},
        ])
        self.assertEqual(DashboardLayout.objects.get(project=self.project, user=self.owner).widgets, document)
        # The converted layout must be accepted when the user next saves it.
        self.assertEqual(self.client.put(self.url(), {'widgets': response.data['widgets']}, format='json').status_code, 200)

    def test_group_titles_push_following_widgets_down_instead_of_overlapping(self):
        self.client.force_authenticate(user=self.owner)
        document = {
            'version': 3,
            'items': [
                {'kind': 'group', 'id': 'group one!', 'title': '  Charts  ', 'x': 0, 'y': 0, 'w': 12, 'h': 4,
                 'children': [{'kind': 'widget', 'id': 'task-status', 'x': 0, 'y': 0, 'w': 6, 'h': 4}]},
                {'kind': 'group', 'id': 'untitled', 'title': ' ', 'x': 0, 'y': 4, 'w': 12, 'h': 4,
                 'children': [{'kind': 'widget', 'id': 'audit', 'x': 0, 'y': 0, 'w': 6, 'h': 4}]},
                {'kind': 'widget', 'id': 'activity', 'x': 6, 'y': 4, 'w': 6, 'h': 4},
            ],
        }
        DashboardLayout.objects.create(project=self.project, user=self.owner, widgets=document)
        widgets = self.client.get(self.url()).data['widgets']
        self.assertEqual(widgets, [
            {'id': 'section-title-groupone', 'title': 'Charts', 'x': 0, 'y': 0, 'w': 12, 'h': 1},
            {'id': 'task-status', 'x': 0, 'y': 1, 'w': 6, 'h': 4},
            {'id': 'activity', 'x': 6, 'y': 4, 'w': 6, 'h': 4},
            {'id': 'audit', 'x': 0, 'y': 5, 'w': 6, 'h': 4},
        ])
        self.assertEqual(self.client.put(self.url(), {'widgets': widgets}, format='json').status_code, 200)

    def test_multiple_section_titles_are_editable_and_scoped_to_user_and_project(self):
        self.client.force_authenticate(user=self.owner)
        widgets = [
            {'id': 'section-title-first', 'title': 'Project overview', 'x': 0, 'y': 0, 'w': 12, 'h': 1},
            {'id': 'audit', 'x': 0, 'y': 1, 'w': 6, 'h': 4},
            {'id': 'section-title-second', 'title': 'Recent updates', 'x': 0, 'y': 5, 'w': 12, 'h': 1},
        ]
        self.assertEqual(self.client.put(self.url(), {'widgets': widgets}, format='json').status_code, 200)
        widgets[0]['title'] = 'Updated overview'
        widgets[0]['w'] = 8
        self.assertEqual(self.client.put(self.url(), {'widgets': widgets}, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url()).data['widgets'], widgets)
        self.assertEqual(self.client.get(self.url(self.other_project)).data['widgets'], DEFAULT_WIDGETS)
        self.client.force_authenticate(user=self.member)
        self.assertEqual(self.client.get(self.url()).data['widgets'], DEFAULT_WIDGETS)
        self.client.force_authenticate(user=self.owner)
        widgets.pop(0)
        self.assertEqual(self.client.put(self.url(), {'widgets': widgets}, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url()).data['widgets'], widgets)

    def test_rejects_invalid_section_titles_and_preserves_normal_widget_minimum_height(self):
        self.client.force_authenticate(user=self.owner)
        title = {'id': 'section-title-first', 'title': 'Overview', 'x': 0, 'y': 0, 'w': 12, 'h': 1}
        invalid = [
            {**title, 'title': ''}, {**title, 'title': ' '}, {**title, 'title': 'x' * 81},
            {key: value for key, value in title.items() if key != 'title'},
            {**title, 'id': 'section-title-'}, {**title, 'h': 0},
            {'id': 'audit', 'x': 0, 'y': 0, 'w': 6, 'h': 1},
            {**title, 'id': 'audit', 'h': 4},
        ]
        for widget in invalid:
            with self.subTest(widget=widget):
                self.assertEqual(self.client.put(self.url(), {'widgets': [widget]}, format='json').status_code, 400)
        self.assertFalse(DashboardLayout.objects.exists())

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

    def test_fractional_resize_dimensions_round_trip_without_rounding(self):
        self.client.force_authenticate(user=self.owner)
        widgets = [
            {'id': 'audit', 'x': 0, 'y': 0, 'w': 6.125, 'h': 5 + 10 / 60},
            {'id': 'activity', 'x': 6, 'y': 5 + 10 / 60, 'w': 6, 'h': 5},
        ]
        self.assertEqual(self.client.put(self.url(), {'widgets': widgets}, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url()).data['widgets'], widgets)
        self.assertEqual(DashboardLayout.objects.get(project=self.project, user=self.owner).widgets, widgets)
        for key in ('x', 'y', 'w', 'h'):
            invalid = {**widgets[0], key: 'NaN'}
            self.assertEqual(self.client.put(self.url(), {'widgets': [invalid]}, format='json').status_code, 400)
        self.assertEqual(self.client.get(self.url()).data['widgets'], widgets)

    def test_compacted_title_can_be_saved_despite_floating_point_noise(self):
        self.client.force_authenticate(user=self.owner)
        height = 3 + 10 / 60
        widgets = [
            {'id': 'tasks', 'x': 0, 'y': 0, 'w': 6, 'h': height},
            {'id': 'section-title-next', 'title': 'Next', 'x': 0, 'y': 12 - (12 - height), 'w': 12, 'h': 1},
        ]
        self.assertEqual(self.client.put(self.url(), {'widgets': widgets}, format='json').status_code, 200)
        self.assertEqual(self.client.get(self.url()).data['widgets'], widgets)
        invalid = [widgets[0], {**widgets[1], 'y': height - 0.01}]
        self.assertEqual(self.client.put(self.url(), {'widgets': invalid}, format='json').status_code, 400)
        self.assertEqual(DashboardLayout.objects.get(project=self.project, user=self.owner).widgets, widgets)

    def test_section_titles_are_capped(self):
        from dashboard.layout import MAX_SECTION_TITLES
        self.client.force_authenticate(user=self.owner)
        titles = [
            {'id': f'section-title-{index}', 'title': f'Section {index}', 'x': 0, 'y': index, 'w': 12, 'h': 1}
            for index in range(MAX_SECTION_TITLES + 1)
        ]
        self.assertEqual(self.client.put(self.url(), {'widgets': titles}, format='json').status_code, 400)
        self.assertFalse(DashboardLayout.objects.exists())
        self.assertEqual(self.client.put(self.url(), {'widgets': titles[:MAX_SECTION_TITLES]}, format='json').status_code, 200)
