/**
 * MED-315 / MED-238: reject → revise → resubmit → approve with isolated fixtures.
 */

import { test, expect } from '@playwright/test';
import {
  createIsolatedBudgetTask,
  fetchTaskJson,
  postTaskAction,
  requireBudgetE2EFixtures,
  submitBudgetTaskViaApi,
} from './fixtures/budget-fixtures';
import {
  approveWithComment,
  clickFsmButton,
  openTaskDetail,
  rejectWithComment,
  startReviewOnDetail,
  expectStatusChip,
} from './fixtures/budget-helpers';
import { fixtureEmail, openBudgetUserSession } from './fixtures/budget-users';

test.describe('Budget reject → reopen → approve', () => {
  test('rejected task hides illegal actions, revises, and completes approval', async ({ browser }) => {
    const fixtures = requireBudgetE2EFixtures();
    const requester = await openBudgetUserSession(browser, fixtureEmail('requester'), undefined, 'single');
    const approver = await openBudgetUserSession(browser, fixtureEmail('approver_a'), undefined, 'single');

    const task = await createIsolatedBudgetTask(requester.page, `E2E reject reopen ${Date.now()}`, {
      approverId: fixtures.users.approver_a.user_id,
      projectKind: 'single',
    });

    try {
      await submitBudgetTaskViaApi(
        requester.page,
        task,
        fixtureEmail('requester'),
        'single',
      );

      await openTaskDetail(approver.page, task.slug, task.projectId);
      await startReviewOnDetail(approver.page);
      await rejectWithComment(approver.page, 'E2E: rejecting to test reopen path');

      let taskData = await fetchTaskJson(requester.page, task.id, undefined, 'single');
      expect(taskData.status).toBe('REJECTED');

      await expect(approver.page.getByRole('button', { name: 'Approve', exact: true })).not.toBeVisible({
        timeout: 3_000,
      });
      await expect(approver.page.getByRole('button', { name: 'Reject', exact: true })).not.toBeVisible({
        timeout: 3_000,
      });
      await expect(approver.page.getByRole('button', { name: 'Lock', exact: true })).not.toBeVisible({
        timeout: 3_000,
      });
      await expect(approver.page.getByRole('button', { name: 'Revise', exact: true })).toBeVisible({
        timeout: 10_000,
      });

      const lockAttempt = await postTaskAction(
        approver.page,
        task,
        'lock',
        fixtureEmail('approver_a'),
        {},
        'single',
      );
      expect(lockAttempt.status).toBe(400);
      taskData = await fetchTaskJson(requester.page, task.id, undefined, 'single');
      expect(taskData.status).toBe('REJECTED');

      await clickFsmButton(approver.page, 'Revise');
      await expectStatusChip(approver.page, /draft/i);
      taskData = await fetchTaskJson(requester.page, task.id, undefined, 'single');
      expect(taskData.status).toBe('DRAFT');

      await openTaskDetail(requester.page, task.slug, task.projectId);
      await clickFsmButton(requester.page, 'Submit');
      await expectStatusChip(requester.page, /submitted/i);

      await openTaskDetail(approver.page, task.slug, task.projectId);
      await startReviewOnDetail(approver.page);
      await approveWithComment(approver.page, 'E2E: approving revised request');
      await expectStatusChip(approver.page, /approved/i);

      taskData = await fetchTaskJson(requester.page, task.id, undefined, 'single');
      expect(taskData.status).toBe('APPROVED');
    } finally {
      await task.cleanup();
      await requester.close();
      await approver.close();
    }
  });
});
