"""
Regression tests for TimeoutEnforcedTask (core.celery_utils).

TimeoutEnforcedTask runs bodies inline while PYTEST_CURRENT_TEST is set, and
pytest re-sets that variable at the start of every phase, so each test
removes it in its own body (not in a fixture) to exercise the executor path.
"""
import threading
import time

import pytest
from celery import current_task
from celery.exceptions import Retry
from django.db import connection
from django.db.backends.signals import connection_created

from backend.celery import app
from core.celery_utils import TaskTimeoutError, TimeoutEnforcedTask

pytestmark = pytest.mark.django_db


def _select_one():
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        return cursor.fetchone()[0]


@app.task(bind=True, time_limit=5, name='core.tests.celery_utils.succeeds')
def succeeds(self):
    return _select_one()


@app.task(bind=True, time_limit=5, name='core.tests.celery_utils.fails')
def fails(self):
    _select_one()
    raise ValueError('synthetic failure')


@app.task(bind=True, time_limit=1, name='core.tests.celery_utils.blocks')
def blocks(self, release):
    _select_one()
    release.wait(10)
    return 'late'


@app.task(bind=True, time_limit=5, name='core.tests.celery_utils.reports_context')
def reports_context(self):
    return {
        'thread': threading.get_ident(),
        'request_id': self.request.id,
        'called_directly': self.request.called_directly,
        'current_task_is_self': current_task._get_current_object() is self,
    }


@app.task(bind=True, time_limit=5, max_retries=3, name='core.tests.celery_utils.retries')
def retries(self):
    raise self.retry(exc=ValueError('transient'), countdown=0)


def _use_executor_path(monkeypatch):
    monkeypatch.delenv('PYTEST_CURRENT_TEST', raising=False)


def _run_as_worker(task, *args):
    """Call the task the way the worker's tracer does: request pushed on this thread."""
    task.push_request(id=f'req-{task.name}', called_directly=False)
    try:
        return task(*args)
    finally:
        task.pop_request()


@pytest.fixture
def executor_connections():
    """Every DB connection opened on a thread other than the test's own thread."""
    created = []
    test_thread = threading.get_ident()

    def on_connection_created(sender, connection, **kwargs):
        if threading.get_ident() != test_thread:
            created.append(connection)

    connection_created.connect(on_connection_created, weak=False)
    try:
        yield created
    finally:
        connection_created.disconnect(on_connection_created)
        for wrapper in created:
            if wrapper.connection is not None:
                wrapper.inc_thread_sharing()
                wrapper.close()


def test_task_class_is_the_app_default():
    assert isinstance(succeeds, TimeoutEnforcedTask)


def test_executor_thread_connection_closed_after_success(monkeypatch, executor_connections):
    _use_executor_path(monkeypatch)
    assert _run_as_worker(succeeds) == 1
    assert len(executor_connections) == 1, 'body should run on the executor thread'
    assert executor_connections[0].connection is None


def test_executor_thread_connection_closed_after_exception(monkeypatch, executor_connections):
    _use_executor_path(monkeypatch)
    with pytest.raises(ValueError, match='synthetic failure'):
        _run_as_worker(fails)
    assert len(executor_connections) == 1
    assert executor_connections[0].connection is None


def test_executor_thread_connection_closed_when_body_outlives_timeout(monkeypatch, executor_connections):
    _use_executor_path(monkeypatch)
    release = threading.Event()
    try:
        with pytest.raises(TaskTimeoutError):
            _run_as_worker(blocks, release)
        assert len(executor_connections) == 1
        assert executor_connections[0].connection is not None  # body still running
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while executor_connections[0].connection is not None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert executor_connections[0].connection is None


def test_executor_thread_sees_worker_request_context(monkeypatch):
    _use_executor_path(monkeypatch)
    seen = _run_as_worker(reports_context)
    assert seen['thread'] != threading.get_ident()
    assert seen['request_id'] == 'req-core.tests.celery_utils.reports_context'
    assert seen['called_directly'] is False
    assert seen['current_task_is_self'] is True


def test_executor_thread_can_schedule_retry(monkeypatch):
    _use_executor_path(monkeypatch)
    published = []
    monkeypatch.setattr(retries, 'apply_async', lambda *args, **kwargs: published.append(kwargs))
    with pytest.raises(Retry):
        _run_as_worker(retries)
    assert len(published) == 1
