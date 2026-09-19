"""Live guidance updates over the CSM agent socket (CSM-S03-02 / MED-222).

The consumer's DB lookups are patched so these tests exercise only the
channel-group bookkeeping and need no transactional database.
"""
import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from channels.layers import channel_layers, get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator

from csm.consumers import CsmConversationConsumer
from csm.routing import websocket_urlpatterns
from csm.services.guidance import guidance_group_name


TEST_CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}
AGENT = SimpleNamespace(id=7, is_authenticated=True, is_staff=False, is_superuser=False)
# conversation id -> matched Experience Group id
CONVERSATION_GROUPS = {101: 11, 102: 11, 103: 12, 104: None}


def _reset_channel_layers():
    # Evict cached layers so each test gets one bound to its own event loop
    # (same approach as chat/tests/test_consumers.py).
    cache = getattr(channel_layers, '_layers', None)
    if isinstance(cache, dict):
        cache.clear()


@pytest.fixture(autouse=True)
def in_memory_channel_layer(settings):
    settings.CHANNEL_LAYERS = TEST_CHANNEL_LAYERS
    _reset_channel_layers()
    yield
    _reset_channel_layers()


@pytest.fixture(autouse=True)
def patched_lookups():
    async def load_queues(self):
        return False, set()

    async def can_access(self, conversation_id):
        return conversation_id in CONVERSATION_GROUPS

    async def experience_group_id(self, conversation_id):
        return CONVERSATION_GROUPS.get(conversation_id)

    with patch.object(CsmConversationConsumer, '_load_accessible_queue_ids', load_queues), \
            patch.object(CsmConversationConsumer, 'can_access_conversation', can_access), \
            patch.object(CsmConversationConsumer, '_conversation_experience_group_id', experience_group_id):
        yield


async def _connect():
    communicator = WebsocketCommunicator(URLRouter(websocket_urlpatterns), f'/ws/csm/agent/{AGENT.id}/')
    communicator.scope['user'] = AGENT
    connected, _ = await communicator.connect()
    assert connected
    return communicator


async def _join(communicator, conversation_id):
    await communicator.send_json_to({'type': 'join_conversation', 'conversation_id': conversation_id})
    reply = await communicator.receive_json_from(timeout=5)
    assert reply == {'type': 'joined', 'conversation_id': conversation_id}


async def _leave(communicator, conversation_id):
    await communicator.send_json_to({'type': 'leave_conversation', 'conversation_id': conversation_id})
    # Leave has no reply; yield so the consumer processes it before we broadcast.
    await asyncio.sleep(0.05)


async def _broadcast(experience_group_id):
    await get_channel_layer().group_send(
        guidance_group_name(experience_group_id),
        {'type': 'guidance.updated', 'experience_group_ids': [experience_group_id]},
    )


async def _close(communicator):
    with suppress(Exception):
        await communicator.disconnect(timeout=2)


async def test_joined_conversation_receives_guidance_updates():
    communicator = await _connect()
    try:
        await _join(communicator, 101)
        await _broadcast(11)

        event = await communicator.receive_json_from(timeout=5)
        assert event == {'type': 'guidance_updated', 'experience_group_ids': [11]}
    finally:
        await _close(communicator)


async def test_other_groups_are_not_delivered():
    communicator = await _connect()
    try:
        await _join(communicator, 101)
        await _broadcast(12)
        assert await communicator.receive_nothing(timeout=0.2)
    finally:
        await _close(communicator)


async def test_leaving_the_conversation_stops_updates():
    communicator = await _connect()
    try:
        await _join(communicator, 101)
        await _leave(communicator, 101)
        await _broadcast(11)
        assert await communicator.receive_nothing(timeout=0.2)
    finally:
        await _close(communicator)


async def test_group_is_kept_while_another_joined_conversation_shares_it():
    communicator = await _connect()
    try:
        await _join(communicator, 101)
        await _join(communicator, 102)
        await _leave(communicator, 101)

        await _broadcast(11)

        event = await communicator.receive_json_from(timeout=5)
        assert event['type'] == 'guidance_updated'
    finally:
        await _close(communicator)


async def test_conversation_without_group_joins_no_guidance_channel():
    communicator = await _connect()
    try:
        await _join(communicator, 104)
        await _broadcast(11)
        await _broadcast(12)
        assert await communicator.receive_nothing(timeout=0.2)
    finally:
        await _close(communicator)
