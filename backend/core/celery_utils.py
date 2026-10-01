"""
Timeout protection for Celery tasks running under a thread-pool worker.

Celery's built-in `time_limit`/`soft_time_limit` are enforced via OS
signals sent to the worker process — that only works under the `prefork`
pool. Every worker in this deployment runs `--pool=threads` (see
docker-compose.dev.yml), where all concurrent tasks share one process, and
the OS has no way to interrupt a single thread without affecting the
others. Under threads, those limits are configured but silently never
enforced (confirmed by dispatching a task that deliberately sleeps past
its configured soft/hard limit — it ran to completion untouched). See
MED-400.

`enforce_timeout` is the replacement. It runs the wrapped function in a
helper thread and stops waiting after `seconds` — this is NOT a true
kill; Python cannot forcibly stop a running thread, so a task that hangs
past its timeout keeps running as an orphaned thread in the background.
What this *does* guarantee is that the Celery worker thread that called
it is freed immediately, so a hang can no longer pin a worker slot
forever — it degrades to "wastes some CPU/memory in the background"
instead of "the queue stops draining."

Under pytest, the wrapper below runs the function directly on the calling
thread instead of handing it to a helper thread — see the
PYTEST_CURRENT_TEST check in enforce_timeout. Tasks that look up data a
test inserted inside its own (uncommitted) transaction would otherwise
see an empty result, because a helper thread gets its own DB connection
that can't see that transaction.
"""
import concurrent.futures
import functools
import logging
import os

logger = logging.getLogger(__name__)


class TaskTimeoutError(TimeoutError):
    """Raised by enforce_timeout() when a task exceeds its time budget."""


def enforce_timeout(seconds):
    """
    Decorator: run the wrapped function in a helper thread and give up
    waiting for it after `seconds`. Raises TaskTimeoutError if the budget
    is exceeded; the underlying call keeps running in the background
    (Python cannot forcibly stop a thread), but the caller — the Celery
    worker thread — is freed immediately either way.

    Apply this inside @app.task, so it wraps the task body:

        @app.task(bind=True)
        @enforce_timeout(seconds=10)
        def my_task(self, ...):
            ...

    Choose `seconds` to match the task's expected duration, not the
    worker's overall budget — see CELERY_TASK_ANNOTATIONS in settings.py
    for the tiers already agreed for the chat.* queues.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Under pytest, run synchronously on the calling thread. Celery's
            # task.apply() executes tasks directly in the calling thread/connection
            # (that's how Django TestCase's per-test transaction stays visible to
            # the task body). Moving the real work into a helper thread — as we do
            # in production — hands it a brand-new DB connection that can't see
            # the test's uncommitted transaction, so DB lookups inside the task
            # silently come back empty and the task exits early instead of
            # exercising the code path the test actually wants to check. There's
            # no real worker slot to protect in a test process anyway, so timeout
            # protection is meaningless here — just call the function directly.
            if os.environ.get('PYTEST_CURRENT_TEST'):
                return func(*args, **kwargs)

            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(func, *args, **kwargs)
            try:
                result = future.result(timeout=seconds)
            except concurrent.futures.TimeoutError:
                logger.error(
                    "%s exceeded its %ss timeout — abandoning. The Celery "
                    "worker is freed to pick up new work, but the "
                    "underlying call may still be running in the "
                    "background (Python cannot forcibly stop a thread).",
                    func.__qualname__,
                    seconds,
                )
                # wait=False: do NOT block here waiting for the orphaned
                # thread to finish — that would defeat the entire point of
                # timing out. Let it run its course in the background.
                executor.shutdown(wait=False)
                raise TaskTimeoutError(
                    f"{func.__qualname__} exceeded {seconds}s timeout"
                ) from None
            except BaseException:
                # func() itself raised (a real error, not a timeout). It
                # has already finished running by the time future.result()
                # re-raises here, so this shutdown is fast, not a wait on
                # still-running work.
                executor.shutdown(wait=True)
                raise
            else:
                executor.shutdown(wait=True)
                return result
        return wrapper
    return decorator
