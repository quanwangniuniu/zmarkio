"""
MED-235: concurrency tests for cross-assign hierarchy writes.

Two users adding subtasks in opposite directions (1→2 and 2→1) at the same
time must not both succeed. Row locks (SELECT … FOR UPDATE) serialize the pair.
"""
import threading
import unittest

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, connections
from core.test_utils import TenantSafeTransactionTestCase

from core.models import Organization, Project
from task.models import Task, TaskHierarchy
from task.services import TaskHierarchyCycleError, add_subtask_to_parent

User = get_user_model()


@unittest.skipUnless(
    connection.vendor == 'postgresql',
    'select_for_update row locking requires PostgreSQL (no-op on SQLite).',
)
class TaskHierarchyRaceTest(TenantSafeTransactionTestCase):
    """Concurrent opposite add-subtask calls must not create a 2-node cycle."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='hierarchy-race@example.com',
            username='hierarchy-race',
            password='pw',
        )
        self.organization = Organization.objects.create(name='Hierarchy Race Org')
        self.project = Project.objects.create(
            name='Hierarchy Race Project',
            organization=self.organization,
        )
        self.task_a = Task.objects.create(
            summary='Race Task A',
            type='asset',
            project=self.project,
            owner=self.user,
        )
        self.task_b = Task.objects.create(
            summary='Race Task B',
            type='asset',
            project=self.project,
            owner=self.user,
        )

    def _cross_assign(self, parent, child, results, index, barrier):
        try:
            barrier.wait(timeout=5)
            add_subtask_to_parent(parent_task=parent, child_task=child)
            results[index] = 'ok'
        except (TaskHierarchyCycleError, ValidationError):
            results[index] = 'rejected'
        finally:
            connections.close_all()

    def test_concurrent_cross_assign_serializes_no_cycle(self):
        """Exactly one of 1→2 / 2→1 wins; the other is rejected; no 2-edge cycle."""
        results = [None, None]
        barrier = threading.Barrier(2)
        threads = [
            threading.Thread(
                target=self._cross_assign,
                args=(self.task_a, self.task_b, results, 0, barrier),
            ),
            threading.Thread(
                target=self._cross_assign,
                args=(self.task_b, self.task_a, results, 1, barrier),
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(sorted(results), ['ok', 'rejected'])

        edge_ab = TaskHierarchy.objects.filter(
            parent_task=self.task_a,
            child_task=self.task_b,
        ).exists()
        edge_ba = TaskHierarchy.objects.filter(
            parent_task=self.task_b,
            child_task=self.task_a,
        ).exists()

        self.assertTrue(edge_ab or edge_ba)
        self.assertFalse(edge_ab and edge_ba)
