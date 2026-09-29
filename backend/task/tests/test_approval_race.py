"""
MED-236: concurrency tests for the approval-chain status race.

When two approvers (e.g. the same user in two browser tabs) act on the same
task at the same time, the transition must be serialized so exactly one wins.
The loser must be rejected with 409 instead of overwriting the winning status
or creating a duplicate ApprovalRecord.

These tests drive the real make-approval endpoint from two threads. Row-level
locking (SELECT ... FOR UPDATE) only serializes on a backend that supports it,
so they are skipped when the test database is not PostgreSQL.
"""
import threading
import unittest

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, Project
from core.test_utils import TenantSafeTransactionTestCase
from task.models import Task, ApprovalRecord

User = get_user_model()


@unittest.skipUnless(
    connection.vendor == 'postgresql',
    'select_for_update row locking requires PostgreSQL (no-op on SQLite).',
)
class ApprovalRaceTest(TenantSafeTransactionTestCase):
    """Two concurrent approval decisions on one task must serialize."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email='owner-race@example.com', username='owner-race', password='pw'
        )
        self.approver = User.objects.create_user(
            email='approver-race@example.com', username='approver-race', password='pw'
        )
        self.organization = Organization.objects.create(name='Race Org')
        self.project = Project.objects.create(
            name='Race Project', organization=self.organization, owner=self.owner
        )
        # The approver reaches the task via the current-approver rule in
        # get_object(), so no explicit project membership is required here.
        self.task = Task.objects.create(
            summary='Race task', owner=self.owner, project=self.project, type='execution'
        )
        # Move to UNDER_REVIEW with a designated approver (single-approver mode).
        self.task.submit()
        self.task.start_review()
        self.task.current_approver = self.approver
        self.task.current_approval_step = 1
        self.task.save()

        self.url = reverse('task-make-approval', kwargs={'pk': self.task.slug})

    def _decide(self, payload, results, index, barrier):
        """Fire one make-approval request from its own client/connection."""
        try:
            barrier.wait(timeout=5)
            client = APIClient()
            client.force_authenticate(user=self.approver)
            response = client.post(self.url, payload, format='json')
            results[index] = response.status_code
        finally:
            # Threads open their own DB connection; close it so the test DB can
            # be torn down cleanly.
            connections.close_all()

    def test_concurrent_approve_and_reject_serialize(self):
        """One decision wins (200); the other is rejected with 409."""
        results = [None, None]
        barrier = threading.Barrier(2)
        threads = [
            threading.Thread(
                target=self._decide,
                args=({'action': 'approve'}, results, 0, barrier),
            ),
            threading.Thread(
                target=self._decide,
                args=({'action': 'reject', 'comment': 'nope'}, results, 1, barrier),
            ),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Exactly one winner (200) and one loser (409 already decided).
        self.assertIn(status.HTTP_200_OK, results)
        self.assertIn(status.HTTP_409_CONFLICT, results)

        # Re-fetch fresh: refresh_from_db() can't overwrite the protected
        # django_fsm status field on an existing instance.
        task = Task.objects.get(pk=self.task.pk)
        # Status is a single terminal decision, never left mid-race.
        self.assertIn(
            task.status,
            {Task.Status.APPROVED, Task.Status.REJECTED},
        )
        # No lost update / duplicate: exactly one record for this step + round.
        records = ApprovalRecord.objects.filter(
            task=task, step_number=1, revision_round=0
        )
        self.assertEqual(records.count(), 1)
        # The record's outcome matches the winning status.
        record = records.first()
        expected_approved = task.status == Task.Status.APPROVED
        self.assertEqual(record.is_approved, expected_approved)

    def test_double_approve_records_once(self):
        """Two identical approve clicks must not double-apply the transition."""
        results = [None, None]
        barrier = threading.Barrier(2)
        threads = [
            threading.Thread(
                target=self._decide,
                args=({'action': 'approve'}, results, i, barrier),
            )
            for i in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertIn(status.HTTP_200_OK, results)
        self.assertIn(status.HTTP_409_CONFLICT, results)

        task = Task.objects.get(pk=self.task.pk)
        self.assertEqual(task.status, Task.Status.APPROVED)
        self.assertEqual(
            ApprovalRecord.objects.filter(task=task, revision_round=0).count(),
            1,
        )
