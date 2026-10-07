/**
 * Who may set up a booking link for a teammate.
 *
 * Mirrors backend `core.permissions.can_manage_project_members`: everyone can
 * publish their own time; naming someone else is owner/admin work, the way
 * Calendly gates "create on behalf of".
 */
export const BOOKING_HOST_ADMIN_ROLES = new Set([
  'owner',
  'Super Administrator',
  'Organization Admin',
  'Team Leader',
  'Campaign Manager',
]);

export function canAssignBookingHost(args: {
  currentUser?: {
    id?: string | number;
    is_org_admin?: boolean;
  } | null;
  projectOwnerId?: string | number | null;
  membershipRole?: string | null;
}): boolean {
  const { currentUser, projectOwnerId, membershipRole } = args;
  if (!currentUser) return false;
  if (currentUser.is_org_admin) return true;
  if (
    projectOwnerId != null &&
    currentUser.id != null &&
    String(projectOwnerId) === String(currentUser.id)
  ) {
    return true;
  }
  return Boolean(membershipRole && BOOKING_HOST_ADMIN_ROLES.has(membershipRole));
}
