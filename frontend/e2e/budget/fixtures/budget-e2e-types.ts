export type BudgetE2EUserFixture = {
  user_id: number;
  email: string;
  username: string;
  access_token: string;
  refresh_token: string;
  organization_access_token: string;
};

export type BudgetE2EFixturePayload = {
  organization_id: number;
  organization_slug: string;
  project_id: number;
  project_slug: string;
  project_name: string;
  budget_pool_id: number;
  ad_channel_id: number;
  budget_pool_composite: string;
  approval_chain_id: number;
  approval_chain_name: string;
  single_approver_project_id: number;
  single_approver_project_slug: string;
  single_approver_project_name: string;
  single_approver_budget_pool_composite: string;
  team_id: number;
  role_name: string;
  password: string;
  users: {
    requester: BudgetE2EUserFixture;
    approver_a: BudgetE2EUserFixture;
    approver_b: BudgetE2EUserFixture;
    approver_c: BudgetE2EUserFixture;
    org_admin: BudgetE2EUserFixture;
    regular: BudgetE2EUserFixture;
  };
};

export const BUDGET_E2E_EMAILS = {
  requester: 'e2e-budget-requester@example.com',
  approver_a: 'e2e-budget-approver-a@example.com',
  approver_b: 'e2e-budget-approver-b@example.com',
  approver_c: 'e2e-budget-approver-c@example.com',
  org_admin: 'e2e-budget-org-admin@example.com',
  regular: 'e2e-budget-regular@example.com',
} as const;

export const BUDGET_E2E_DEFAULT_PASSWORD = 'password123!';

export const BUDGET_E2E_DEFAULT_ROLE_NAME = 'E2E Budget Approver';

export function resolveBudgetRbacHeaders(
  fixtures: BudgetE2EFixturePayload,
  email: string,
): Record<string, string> {
  const headers: Record<string, string> = {};

  if (email === fixtures.users.org_admin.email) {
    headers['x-user-role'] = 'Org Admin';
    return headers;
  }

  if (email === fixtures.users.regular.email) {
    headers['x-user-role'] = 'Member';
    return headers;
  }

  const teamId = fixtures.team_id;
  if (!teamId) {
    throw new Error(
      'Budget E2E fixtures missing team_id. Re-run issue_budget_e2e_fixtures to regenerate budget-e2e-fixtures.json.',
    );
  }

  headers['x-user-role'] = fixtures.role_name ?? BUDGET_E2E_DEFAULT_ROLE_NAME;
  headers['x-team-id'] = String(teamId);
  return headers;
}

export function resolveBudgetRbacAuthState(
  fixtures: BudgetE2EFixturePayload,
  email: string,
): { roles: string[]; userTeams: number[]; selectedTeamId: number | null } {
  if (email === fixtures.users.org_admin.email) {
    return { roles: ['Org Admin'], userTeams: [], selectedTeamId: null };
  }

  if (email === fixtures.users.regular.email) {
    return { roles: ['Member'], userTeams: [], selectedTeamId: null };
  }

  const teamId = fixtures.team_id;
  if (!teamId) {
    throw new Error(
      'Budget E2E fixtures missing team_id. Re-run issue_budget_e2e_fixtures to regenerate budget-e2e-fixtures.json.',
    );
  }

  return {
    roles: [fixtures.role_name ?? BUDGET_E2E_DEFAULT_ROLE_NAME],
    userTeams: [teamId],
    selectedTeamId: teamId,
  };
}
