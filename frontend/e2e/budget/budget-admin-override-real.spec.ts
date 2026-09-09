/**
 * MED-315 / MED-240: org-admin override against a real backend (not mocked).
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
  expectAdminOverrideBadge,
  openTaskDetail,
  startReviewOnDetail,
} from './fixtures/budget-helpers';
import { fixtureEmail, openBudgetUserSession } from './fixtures/budget-users';

test.describe('Budget org-admin override (real backend)', () => {
  test('org admin overrides step 1 and chain advances to step 2 approver', async ({ browser }) => {
    const fixtures = requireBudgetE2EFixtures();
    const requester = await openBudgetUserSession(browser, fixtureEmail('requester'));
    const orgAdmin = await openBudgetUserSession(browser, fixtureEmail('org_admin'));
    const approverA = await openBudgetUserSession(browser, fixtureEmail('approver_a'));
    const approverB = await openBudgetUserSession(browser, fixtureEmail('approver_b'));

    const task = await createIsolatedBudgetTask(requester.page, `E2E org override ${Date.now()}`, {
      approverId: fixtures.users.approver_a.user_id,
      projectKind: 'chain',
    });

    try {
      await submitBudgetTaskViaApi(requester.page, task, fixtureEmail('requester'));

      await openTaskDetail(approverA.page, task.slug, task.projectId);
      await startReviewOnDetail(approverA.page);

      await openTaskDetail(orgAdmin.page, task.slug, task.projectId);
      await approveWithComment(orgAdmin.page, 'E2E org-admin override step 1');

      await expectAdminOverrideBadge(orgAdmin.page);

      let taskData = await fetchTaskJson(orgAdmin.page, task.id, fixtureEmail('org_admin'));
      expect(taskData.linked_object?.is_admin_override).toBe(true);
      expect(taskData.status).toBe('UNDER_REVIEW');
      expect(taskData.current_approver?.id ?? taskData.current_approver_id).toBe(
        fixtures.users.approver_b.user_id,
      );

      await openTaskDetail(approverB.page, task.slug, task.projectId);
      await expect(
        approverB.page.getByRole('button', { name: 'Approve', exact: true }),
      ).toBeVisible({ timeout: 10_000 });
    } finally {
      await task.cleanup();
      await requester.close();
      await orgAdmin.close();
      await approverA.close();
      await approverB.close();
    }
  });

  test('regular member cannot override approve', async ({ browser }) => {
    const fixtures = requireBudgetE2EFixtures();
    const requester = await openBudgetUserSession(browser, fixtureEmail('requester'));
    const regular = await openBudgetUserSession(browser, fixtureEmail('regular'));
    const approverA = await openBudgetUserSession(browser, fixtureEmail('approver_a'));

    const task = await createIsolatedBudgetTask(requester.page, `E2E override denied ${Date.now()}`, {
      approverId: fixtures.users.approver_a.user_id,
      projectKind: 'chain',
    });

    try {
      await submitBudgetTaskViaApi(requester.page, task, fixtureEmail('requester'));

      await openTaskDetail(approverA.page, task.slug, task.projectId);
      await startReviewOnDetail(approverA.page);
      await approverA.close();

      const attempt = await postTaskAction(
        regular.page,
        task,
        'make-approval',
        fixtureEmail('regular'),
        { action: 'approve', comment: 'should fail' },
      );
      expect([403, 400]).toContain(attempt.status);

      const taskData = await fetchTaskJson(requester.page, task.id);
      expect(taskData.status).toBe('UNDER_REVIEW');
      expect(taskData.current_approver?.id ?? taskData.current_approver_id).toBe(
        fixtures.users.approver_a.user_id,
      );
    } finally {
      await task.cleanup();
      await requester.close();
      await regular.close();
    }
  });
});
