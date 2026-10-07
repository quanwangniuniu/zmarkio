"""Who may complete a booking on a public link."""

from core.models import ProjectMember


def has_named_invitees(link) -> bool:
    if link.invitee_users.exists():
        return True
    return bool(link.invitee_emails)


def is_named_invitee(link, user) -> bool:
    """
    Signed-in account that was named on the link.

    Email-only invitees count once they sign in with that address. The host
    is not implied — naming people is what makes the list.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if link.invitee_users.filter(pk=user.pk).exists():
        return True
    email = (getattr(user, "email", None) or "").strip().lower()
    if not email:
        return False
    named = {
        address.strip().lower()
        for address in (link.invitee_emails or [])
        if address
    }
    return email in named


def can_book_public_link(link, user) -> bool:
    """Open links stay open. Invitees-only links require a named, signed-in person."""
    if not getattr(link, "invitees_only", False):
        return True
    return is_named_invitee(link, user)


def booker_shares_project(link, user) -> bool:
    """
    The signed-in viewer and the host already sit in the same project.

    Used on the public confirm step so a teammate can be told to keep the
    meeting on their personal calendar. Guests and outsiders stay false —
    no project membership is leaked to an anonymous payload.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    calendar = getattr(link, "calendar", None)
    if calendar is not None and getattr(calendar, "project_id", None):
        return ProjectMember.objects.filter(
            project_id=calendar.project_id,
            user=user,
            is_active=True,
        ).exists()
    host_id = getattr(link, "owner_id", None)
    if not host_id:
        return False
    host_projects = ProjectMember.objects.filter(
        user_id=host_id,
        is_active=True,
    ).values_list("project_id", flat=True)
    return ProjectMember.objects.filter(
        user=user,
        is_active=True,
        project_id__in=host_projects,
    ).exists()
