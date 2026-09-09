import math
import os
from time import perf_counter
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status

from task.models import Task, TaskHierarchy


def _tasks_from_response(response):
    data = response.data
    return data.get('results', data) if isinstance(data, dict) else data


@pytest.mark.django_db
@pytest.mark.parametrize('include_subtasks', [None, 'false', 'true', 'TRUE'])
def test_parent_prefetch_only_runs_when_subtasks_requested(
    authenticated_client, project, user, include_subtasks,
):
    user.active_project = project
    user.save(update_fields=['active_project'])
    parent = Task.objects.create(
        summary='Parent', type='asset', project=project, owner=user, created_by=user,
    )
    child = Task.objects.create(
        summary='Child', type='asset', project=project, owner=user,
        created_by=user, is_subtask=True,
    )
    TaskHierarchy.objects.create(parent_task=parent, child_task=child)
    params = {} if include_subtasks is None else {'include_subtasks': include_subtasks}
    with CaptureQueriesContext(connection) as queries:
        response = authenticated_client.get(reverse('task-list'), params)
    assert response.status_code == 200
    # Exclude the main task query's hierarchy COUNT join: only count direct
    # hierarchy SELECTs, which should be the single prefetch (or none).
    hierarchy_queries = [q for q in queries if 'FROM "task_hierarchies"' in q['sql']]
    includes_children = include_subtasks in ('true', 'TRUE')
    assert len(hierarchy_queries) == int(includes_children)
    payloads = {task['id']: task for task in _tasks_from_response(response)}
    assert set(payloads) == ({parent.id, child.id} if includes_children else {parent.id})
    assert payloads[parent.id]['parent_relationship'] is None
    if includes_children:
        assert payloads[child.id]['parent_relationship'][0]['parent_task_id'] == parent.id


@pytest.mark.django_db
@pytest.mark.parametrize('scenario', [
    'empty_list', 'no_parent', 'missing_hierarchy',
    'parent_outside_page', 'shared_parent',
])
def test_task_list_hierarchy_edge_cases(
    authenticated_client, project, user, monkeypatch, scenario,
):
    from rest_framework.pagination import PageNumberPagination

    user.active_project = project
    user.save(update_fields=['active_project'])
    monkeypatch.setattr(PageNumberPagination, 'page_size', 20)

    def create_task(summary, **kwargs):
        return Task.objects.create(
            summary=summary, type='asset', project=project,
            owner=user, created_by=user, **kwargs,
        )

    expected = {}
    parent = None
    if scenario in ('no_parent', 'missing_hierarchy'):
        task = create_task('Standalone task', is_subtask=scenario == 'missing_hierarchy')
        expected[task.id] = None
    elif scenario in ('parent_outside_page', 'shared_parent'):
        parent = create_task('Parent', order_in_project=10)
        relation = [{
            'parent_task_id': parent.id,
            'parent_task_slug': parent.slug,
            'parent_task_summary': parent.summary,
        }]
        for index in range(3 if scenario == 'shared_parent' else 1):
            child = create_task(f'Child {index}', is_subtask=True, order_in_project=0)
            TaskHierarchy.objects.create(parent_task=parent, child_task=child)
            expected[child.id] = relation
        if scenario == 'parent_outside_page':
            monkeypatch.setattr(PageNumberPagination, 'page_size', 1)
        else:
            expected[parent.id] = None

    with CaptureQueriesContext(connection) as queries:
        response = authenticated_client.get(
            reverse('task-list'), {'include_subtasks': 'true'},
        )
    assert response.status_code == status.HTTP_200_OK
    assert len(queries) <= 12, [q['sql'] for q in queries]
    tasks = _tasks_from_response(response)
    assert {task['id']: task['parent_relationship'] for task in tasks} == expected
    if scenario == 'parent_outside_page':
        assert response.data['count'] == 2
        assert response.data['next'] is not None
        assert parent.id not in {task['id'] for task in tasks}
    if scenario == 'missing_hierarchy':
        assert tasks[0]['is_subtask'] is True


@pytest.mark.django_db
@pytest.mark.parametrize('child_count', [1, 19, 199])
def test_task_list_prefetches_parent_hierarchy_within_query_budget(
    authenticated_client,
    project,
    user,
    monkeypatch,
    child_count,
):
    """MED-296: hierarchy lookups must not grow by one query per subtask."""
    user.active_project = project
    user.save(update_fields=['active_project'])
    from rest_framework.pagination import PageNumberPagination
    monkeypatch.setattr(PageNumberPagination, 'page_size', child_count + 1)

    parent = Task.objects.create(
        summary='Campaign launch',
        type='asset',
        project=project,
        owner=user,
        created_by=user,
    )
    children = []
    for index in range(child_count):
        child = Task.objects.create(
            summary=f'Campaign subtask {index}',
            type='asset',
            project=project,
            owner=user,
            created_by=user,
            is_subtask=True,
        )
        TaskHierarchy.objects.create(parent_task=parent, child_task=child)
        children.append(child)

    with CaptureQueriesContext(connection) as queries:
        response = authenticated_client.get(
            reverse('task-list'),
            {'include_subtasks': 'true'},
        )

    assert response.status_code == status.HTTP_200_OK
    assert len(queries) <= 12, [q['sql'] for q in queries]

    tasks = _tasks_from_response(response)
    assert len(tasks) == child_count + 1
    child_ids = {child.id for child in children}
    child_payloads = {
        task['id']: task
        for task in tasks
        if task['id'] in child_ids
    }
    assert len(child_payloads) == child_count
    assert all(
        payload['parent_relationship'] == [{
            'parent_task_id': parent.id,
            'parent_task_slug': parent.slug,
            'parent_task_summary': parent.summary,
        }]
        for payload in child_payloads.values()
    )

    if child_count == 199 and os.environ.get('MED296_PROFILE') == '1':
        # Diagnostic only: profiler overhead is not part of the p95 benchmark.
        import cProfile
        import pstats
        from task.views import TaskViewSet

        original = TaskViewSet.get_queryset

        def legacy_queryset(view):
            return original(view).prefetch_related(None)

        for mode, queryset_method in [('before', legacy_queryset), ('after', original)]:
            sql_times = []

            def time_sql(execute, sql, params, many, context):
                started = perf_counter()
                try:
                    return execute(sql, params, many, context)
                finally:
                    sql_times.append(perf_counter() - started)

            profiler = cProfile.Profile()
            with patch.object(TaskViewSet, 'get_queryset', queryset_method):
                with connection.execute_wrapper(time_sql):
                    started = perf_counter()
                    profiler.enable()
                    measured = authenticated_client.get(
                        reverse('task-list'), {'include_subtasks': 'true'},
                    )
                    measured.content
                    profiler.disable()
                    elapsed = perf_counter() - started
            assert measured.status_code == 200
            assert measured.data == response.data
            print(f'MED296 PROFILE {mode}: rows={len(tasks)} '
                  f'total={elapsed:.4f}s sql={sum(sql_times):.4f}s '
                  f'queries={len(sql_times)}')
            pstats.Stats(profiler).strip_dirs().sort_stats('cumulative').print_stats(30)
            pstats.Stats(profiler).strip_dirs().sort_stats('tottime').print_stats(15)

    if child_count == 199 and os.environ.get('MED296_BENCHMARK') == '1':
        # Opt-in endpoint benchmark; no production pagination changes and no
        # synthetic database delay. Alternate order to reduce warm-cache bias.
        from task.views import TaskViewSet
        original = TaskViewSet.get_queryset

        def legacy_queryset(view):
            return original(view).prefetch_related(None)

        timings = {'before': [], 'after': []}
        for iteration in range(34):
            modes = ('before', 'after') if iteration % 2 else ('after', 'before')
            for mode in modes:
                with patch.object(
                    TaskViewSet, 'get_queryset',
                    legacy_queryset if mode == 'before' else original,
                ):
                    started = perf_counter()
                    measured = authenticated_client.get(
                        reverse('task-list'), {'include_subtasks': 'true'},
                    )
                    measured.content
                    elapsed = perf_counter() - started
                assert measured.status_code == 200
                assert measured.data == response.data
                if iteration >= 4:
                    timings[mode].append(elapsed)
        p95 = {
            mode: sorted(samples)[math.ceil(len(samples) * .95) - 1]
            for mode, samples in timings.items()
        }
        improvement = 1 - p95['after'] / p95['before']
        print(f'MED296 p95 before={p95["before"]:.4f}s '
              f'after={p95["after"]:.4f}s improvement={improvement:.2%}')
        assert improvement > .5
