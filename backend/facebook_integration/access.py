from __future__ import annotations

from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404

from core.models import Project, ProjectMember
from core.permissions import can_manage_project_members
from core.tenant_context import current_tenant_schema

from .models import MetaAdAccount


def _active_project_ids_for_user(user, project_id: int | None = None) -> QuerySet:
    qs = ProjectMember.objects.filter(
        user=user,
        is_active=True,
        project__is_deleted=False,
    )
    if project_id is not None:
        qs = qs.filter(project_id=project_id)
    return qs.values_list("project_id", flat=True)


def get_accessible_meta_ad_accounts(
    user,
    *,
    project_id: int | None = None,
) -> QuerySet[MetaAdAccount]:
    """Accounts visible to a user through ownership or active project sharing."""
    if user is None or not getattr(user, "is_authenticated", False):
        return MetaAdAccount.objects.none()

    account_filter = Q(connection__user=user, connection__is_active=True)
    shared_project_ids = _active_project_ids_for_user(user, project_id=project_id)
    account_filter |= Q(
        project_id__in=shared_project_ids,
        project_schema=current_tenant_schema(),
        connection__is_active=True,
    )

    return (
        MetaAdAccount.objects.filter(account_filter)
        .select_related("connection", "connection__user")
        .distinct()
    )


def get_accessible_meta_ad_account_or_404(user, ad_account_id: int) -> MetaAdAccount:
    return get_object_or_404(
        get_accessible_meta_ad_accounts(user),
        pk=ad_account_id,
    )


def user_can_read_meta_ad_account(user, account: MetaAdAccount) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if account.connection.user_id == user.id:
        return True
    if account.project_id is None or account.project_schema != current_tenant_schema():
        return False
    return ProjectMember.objects.filter(
        user=user,
        project_id=account.project_id,
        project__is_deleted=False,
        is_active=True,
    ).exists()


def user_can_manage_meta_ad_account(
    user,
    account: MetaAdAccount,
    *,
    target_project: Project | None = None,
) -> bool:
    """Connector, linked-project managers, or target-project managers may manage."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if account.connection.user_id == user.id:
        return True
    if (account.project_id and account.project_schema == current_tenant_schema()
            and can_manage_project_members(user, account.project)):
        return True
    if target_project is not None and can_manage_project_members(user, target_project):
        return True
    return False


def user_can_sync_meta_ad_account(user, account: MetaAdAccount) -> bool:
    return user_can_manage_meta_ad_account(user, account)
