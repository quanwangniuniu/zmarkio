"""Concurrency-safe bookkeeping for one chat WebSocket's group subscriptions.

The registry owns chat-group Channel Layer operations and the matching local
state. It intentionally knows nothing about Django models, authentication or
the WebSocket protocol, so its concurrency and failure behaviour can be tested
with a small in-memory channel-layer stand-in.
"""

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Future as ThreadFuture
from dataclasses import dataclass
from typing import Any, Collection, Mapping, Protocol, TypeVar

from .metrics import chat_active_subscriptions, chat_subscription_leaks_total


logger = logging.getLogger(__name__)
T = TypeVar('T')
DEFAULT_THREAD_CALL_TIMEOUT_SECONDS = 5.0
DEFAULT_DISPATCH_CONCURRENCY = 25


class RegistryError(RuntimeError):
    """Base error for subscription-registry lifecycle failures."""


class RegistryClosedError(RegistryError):
    """Raised when a mutating call targets a terminal registry."""


class RegistryUnavailableError(RegistryError):
    """Raised when the registry owner event loop cannot accept work."""


class RegistryTimeoutError(RegistryError):
    """Raised when a foreign-loop call outlives its bounded wait."""


class RegistryCancelledError(RegistryError):
    """Raised when closing the registry cancels submitted foreign-loop work."""


class ChannelLayerProtocol(Protocol):
    """The small part of a Channels layer used by the registry."""

    async def group_add(self, group: str, channel: str) -> None: ...

    async def group_discard(self, group: str, channel: str) -> None: ...

    async def group_send(self, group: str, message: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class SubscriptionDelta:
    """Chat-group changes completed by one successful sync."""

    added: frozenset[str]
    removed: frozenset[str]


@dataclass(frozen=True)
class CleanupResult:
    """Outcome of terminal registry cleanup for one connection."""

    subscriptions: frozenset[str]
    released: frozenset[str]
    leaked: frozenset[str]


@dataclass(frozen=True)
class DispatchFailure:
    """One Channel Layer target that rejected a dispatch."""

    group_name: str
    error: Exception


@dataclass(frozen=True)
class DispatchResult:
    """Complete per-target outcome of one bounded dispatch."""

    attempted: frozenset[str]
    sent: frozenset[str]
    failures: tuple[DispatchFailure, ...]

    @property
    def failed(self) -> frozenset[str]:
        return frozenset(failure.group_name for failure in self.failures)


class SubscriptionRegistry:
    """Thread-safe chat-group subscriptions for one channel connection.

    Operations from foreign threads are marshalled to the event loop that
    created the registry. Channel Layer I/O and local state changes are then
    serialized under one lock. State is updated after each successful I/O
    operation, so a partial failure never makes the registry claim an operation
    happened when the layer rejected it.
    """

    def __init__(
        self,
        channel_layer: ChannelLayerProtocol,
        channel_name: str,
        *,
        thread_call_timeout: float = DEFAULT_THREAD_CALL_TIMEOUT_SECONDS,
        dispatch_concurrency: int = DEFAULT_DISPATCH_CONCURRENCY,
    ) -> None:
        thread_call_timeout = float(thread_call_timeout)
        dispatch_concurrency = int(dispatch_concurrency)
        if thread_call_timeout <= 0:
            raise ValueError('thread_call_timeout must be greater than zero')
        if dispatch_concurrency <= 0:
            raise ValueError('dispatch_concurrency must be greater than zero')

        self._channel_layer = channel_layer
        self._channel_name = channel_name
        self._thread_call_timeout = thread_call_timeout
        self._dispatch_concurrency = dispatch_concurrency
        self._groups: set[str] = set()
        try:
            self._owner_loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError(
                'subscription registry must be created inside a running event loop'
            ) from exc
        self._lock = asyncio.Lock()
        self._closed = False
        self._terminal = threading.Event()
        self._cross_thread_lock = threading.Lock()
        self._cross_thread_futures: set[ThreadFuture[Any]] = set()

    async def add(self, group_name: str) -> bool:
        """Subscribe this connection to ``group_name`` if it is not present."""
        return await self._run_on_owner(
            lambda: self._add(group_name),
            operation_name='add',
        )

    async def _add(self, group_name: str) -> bool:
        async with self._lock:
            self._ensure_open()
            return await self._add_locked(group_name)

    async def remove(self, group_name: str) -> bool:
        """Discard this connection from ``group_name`` if it is present."""
        return await self._run_on_owner(
            lambda: self._remove(group_name),
            operation_name='remove',
        )

    async def _remove(self, group_name: str) -> bool:
        async with self._lock:
            self._ensure_open()
            return await self._remove_locked(group_name)

    async def sync(self, allowed: Collection[str]) -> SubscriptionDelta:
        """Make tracked subscriptions exactly match ``allowed``.

        Revocations run before joins. If a layer call raises, successfully
        completed operations remain reflected in ``snapshot()`` and the error
        propagates so the consumer can close an untrustworthy connection.
        """
        target = frozenset(allowed)
        return await self._run_on_owner(
            lambda: self._sync(target),
            operation_name='sync',
        )

    async def _sync(self, target: frozenset[str]) -> SubscriptionDelta:
        async with self._lock:
            self._ensure_open()
            to_remove = self._groups - target
            to_add = target - self._groups
            removed: set[str] = set()
            added: set[str] = set()

            for group_name in sorted(to_remove):
                if await self._remove_locked(group_name):
                    removed.add(group_name)
            for group_name in sorted(to_add):
                if await self._add_locked(group_name):
                    added.add(group_name)

            return SubscriptionDelta(
                added=frozenset(added),
                removed=frozenset(removed),
            )

    async def clear(self) -> CleanupResult:
        """Best-effort terminal cleanup of every tracked subscription.

        All groups are attempted even if one discard fails. Failed groups are
        reported as possible leaks, while the local registry and active gauge
        are always released because this connection no longer owns them.
        Calling ``clear`` more than once is harmless.
        """
        self._begin_close()
        return await self._run_on_owner(
            self._clear,
            operation_name='clear',
            allow_terminal=True,
        )

    async def _clear(self) -> CleanupResult:
        async with self._lock:
            if self._closed:
                return CleanupResult(frozenset(), frozenset(), frozenset())

            self._closed = True
            subscriptions = frozenset(self._groups)
            released: set[str] = set()
            leaked: set[str] = set()

            for group_name in sorted(subscriptions):
                try:
                    await self._channel_layer.group_discard(
                        group_name,
                        self._channel_name,
                    )
                except Exception:
                    leaked.add(group_name)
                    logger.exception(
                        'Failed to discard chat subscription during cleanup: group=%s',
                        group_name,
                    )
                else:
                    released.add(group_name)
                finally:
                    # The registry is terminal after clear. A failed discard is
                    # represented by the leak counter, not by a permanently
                    # drifting gauge owned by an object that is about to die.
                    self._groups.discard(group_name)
                    chat_active_subscriptions.dec()

            if leaked:
                chat_subscription_leaks_total.labels(
                    reason='discard_failed'
                ).inc(len(leaked))

            return CleanupResult(
                subscriptions=subscriptions,
                released=frozenset(released),
                leaked=frozenset(leaked),
            )

    async def snapshot(self) -> frozenset[str]:
        """Return an immutable, consistent view of current subscriptions."""
        return await self._run_on_owner(
            self._snapshot,
            operation_name='snapshot',
            allow_terminal=True,
        )

    async def _snapshot(self) -> frozenset[str]:
        async with self._lock:
            return frozenset(self._groups)

    async def dispatch(
        self,
        event: Mapping[str, Any],
        *,
        groups: Collection[str] | None = None,
    ) -> DispatchResult:
        """Publish ``event`` to tracked groups, or an explicit group snapshot.

        Explicit groups support disconnect ordering: the connection first
        leaves its groups, then publishes its offline presence to the groups it
        just left without reintroducing a subscription. Every target is
        attempted with bounded concurrency and failures are returned rather
        than allowing one broken group to cancel the remaining sends.
        """
        message = dict(event)
        targets = None if groups is None else frozenset(groups)
        return await self._run_on_owner(
            lambda: self._dispatch(message, groups=targets),
            operation_name='dispatch',
            # Disconnect publishes offline presence to the immutable audience
            # returned by clear(), after the registry is terminal.
            allow_terminal=targets is not None,
        )

    async def _dispatch(
        self,
        event: dict[str, Any],
        *,
        groups: frozenset[str] | None,
    ) -> DispatchResult:
        async with self._lock:
            if groups is None:
                self._ensure_open()
            targets = frozenset(self._groups if groups is None else groups)
            ordered_targets = sorted(targets)
            semaphore = asyncio.Semaphore(self._dispatch_concurrency)

            async def bounded_send(group_name: str) -> None:
                async with semaphore:
                    await self._channel_layer.group_send(group_name, event)

            outcomes = await asyncio.gather(
                *(bounded_send(group_name) for group_name in ordered_targets),
                return_exceptions=True,
            )

            sent: set[str] = set()
            failures: list[DispatchFailure] = []
            for group_name, outcome in zip(ordered_targets, outcomes):
                if isinstance(outcome, Exception):
                    failures.append(
                        DispatchFailure(group_name=group_name, error=outcome)
                    )
                elif isinstance(outcome, BaseException):
                    # Task cancellation and other control-flow exceptions must
                    # retain their normal semantics rather than becoming a
                    # per-target publish failure.
                    raise outcome
                else:
                    sent.add(group_name)

            return DispatchResult(
                attempted=targets,
                sent=frozenset(sent),
                failures=tuple(failures),
            )

    async def _run_on_owner(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        operation_name: str,
        allow_terminal: bool = False,
    ) -> T:
        """Run state and Channel Layer work on the registry's owner loop.

        Normal ASGI calls take the direct path. A caller in another OS thread
        submits through asyncio's thread-safe loop bridge, keeping the registry
        thread-safe without moving a Channels client to a foreign event loop.
        """
        if self._terminal.is_set() and not allow_terminal:
            raise RegistryClosedError('subscription registry is closed')

        current_loop = asyncio.get_running_loop()
        if current_loop is self._owner_loop:
            return await operation()
        if self._owner_loop.is_closed() or not self._owner_loop.is_running():
            raise RegistryUnavailableError(
                'subscription registry owner event loop is unavailable'
            )

        awaitable = operation()
        try:
            future = asyncio.run_coroutine_threadsafe(
                awaitable,
                self._owner_loop,
            )
        except RuntimeError as exc:
            close = getattr(awaitable, 'close', None)
            if close is not None:
                close()
            raise RegistryUnavailableError(
                'subscription registry owner event loop rejected work'
            ) from exc

        self._track_cross_thread_future(
            future,
            allow_terminal=allow_terminal,
        )
        try:
            wrapped = asyncio.wrap_future(future)
            try:
                return await asyncio.wait_for(
                    asyncio.shield(wrapped),
                    timeout=self._thread_call_timeout,
                )
            except asyncio.TimeoutError as exc:
                future.cancel()
                self._begin_close()
                logger.error(
                    'Timed out waiting %.3fs for subscription registry owner loop: '
                    'operation=%s',
                    self._thread_call_timeout,
                    operation_name,
                )
                raise RegistryTimeoutError(
                    f'timed out waiting for registry owner loop during '
                    f'{operation_name}'
                ) from exc
            except asyncio.CancelledError as exc:
                owner_work_cancelled = future.cancelled()
                future.cancel()
                if not owner_work_cancelled:
                    # Preserve cooperative cancellation initiated by this
                    # caller. Shielding the owner-loop future keeps this case
                    # distinguishable on Python versions before Task.cancelling().
                    raise
                self._begin_close()
                logger.error(
                    'Subscription registry owner-loop work was cancelled: '
                    'operation=%s',
                    operation_name,
                )
                raise RegistryCancelledError(
                    f'registry owner-loop work was cancelled during '
                    f'{operation_name}'
                ) from exc
        finally:
            self._forget_cross_thread_future(future)

    def _track_cross_thread_future(
        self,
        future: ThreadFuture[Any],
        *,
        allow_terminal: bool,
    ) -> None:
        """Track submitted work so terminal cleanup can cancel it promptly."""
        should_cancel = False
        with self._cross_thread_lock:
            if self._terminal.is_set() and not allow_terminal:
                should_cancel = True
            else:
                self._cross_thread_futures.add(future)
        if should_cancel:
            future.cancel()
            raise RegistryClosedError('subscription registry is closed')
        future.add_done_callback(self._forget_cross_thread_future)

    def _forget_cross_thread_future(self, future: ThreadFuture[Any]) -> None:
        with self._cross_thread_lock:
            self._cross_thread_futures.discard(future)

    def _begin_close(self) -> None:
        """Mark terminal once and cancel cross-thread work already submitted."""
        with self._cross_thread_lock:
            if self._terminal.is_set():
                return
            self._terminal.set()
            futures = tuple(self._cross_thread_futures)
        for future in futures:
            future.cancel()

    async def _add_locked(self, group_name: str) -> bool:
        if group_name in self._groups:
            return False
        await self._channel_layer.group_add(group_name, self._channel_name)
        self._groups.add(group_name)
        chat_active_subscriptions.inc()
        return True

    async def _remove_locked(self, group_name: str) -> bool:
        if group_name not in self._groups:
            return False
        await self._channel_layer.group_discard(group_name, self._channel_name)
        self._groups.remove(group_name)
        chat_active_subscriptions.dec()
        return True

    def _ensure_open(self) -> None:
        if self._closed or self._terminal.is_set():
            raise RegistryClosedError('subscription registry is closed')
