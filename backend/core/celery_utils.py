"""
Timeout protection for Celery tasks running under --pool=threads, where
Celery's native time_limit/soft_time_limit are silently inert. 
TimeoutEnforcedTask is wired in once as the app's default Task class — tasks 
just declare the native `time_limit=N` kwarg; no per-task decorator or import.
"""
import concurrent.futures
import logging
import os

from celery import Task

logger = logging.getLogger(__name__)


class TaskTimeoutError(TimeoutError):
    """Raised by TimeoutEnforcedTask when a task exceeds its time budget."""


class TimeoutEnforcedTask(Task):
    """
    Default Task class for this app (wired in backend/backend/celery.py).
    Declare a timeout on any task exactly like you'd declare Celery's own
    native time_limit — no extra import, no per-task decorator:

        @shared_task(bind=True, time_limit=60)
        def my_task(self, ...):
            ...

    Tasks with no `time_limit` set run unprotected, same as before this
    class existed.
    """

    def __call__(self, *args, **kwargs):
        seconds = self.time_limit
        if not seconds or os.environ.get('PYTEST_CURRENT_TEST'):
            return self.run(*args, **kwargs)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.run, *args, **kwargs)
        try:
            result = future.result(timeout=seconds)
        except concurrent.futures.TimeoutError:
            logger.error(
                "%s exceeded its %ss timeout — abandoning. The Celery "
                "worker is freed to pick up new work, but the "
                "underlying call may still be running in the "
                "background (Python cannot forcibly stop a thread).",
                self.name,
                seconds,
            )
            # wait=False: do NOT block here waiting for the orphaned
            # thread to finish — that would defeat the entire point of
            # timing out. Let it run its course in the background.
            executor.shutdown(wait=False)
            raise TaskTimeoutError(
                f"{self.name} exceeded {seconds}s timeout"
            ) from None
        except BaseException:
            # self.run() itself raised (a real error, not a timeout). It
            # has already finished running by the time future.result()
            # re-raises here, so this shutdown is fast, not a wait on
            # still-running work.
            executor.shutdown(wait=True)
            raise
        else:
            executor.shutdown(wait=True)
            return result