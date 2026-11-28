"""Tests for reaction snapshots: aggregation, versioning and the realtime payload (MED-277)."""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from core.models import Organization, Project, ProjectMember
from chat.models import Chat, ChatParticipant, ChatType, Message, MessageReaction
from chat.serializers import aggregate_reactions, current_reaction_version
from chat.tasks import notify_reaction_update

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture
def setup():
    """Two participants (alice, bob) in one chat, and one message from alice."""
    alice = User.objects.create_user(email='alice@example.com', username='alice', password='pass12345')
    bob = User.objects.create_user(email='bob@example.com', username='bob', password='pass12345')
    organization = Organization.objects.create(name='Reaction Org')
    project = Project.objects.create(name='Reaction Project', organization=organization)
    for user in (alice, bob):
        ProjectMember.objects.create(user=user, project=project, role='member', is_active=True)
    chat = Chat.objects.create(project=project, type=ChatType.PRIVATE)
    for user in (alice, bob):
        ChatParticipant.objects.create(chat=chat, user=user, is_active=True)
    message = Message.objects.create(chat=chat, sender=alice, content='hello')
    return alice, bob, message


def test_aggregate_reactions_groups_by_emoji_and_counts_users(setup):
    alice, bob, message = setup
    MessageReaction.objects.create(message=message, user=alice, emoji='👍')
    MessageReaction.objects.create(message=message, user=bob, emoji='👍')
    MessageReaction.objects.create(message=message, user=bob, emoji='🎉')

    rows = MessageReaction.objects.filter(message=message).select_related('user').order_by('id')
    result = {group['emoji']: group for group in aggregate_reactions(rows, current_user_id=alice.id)}

    assert result['👍']['count'] == 2
    assert {u['id'] for u in result['👍']['users']} == {alice.id, bob.id}
    assert result['👍']['reacted_by_me'] is True
    assert result['🎉']['count'] == 1
    assert result['🎉']['reacted_by_me'] is False


def test_reaction_version_increases_over_time():
    first = current_reaction_version()
    second = current_reaction_version()
    assert isinstance(first, int)
    assert second > first


def test_notify_reaction_update_broadcasts_full_snapshot_with_version(setup):
    alice, bob, message = setup
    MessageReaction.objects.create(message=message, user=alice, emoji='👍')
    MessageReaction.objects.create(message=message, user=bob, emoji='👍')

    with (
        patch('chat.tasks.OnlineStatusService.get_online_users', return_value=[alice.id, bob.id]),
        patch('chat.tasks.broadcast_event_to_user_groups_sync', return_value=([alice.id, bob.id], {})) as broadcast,
    ):
        notify_reaction_update(message.id, bob.id, '👍', 'added')

    broadcast.assert_called_once()
    payload = broadcast.call_args.args[2]['reaction']
    assert payload['message_id'] == message.id
    assert isinstance(payload['reactions_version'], int)
    assert len(payload['reactions']) == 1
    assert payload['reactions'][0]['emoji'] == '👍'
    assert payload['reactions'][0]['count'] == 2
    # The broadcast goes to everyone, so reacted_by_me must be worked out on each client.
    assert payload['reactions'][0]['reacted_by_me'] is False


def test_react_api_returns_snapshot_with_increasing_version(setup):
    alice, _bob, message = setup
    client = APIClient()
    client.force_authenticate(user=alice)
    url = reverse('message-react', kwargs={'pk': message.id})

    with patch('chat.tasks.notify_reaction_update.delay'):
        added = client.post(url, {'emoji': '👍'}, format='json')
        removed = client.post(url, {'emoji': '👍'}, format='json')  # same emoji again = toggle off

    assert added.status_code == 200
    assert added.data['status'] == 'added'
    assert added.data['message']['reactions'][0]['count'] == 1
    assert removed.data['status'] == 'removed'
    assert removed.data['message']['reactions'] == []
    assert removed.data['reactions_version'] > added.data['reactions_version']


def test_remove_reaction_api_returns_snapshot_with_version(setup):
    alice, _bob, message = setup
    MessageReaction.objects.create(message=message, user=alice, emoji='👍')
    client = APIClient()
    client.force_authenticate(user=alice)
    url = reverse('message-remove-reaction', kwargs={'pk': message.id, 'emoji': '👍'})

    with patch('chat.tasks.notify_reaction_update.delay') as notify:
        response = client.delete(url)

    assert response.status_code == 200
    assert response.data['message']['reactions'] == []
    assert isinstance(response.data['reactions_version'], int)
    assert response.data['reactions_version'] > 0
    notify.assert_called_once()
