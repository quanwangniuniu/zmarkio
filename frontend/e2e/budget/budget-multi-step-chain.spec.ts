/**
 * MED-315: isolated multi-step approval chain (A → B → C).
 *
 * Requires backend fixtures:
 *   python manage.py issue_budget_e2e_fixtures > frontend/e2e/budget/fixtures/budget-e2e-fixtures.json
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

test.describe('Budget multi-step approval chain', () => {
  test('three approvers advance the chain until APPROVED', async ({ browser }) => {
    const fixtures = requireBudgetE2EFixtures();
    const requester = await openBudgetUserSession(browser, fixtureEmail('requester'));
    const approverA = await openBudgetUserSession(browser, fixtureEmail('approver_a'));
    const approverB = await openBudgetUserSession(browser, fixtureEmail('approver_b'));
    const approverC = await openBudgetUserSession(browser, fixtureEmail('approver_c'));

    const task = await createIsolatedBudgetTask(requester.page, `E2E chain ${Date.now()}`, {
      approverId: fixtures.users.approver_a.user_id,
    });

    try {
      await submitBudgetTaskViaApi(requester.page, task, fixtureEmail('requester'));

      await openTaskDetail(approverA.page, task.slug, task.projectId);
      await startReviewOnDetail(approverA.page);

      let taskData = await fetchTaskJson(requester.page, task.id);
      expect(taskData.status).toBe('UNDER_REVIEW');
      expect(taskData.current_approver?.id ?? taskData.current_approver_id).toBe(
        fixtures.users.approver_a.user_id,
      );

      await approveWithComment(approverA.page, 'E2E step 1 approved');
      await expectStatusChip(approverA.page, /under.?review/i);

      taskData = await fetchTaskJson(requester.page, task.id);
      expect(taskData.current_approver?.id ?? taskData.current_approver_id).toBe(
        fixtures.users.approver_b.user_id,
      );

      await openTaskDetail(approverB.page, task.slug, task.projectId);
      await approveWithComment(approverB.page, 'E2E step 2 approved');
      taskData = await fetchTaskJson(requester.page, task.id);
      expect(taskData.current_approver?.id ?? taskData.current_approver_id).toBe(
        fixtures.users.approver_c.user_id,
      );

      await openTaskDetail(approverC.page, task.slug, task.projectId);
      await approveWithComment(approverC.page, 'E2E final approval');
      await expectStatusChip(approverC.page, /approved/i);

      taskData = await fetchTaskJson(requester.page, task.id);
      expect(taskData.status).toBe('APPROVED');
    } finally {
      await task.cleanup();
      await requester.close();
      await approverA.close();
      await approverB.close();
      await approverC.close();
    }
  });

  test('non-current approver cannot approve out of turn', async ({ browser }) => {
    const fixtures = requireBudgetE2EFixtures();
    const requester = await openBudgetUserSession(browser, fixtureEmail('requester'));
    const approverB = await openBudgetUserSession(browser, fixtureEmail('approver_b'));

    const task = await createIsolatedBudgetTask(requester.page, `E2E chain guard ${Date.now()}`, {
      approverId: fixtures.users.approver_a.user_id,
    });

    try {
      await submitBudgetTaskViaApi(requester.page, task, fixtureEmail('requester'));

      const approverA = await openBudgetUserSession(browser, fixtureEmail('approver_a'));
      try {
        await openTaskDetail(approverA.page, task.slug, task.projectId);
        await startReviewOnDetail(approverA.page);
      } finally {
        await approverA.close();
      }

      await openTaskDetail(approverB.page, task.slug, task.projectId);
      await expect(
        approverB.page.getByRole('button', { name: 'Approve', exact: true }),
      ).not.toBeVisible({ timeout: 5_000 });
    } finally {
      await task.cleanup();
      await requester.close();
      await approverB.close();
    }
  });
});
