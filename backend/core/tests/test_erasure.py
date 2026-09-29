import pytest
from django.apps import apps
from django.contrib.auth import get_user_model

from chat.models import Chat, ChatType, Message
from chat.serializers import UserSimpleSerializer
from core.erasure_policies import (
    ERASURE_POLICIES,
    ErasureAction,
    iter_model_policies,
    policy_field_map,
)
from core.models import Project
from core.serializers import UserSummarySerializer
from core.services.erasure import erase_user_data
from user_preferences.models import UserPreferences


def _installed_user_relations():
    User = get_user_model()
    relations = set()
    for model in apps.get_models():
        if model._meta.proxy or model._meta.auto_created or model is User:
            continue
        for field in (*model._meta.fields, *model._meta.local_many_to_many):
            if getattr(field.remote_field, "model", None) is User:
                relations.add((model._meta.label_lower, field.name))
    return relations


def test_policy_covers_every_installed_user_relation_exactly_once():
    declared = [
        (policy.model_label.lower(), field.field_name)
        for policy in iter_model_policies()
        for field in policy.fields
    ]
    assert len(declared) == len(set(declared)), "A user relation has multiple erasure actions"
    assert set(declared) == _installed_user_relations()


@pytest.mark.parametrize("app_label", sorted(ERASURE_POLICIES))
def test_each_app_declares_valid_cascade_policy(app_label):
    policies = ERASURE_POLICIES[app_label]
    assert policies
    for policy in policies:
        model = apps.get_model(policy.model_label)
        assert model._meta.app_label == app_label
        assert policy.fields
        for field_policy in policy.fields:
            field = model._meta.get_field(field_policy.field_name)
            assert field_policy.action in ErasureAction
            if field_policy.action is ErasureAction.UNLINK:
                assert field.many_to_many or field.null


@pytest.mark.django_db
def test_delete_anonymize_and_unlink_cascades(user, project):
    preferences, _ = UserPreferences.objects.get_or_create(user=user)
    chat = Chat.objects.create(project=project, type=ChatType.GROUP, name="History", created_by=user)
    message = Message.objects.create(chat=chat, sender=user, content="Shared work")
    message.hidden_by_users.add(user)
    user.is_active = False
    user.save(update_fields=["is_active"])

    assert erase_user_data(user.pk)

    assert not UserPreferences.objects.filter(pk=preferences.pk).exists()
    assert Project.objects.filter(pk=project.pk, owner_id=user.pk).exists()
    assert Message.objects.filter(pk=message.pk, sender_id=user.pk, content="Shared work").exists()
    assert not message.hidden_by_users.filter(pk=user.pk).exists()
    user.refresh_from_db()
    assert UserSimpleSerializer(user).data["username"] == "Deleted user"
    assert UserSimpleSerializer(user).data["email"] == ""
    assert UserSimpleSerializer(user).data["is_online"] is False
    assert UserSummarySerializer(user).data["name"] == "Deleted user"


def test_legal_and_audit_relations_are_explicitly_retained():
    policies = policy_field_map()
    assert policies[("core.auditevent", "actor")] is ErasureAction.RETAIN
    assert policies[("audit.adminauditevent", "actor")] is ErasureAction.RETAIN
    assert policies[("stripe_meta.payment", "user")] is ErasureAction.RETAIN
    assert policies[("task.approvalrecord", "approved_by")] is ErasureAction.RETAIN
