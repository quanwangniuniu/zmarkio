"""GDPR user-erasure orchestration driven by the central per-app policy."""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection, models, transaction
from django.db.models import F, Q

from authentication.session_registry import SessionRegistry
from core.erasure_policies import ErasureAction, iter_model_policies
from core.services.tenant import slug_to_schema_name
from core.tenant_context import tenant_schema_context


def is_erased_user(user):
    """Return whether the existing user row is the erasure tombstone."""
    return bool(
        user.pk
        and not user.is_active
        and user.username == f"deleted_{user.pk}"
        and user.email == f"deleted_{user.pk}@removed.invalid"
    )


def request_user_erasure(user_id):
    """Disable access, revoke sessions, and enqueue the long-running cascade."""
    from core.tasks import perform_user_erasure

    User = get_user_model()
    with tenant_schema_context("public"), transaction.atomic():
        user = User.objects.select_for_update().get(pk=user_id)
        if not user.is_active:
            raise ValueError("Account is already inactive")
        user.is_active = False
        user.auth_token_version = F("auth_token_version") + 1
        user.save(update_fields=["is_active", "auth_token_version"])
        SessionRegistry.revoke_all_sessions(user_id)
        perform_user_erasure.delay(user_id)


def _user_filter(model, field_names, user_id):
    query = Q()
    for field_name in field_names:
        field = model._meta.get_field(field_name)
        lookup = f"{field_name}__pk" if field.many_to_many else f"{field_name}_id"
        query |= Q(**{lookup: user_id})
    return query


def _delete_files(queryset):
    file_fields = [
        field for field in queryset.model._meta.fields
        if isinstance(field, models.FileField)
    ]
    if not file_fields:
        return
    for instance in queryset.only(*(field.name for field in file_fields)).iterator():
        for field in file_fields:
            value = getattr(instance, field.name)
            if value:
                value.delete(save=False)


def _apply_policy(policy, user_id):
    model = apps.get_model(policy.model_label)
    actions = {}
    for field_policy in policy.fields:
        actions.setdefault(field_policy.action, []).append(field_policy.field_name)

    delete_fields = actions.get(ErasureAction.DELETE, ())
    if delete_fields:
        queryset = model._base_manager.filter(
            _user_filter(model, delete_fields, user_id)
        )
        _delete_files(queryset)
        queryset.delete()

    for field_name in actions.get(ErasureAction.UNLINK, ()):
        field = model._meta.get_field(field_name)
        queryset = model._base_manager.filter(
            _user_filter(model, (field_name,), user_id)
        ).distinct()
        if field.many_to_many:
            for instance in queryset.iterator():
                getattr(instance, field_name).remove(user_id)
        elif field.null:
            queryset.update(**{field_name: None})
        else:  # guarded by policy validation tests
            raise ValueError(
                f"Cannot unlink non-null field {policy.model_label}.{field_name}"
            )


def _apply_schema_policies(schema_name, user_id):
    with tenant_schema_context(schema_name), transaction.atomic():
        existing_tables = set(connection.introspection.table_names())
        for policy in iter_model_policies():
            model = apps.get_model(policy.model_label)
            if model._meta.db_table in existing_tables:
                _apply_policy(policy, user_id)


def _tenant_schemas():
    Organization = apps.get_model("core.Organization")
    with tenant_schema_context("public"):
        slugs = Organization.objects.values_list("slug", flat=True).iterator()
        return [slug_to_schema_name(slug) for slug in slugs]


def _anonymize_user(user_id):
    User = get_user_model()
    with tenant_schema_context("public"), transaction.atomic():
        try:
            user = User.objects.select_for_update().get(pk=user_id)
        except User.DoesNotExist:
            return False
        if is_erased_user(user):
            return True
        if user.is_active:
            # A queued task can outlive a rolled-back request transaction.
            return False

        if user.avatar:
            user.avatar.delete(save=False)
        user.email = f"deleted_{user.pk}@removed.invalid"
        user.username = f"deleted_{user.pk}"
        user.first_name = ""
        user.last_name = ""
        user.job = ""
        user.department = ""
        user.location = ""
        user.avatar = None
        user.google_id = None
        user.google_registered = False
        user.verification_token = None
        user.password_reset_token = None
        user.password_reset_token_expires_at = None
        user.organization = None
        user.current_organization = None
        user.active_project = None
        user.is_verified = False
        user.is_staff = False
        user.is_superuser = False
        user.set_unusable_password()
        user.save()
        user.groups.clear()
        user.user_permissions.clear()
        return True


def erase_user_data(user_id):
    """Apply every schema cascade, then replace the account with a tombstone.

    Each schema is committed separately so retries remain bounded and
    idempotent. The account is anonymized last, so a partial failure is retried
    instead of being mistaken for completed erasure.
    """
    User = get_user_model()
    with tenant_schema_context("public"):
        try:
            user = User.objects.only("id", "is_active", "username", "email").get(pk=user_id)
        except User.DoesNotExist:
            return False
        if is_erased_user(user):
            return True
        if user.is_active:
            return False

    for schema_name in ("public", *_tenant_schemas()):
        _apply_schema_policies(schema_name, user_id)
    return _anonymize_user(user_id)
