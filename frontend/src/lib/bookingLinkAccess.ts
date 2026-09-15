export function hasNamedInvitees(
  inviteeIds: number[],
  inviteeEmails: string[],
): boolean {
  return inviteeIds.length > 0 || inviteeEmails.some((address) => address.trim());
}

export function canRestrictToInvitees(
  inviteeIds: number[],
  inviteeEmails: string[],
): boolean {
  return hasNamedInvitees(inviteeIds, inviteeEmails);
}
