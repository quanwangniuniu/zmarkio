/**
 * MED-315: single-approver happy path with distinct requester and approver roles.
 */

import { test, expect } from '@playwright/test';
import {
  createIsolatedBudgetTask,
  fetchTaskJson,
  requireBudgetE2EFixtures,
  submitBudgetTaskViaApi,
} from './fixtures/budget-fixtures';
import {
  approveWithComment,
  openTaskDetail,
  startReviewOnDetail,
  expectStatusChip,
} from './fixtures/budget-helpers';
import { fixtureEmail, openBudgetUserSession } from './fixtures/budget-users';

test.describe('Budget single-approver happy path', () => {
  test('requester submits and designated approver approves', async ({ browser }) => {
    const fixtures = requireBudgetE2EFixtures();
    const requester = await openBudgetUserSession(browser, fixtureEmail('requester'), undefined, 'single');
    const approver = await openBudgetUserSession(browser, fixtureEmail('approver_a'), undefined, 'single');

    const task = await createIsolatedBudgetTask(requester.page, `E2E single approver ${Date.now()}`, {
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

      let taskData = await fetchTaskJson(requester.page, task.id, undefined, 'single');
      expect(taskData.status).toBe('SUBMITTED');

      await openTaskDetail(approver.page, task.slug, task.projectId);
      await startReviewOnDetail(approver.page);
      await approveWithComment(approver.page, 'E2E single-step approval');
      await expectStatusChip(approver.page, /approved/i);

      taskData = await fetchTaskJson(requester.page, task.id, undefined, 'single');
      expect(taskData.status).toBe('APPROVED');
      expect(taskData.current_approver?.id ?? taskData.current_approver_id).toBe(
        fixtures.users.approver_a.user_id,
      );

      await openTaskDetail(requester.page, task.slug, task.projectId);
      await expect(
        requester.page.getByRole('button', { name: 'Approve', exact: true }),
      ).not.toBeVisible({ timeout: 3_000 });
    } finally {
      await task.cleanup();
      await requester.close();
      await approver.close();
    }
  });
});
