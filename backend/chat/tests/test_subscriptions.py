"""Unit and concurrency tests for the chat subscription registry."""

import asyncio
import threading
import time
from collections import defaultdict
from typing import Any

import pytest
from channels.testing import WebsocketCommunicator
from prometheus_client import REGISTRY

from chat.consumers import ChatConsumer
from chat.subscriptions import (
    RegistryCancelledError,
    RegistryClosedError,
    RegistryError,
    RegistryTimeoutError,
    SubscriptionRegistry,
)


class FakeChannelLayer:
    """Small asynchronous Channel Layer stand-in with injectable failures."""

    def __init__(self) -> None:
        self.groups: dict[str, set[str]] = defaultdict(set)
        self.calls: list[tuple[str, str, str | dict[str, Any]]] = []
        self.fail_add: set[str] = set()
        self.fail_discard: set[str] = set()
        self.fail_send: set[str] = set()
        self.operation_loops: list[asyncio.AbstractEventLoop] = []

    async def group_add(self, group: str, channel: str) -> None:
        await asyncio.sleep(0)
        self.operation_loops.append(asyncio.get_running_loop())
        self.calls.append(('add', group, channel))
        if group in self.fail_add:
            raise RuntimeError(f'add failed: {group}')
        self.groups[group].add(channel)

    async def group_discard(self, group: str, channel: str) -> None:
        await asyncio.sleep(0)
        self.operation_loops.append(asyncio.get_running_loop())
        self.calls.append(('discard', group, channel))
        if group in self.fail_discard:
            raise RuntimeError(f'discard failed: {group}')
        self.groups[group].discard(channel)

    async def group_send(self, group: str, message: dict[str, Any]) -> None:
        await asyncio.sleep(0)
        self.operation_loops.append(asyncio.get_running_loop())
        self.calls.append(('send', group, dict(message)))
        if group in self.fail_send:
            raise RuntimeError(f'send failed: {group}')

    def subscriptions_for(self, channel: str) -> frozenset[str]:
        return frozenset(
            group for group, channels in self.groups.items() if channel in channels
        )


class SlowAddChannelLayer(FakeChannelLayer):
    """Hold one add open so foreign-thread timeout/cancellation is observable."""

    def __init__(self) -> None:
        super().__init__()
        self.add_started = asyncio.Event()
        self.release_add = asyncio.Event()

    async def group_add(self, group: str, channel: str) -> None:
        self.add_started.set()
        await self.release_add.wait()
        await super().group_add(group, channel)


class TrackingSendChannelLayer(FakeChannelLayer):
    """Track in-flight sends while preserving injectable failures."""

    def __init__(self, send_delay: float = 0.005) -> None:
        super().__init__()
        self.send_delay = send_delay
        self.active_sends = 0
        self.max_active_sends = 0

    async def group_send(self, group: str, message: dict[str, Any]) -> None:
        self.active_sends += 1
        self.max_active_sends = max(self.max_active_sends, self.active_sends)
        try:
            await asyncio.sleep(self.send_delay)
            await super().group_send(group, message)
        finally:
            self.active_sends -= 1


class CrashingSubscriptionConsumer(ChatConsumer):
    """Exercise ChatConsumer's terminal cleanup without auth or database work."""

    async def connect(self):
        self.subscriptions = SubscriptionRegistry(
            self.channel_layer,
            self.channel_name,
        )
        await self.subscriptions.add('chat_crash_test')
        await self.accept()
        raise RuntimeError('post-accept subscription crash')


def metric_value(name: str, labels: dict[str, str] | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


@pytest.mark.asyncio
async def test_add_remove_are_idempotent_and_keep_layer_in_sync():
    layer = FakeChannelLayer()
    registry = SubscriptionRegistry(layer, 'channel-1')
    active_before = metric_value('chat_active_subscriptions')

    assert await registry.add('chat_1') is True
    assert await registry.add('chat_1') is False
    assert await registry.snapshot() == frozenset({'chat_1'})
    assert layer.subscriptions_for('channel-1') == frozenset({'chat_1'})
    assert metric_value('chat_active_subscriptions') == active_before + 1

    assert await registry.remove('chat_1') is True
    assert await registry.remove('chat_1') is False
    assert await registry.snapshot() == frozenset()
    assert layer.subscriptions_for('channel-1') == frozenset()
    assert metric_value('chat_active_subscriptions') == active_before

    await registry.clear()


@pytest.mark.asyncio
async def test_sync_discards_before_adding_and_returns_completed_delta():
    layer = FakeChannelLayer()
    registry = SubscriptionRegistry(layer, 'channel-1')
    await registry.sync({'chat_1', 'chat_2'})
    layer.calls.clear()

    delta = await registry.sync({'chat_2', 'chat_3'})

    assert delta.removed == frozenset({'chat_1'})
    assert delta.added == frozenset({'chat_3'})
    assert [call[0] for call in layer.calls] == ['discard', 'add']
    assert await registry.snapshot() == frozenset({'chat_2', 'chat_3'})
    assert layer.subscriptions_for('channel-1') == await registry.snapshot()

    await registry.clear()


@pytest.mark.asyncio
async def test_partial_sync_failure_preserves_completed_layer_state():
    layer = FakeChannelLayer()
    registry = SubscriptionRegistry(layer, 'channel-1')
    await registry.sync({'chat_1', 'chat_2'})
    layer.fail_add.add('chat_4')

    with pytest.raises(RuntimeError, match='add failed: chat_4'):
        # Sorted ordering removes chat_1, adds chat_3, then fails on chat_4.
        await registry.sync({'chat_2', 'chat_3', 'chat_4'})

    assert await registry.snapshot() == frozenset({'chat_2', 'chat_3'})
    assert layer.subscriptions_for('channel-1') == await registry.snapshot()

    await registry.clear()


@pytest.mark.asyncio
async def test_snapshot_is_immutable_and_detached_from_future_changes():
    layer = FakeChannelLayer()
    registry = SubscriptionRegistry(layer, 'channel-1')
    await registry.add('chat_1')

    snapshot = await registry.snapshot()
    assert isinstance(snapshot, frozenset)
    await registry.add('chat_2')
    assert snapshot == frozenset({'chat_1'})

    await registry.clear()


@pytest.mark.asyncio
async def test_concurrent_add_remove_has_no_state_or_metric_drift():
    layer = FakeChannelLayer()
    registry = SubscriptionRegistry(layer, 'channel-1')
    active_before = metric_value('chat_active_subscriptions')
    groups = [f'chat_{index}' for index in range(100)]

    results = await asyncio.gather(
        *(registry.add(group) for group in groups),
        *(registry.add(group) for group in groups),
    )
    assert sum(results) == len(groups)
    assert await registry.snapshot() == frozenset(groups)
    assert layer.subscriptions_for('channel-1') == frozenset(groups)
    assert metric_value('chat_active_subscriptions') == active_before + len(groups)

    results = await asyncio.gather(
        *(registry.remove(group) for group in groups),
        *(registry.remove(group) for group in groups),
    )
    assert sum(results) == len(groups)
    assert await registry.snapshot() == frozenset()
    assert layer.subscriptions_for('channel-1') == frozenset()
    assert metric_value('chat_active_subscriptions') == active_before

    await registry.clear()


@pytest.mark.asyncio
async def test_concurrent_syncs_finish_as_one_complete_target_state():
    layer = FakeChannelLayer()
    registry = SubscriptionRegistry(layer, 'channel-1')
    targets = [
        {f'chat_{index}' for index in range(start, start + 20)}
        for start in range(0, 100, 10)
    ]

    await asyncio.gather(*(registry.sync(target) for target in targets))

    snapshot = await registry.snapshot()
    assert snapshot in {frozenset(target) for target in targets}
    assert layer.subscriptions_for('channel-1') == snapshot

    await registry.clear()


@pytest.mark.asyncio
async def test_calls_from_os_threads_are_marshaled_to_the_owner_event_loop():
    owner_loop = asyncio.get_running_loop()
    layer = FakeChannelLayer()
    registry = SubscriptionRegistry(layer, 'channel-1')
    barrier = threading.Barrier(8)

    def add_from_thread(index: int) -> tuple[int, bool]:
        barrier.wait(timeout=5)
        added = asyncio.run(registry.add(f'chat_{index}'))
        return threading.get_ident(), added

    results = await asyncio.gather(
        *(asyncio.to_thread(add_from_thread, index) for index in range(8))
    )

    assert all(added for _, added in results)
    assert len({thread_id for thread_id, _ in results}) > 1
    assert await registry.snapshot() == frozenset(
        f'chat_{index}' for index in range(8)
    )
    assert set(layer.operation_loops) == {owner_loop}

    remove_barrier = threading.Barrier(8)

    def dispatch_and_remove_from_thread(index: int) -> tuple[int, bool]:
        remove_barrier.wait(timeout=5)

        async def operate() -> bool:
            await registry.dispatch(
                {'type': 'thread_test', 'index': index},
                groups={f'chat_{index}'},
            )
            return await registry.remove(f'chat_{index}')

        removed = asyncio.run(operate())
        return threading.get_ident(), removed

    results = await asyncio.gather(
        *(
            asyncio.to_thread(dispatch_and_remove_from_thread, index)
            for index in range(8)
        )
    )

    assert all(removed for _, removed in results)
    assert len({thread_id for thread_id, _ in results}) > 1
    assert await registry.snapshot() == frozenset()
    assert layer.subscriptions_for('channel-1') == frozenset()
    assert set(layer.operation_loops) == {owner_loop}

    await registry.clear()


@pytest.mark.asyncio
async def test_foreign_thread_call_times_out_without_blocking_forever():
    layer = SlowAddChannelLayer()
    registry = SubscriptionRegistry(
        layer,
        'channel-1',
        thread_call_timeout=0.05,
    )

    def add_from_thread() -> tuple[RegistryError, float]:
        started_at = time.monotonic()
        try:
            asyncio.run(registry.add('chat_slow'))
        except RegistryError as exc:
            return exc, time.monotonic() - started_at
        raise AssertionError('foreign-thread add unexpectedly succeeded')

    worker = asyncio.create_task(asyncio.to_thread(add_from_thread))
    await asyncio.wait_for(layer.add_started.wait(), timeout=1)
    error, elapsed = await asyncio.wait_for(worker, timeout=1)

    assert isinstance(error, RegistryTimeoutError)
    assert elapsed < 0.5
    assert await registry.snapshot() == frozenset()
    with pytest.raises(RegistryClosedError, match='registry is closed'):
        await registry.add('chat_after_timeout')

    await registry.clear()


@pytest.mark.asyncio
async def test_clear_cancels_outstanding_foreign_thread_calls():
    layer = SlowAddChannelLayer()
    registry = SubscriptionRegistry(
        layer,
        'channel-1',
        thread_call_timeout=5,
    )

    def add_from_thread() -> RegistryError:
        try:
            asyncio.run(registry.add('chat_slow'))
        except RegistryError as exc:
            return exc
        raise AssertionError('foreign-thread add unexpectedly succeeded')

    worker = asyncio.create_task(asyncio.to_thread(add_from_thread))
    await asyncio.wait_for(layer.add_started.wait(), timeout=1)

    cleanup = await asyncio.wait_for(registry.clear(), timeout=1)
    error = await asyncio.wait_for(worker, timeout=1)

    assert isinstance(error, RegistryCancelledError)
    assert cleanup.subscriptions == frozenset()
    assert await registry.snapshot() == frozenset()


@pytest.mark.asyncio
async def test_foreign_thread_caller_cancellation_is_preserved():
    layer = SlowAddChannelLayer()
    registry = SubscriptionRegistry(
        layer,
        'channel-1',
        thread_call_timeout=5,
    )
    foreign_task_ready = threading.Event()
    foreign_call: dict[str, Any] = {}

    def add_from_thread() -> bool:
        async def add() -> bool:
            foreign_call['loop'] = asyncio.get_running_loop()
            foreign_call['task'] = asyncio.current_task()
            foreign_task_ready.set()
            try:
                await registry.add('chat_slow')
            except asyncio.CancelledError:
                return True
            return False

        return asyncio.run(add())

    worker = asyncio.create_task(asyncio.to_thread(add_from_thread))
    await asyncio.wait_for(layer.add_started.wait(), timeout=1)
    assert foreign_task_ready.is_set()

    foreign_call['loop'].call_soon_threadsafe(foreign_call['task'].cancel)
    assert await asyncio.wait_for(worker, timeout=1) is True

    layer.release_add.set()
    assert await registry.add('chat_after_cancel') is True
    await registry.clear()


@pytest.mark.asyncio
async def test_clear_returns_previous_groups_and_does_not_report_normal_cleanup():
    layer = FakeChannelLayer()
    registry = SubscriptionRegistry(layer, 'channel-1')
    await registry.sync({'chat_1', 'chat_2'})
    active_before_clear = metric_value('chat_active_subscriptions')
    leaks_before = metric_value(
        'chat_subscription_leaks_total',
        {'reason': 'discard_failed'},
    )

    result = await registry.clear()

    assert result.subscriptions == frozenset({'chat_1', 'chat_2'})
    assert result.released == result.subscriptions
    assert result.leaked == frozenset()
    assert await registry.snapshot() == frozenset()
    assert metric_value('chat_active_subscriptions') == active_before_clear - 2
    assert metric_value(
        'chat_subscription_leaks_total',
        {'reason': 'discard_failed'},
    ) == leaks_before


@pytest.mark.asyncio
async def test_clear_reports_failed_discards_without_gauge_drift():
    layer = FakeChannelLayer()
    registry = SubscriptionRegistry(layer, 'channel-1')
    await registry.sync({'chat_1', 'chat_2'})
    layer.fail_discard.add('chat_2')
    active_before_clear = metric_value('chat_active_subscriptions')
    leaks_before = metric_value(
        'chat_subscription_leaks_total',
        {'reason': 'discard_failed'},
    )

    result = await registry.clear()

    assert result.released == frozenset({'chat_1'})
    assert result.leaked == frozenset({'chat_2'})
    assert await registry.snapshot() == frozenset()
    assert layer.subscriptions_for('channel-1') == frozenset({'chat_2'})
    assert metric_value('chat_active_subscriptions') == active_before_clear - 2
    assert metric_value(
        'chat_subscription_leaks_total',
        {'reason': 'discard_failed'},
    ) == leaks_before + 1


@pytest.mark.asyncio
async def test_dispatch_reports_partial_failures_without_state_drift():
    layer = TrackingSendChannelLayer()
    registry = SubscriptionRegistry(
        layer,
        'channel-1',
        dispatch_concurrency=2,
    )
    groups = {'chat_1', 'chat_2', 'chat_3'}
    await registry.sync(groups)
    layer.fail_send.add('chat_2')

    result = await registry.dispatch({'type': 'presence_update'})

    assert result.attempted == frozenset(groups)
    assert result.sent == frozenset({'chat_1', 'chat_3'})
    assert result.failed == frozenset({'chat_2'})
    assert len(result.failures) == 1
    assert isinstance(result.failures[0].error, RuntimeError)
    assert await registry.snapshot() == frozenset(groups)
    assert layer.subscriptions_for('channel-1') == frozenset(groups)
    assert len([call for call in layer.calls if call[0] == 'send']) == 3

    await registry.clear()


@pytest.mark.asyncio
async def test_dispatch_bounds_concurrency_for_large_group_snapshots():
    layer = TrackingSendChannelLayer()
    registry = SubscriptionRegistry(
        layer,
        'channel-1',
        dispatch_concurrency=5,
    )
    groups = {f'chat_{index}' for index in range(100)}
    await registry.sync(groups)

    result = await registry.dispatch({'type': 'presence_update'})

    assert result.attempted == frozenset(groups)
    assert result.sent == frozenset(groups)
    assert result.failures == ()
    assert 1 < layer.max_active_sends <= 5
    assert await registry.snapshot() == frozenset(groups)

    await registry.clear()


@pytest.mark.asyncio
async def test_dispatch_uses_snapshot_and_supports_post_clear_offline_broadcast():
    layer = FakeChannelLayer()
    registry = SubscriptionRegistry(layer, 'channel-1')
    await registry.sync({'chat_1', 'chat_2'})

    sent = await registry.dispatch({'type': 'presence_update', 'is_online': True})
    cleanup = await registry.clear()
    sent_after_clear = await registry.dispatch(
        {'type': 'presence_update', 'is_online': False},
        groups=cleanup.subscriptions,
    )

    assert sent.sent == frozenset({'chat_1', 'chat_2'})
    assert sent.failures == ()
    assert sent_after_clear.sent == sent.sent
    assert sent_after_clear.failures == ()
    send_calls = [call for call in layer.calls if call[0] == 'send']
    assert len(send_calls) == 4
    assert {call[1] for call in send_calls} == {'chat_1', 'chat_2'}


@pytest.mark.asyncio
async def test_clear_is_idempotent_and_registry_cannot_reopen():
    layer = FakeChannelLayer()
    registry = SubscriptionRegistry(layer, 'channel-1')
    first = await registry.clear()
    second = await registry.clear()

    assert first.subscriptions == frozenset()
    assert second.subscriptions == frozenset()
    with pytest.raises(RuntimeError, match='registry is closed'):
        await registry.add('chat_1')


@pytest.mark.asyncio
async def test_consumer_crash_releases_subscriptions_and_active_gauge(settings):
    settings.CHANNEL_LAYERS = {
        'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}
    }
    active_before = metric_value('chat_active_subscriptions')
    communicator = WebsocketCommunicator(
        CrashingSubscriptionConsumer.as_asgi(),
        '/ws/chat/crash-test/',
    )

    await communicator.connect()
    with pytest.raises(RuntimeError, match='post-accept subscription crash'):
        await communicator.wait(timeout=5)

    assert metric_value('chat_active_subscriptions') == active_before
