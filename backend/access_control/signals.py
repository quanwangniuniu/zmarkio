from django.db import connection, transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from access_control.models import RolePermission, UserRole
from access_control.services import invalidate_user_permission_cache
from core.models import ProjectMember, Role, TeamMember


def _current_schema_name() -> str:
    with connection.cursor() as cursor:
        cursor.execute("SHOW search_path")
        search_path = cursor.fetchone()[0]
    return search_path.split(",")[0].strip().strip('"')


def _invalidate_user(user_id: int) -> None:
    schema_name = _current_schema_name()
    transaction.on_commit(
        lambda: invalidate_user_permission_cache(schema_name, user_id)
    )


def _invalidate_role_users(role_id: int) -> None:
    schema_name = _current_schema_name()
    user_ids = list(
        UserRole.objects.filter(role_id=role_id)
        .values_list("user_id", flat=True)
        .distinct()
    )

    def invalidate():
        for user_id in user_ids:
            invalidate_user_permission_cache(schema_name, user_id)

    transaction.on_commit(invalidate)


@receiver(post_save, sender=UserRole)
@receiver(post_delete, sender=UserRole)
def invalidate_on_user_role_change(sender, instance, **kwargs):
    _invalidate_user(instance.user_id)


@receiver(post_save, sender=RolePermission)
@receiver(post_delete, sender=RolePermission)
def invalidate_on_role_permission_change(sender, instance, **kwargs):
    _invalidate_role_users(instance.role_id)


@receiver(post_save, sender=Role)
def invalidate_on_role_change(sender, instance, **kwargs):
    _invalidate_role_users(instance.id)


@receiver(post_save, sender=ProjectMember)
@receiver(post_delete, sender=ProjectMember)
def invalidate_on_project_membership_change(sender, instance, **kwargs):
    _invalidate_user(instance.user_id)


@receiver(post_save, sender=TeamMember)
@receiver(post_delete, sender=TeamMember)
def invalidate_on_team_membership_change(sender, instance, **kwargs):
    _invalidate_user(instance.user_id)