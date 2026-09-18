"""
Single entry for agent commits that affect systems outside the agent sandbox.

Toggle off: commits run immediately. Toggle on: persist AgentPendingExternalApproval
and pause (workflow) or return events for the client (non-workflow).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import requests as http_requests

logger = logging.getLogger(__name__)

KIND_TASK = 'task'
KIND_DECISION_TREE = 'decision_tree'
KIND_MIRO_BOARD = 'miro_board'
KIND_CALENDAR_EVENT = 'calendar_event'
KIND_FORWARD_MESSAGE = 'forward_message'
KIND_CUSTOM_API = 'custom_api'
KIND_EMAIL = 'email'


@dataclass
class ExternalCommitResult:
    paused: bool
    sse_events: list
    pending_id: str | None
    # When not paused: merged output for workflow executor / orchestrator
    output_data: dict | None
    workflow_run_patch: dict | None  # e.g. created_tasks, miro ids — applied by caller


def _project_destination_options(project) -> list[dict]:
    return [
        {
            'id': str(project.id),
            'label': getattr(project, 'name', str(project.id)),
            'value': {'project_id': str(project.id)},
        }
    ]


def _user_accessible_projects(user):
    from core.models import Project, ProjectMember

    ids = ProjectMember.objects.filter(user=user, is_active=True).values_list(
        'project_id', flat=True
    )
    return list(
        Project.objects.filter(id__in=ids, is_deleted=False).order_by('name')[:50]
    )


def build_destination_options(
    *,
    kind: str,
    session,
    project,
    user,
    draft: dict,
    commit_context: dict,
) -> tuple[list[dict], dict | None]:
    """Return (options, default_destination)."""
    if kind in (KIND_TASK, KIND_DECISION_TREE, KIND_MIRO_BOARD):
        # Destination is always the session project for tasks + miro boards.
        # Keep default destination for internal commit plumbing, but do not offer options.
        default = {'project_id': str(project.id)}
        return [], default

    if kind == KIND_CALENDAR_EVENT:
        from calendars.models import Calendar as CalendarModel

        org_id = commit_context.get('organization_id')
        if not org_id:
            return [], None
        cals = list(
            CalendarModel.objects.filter(
                organization_id=org_id,
                owner=user,
                is_deleted=False,
            ).order_by('-is_primary', 'name')[:30]
        )
        opts = [
            {'id': str(c.id), 'label': c.name, 'value': {'calendar_id': str(c.id)}}
            for c in cals
        ]
        primary = next((c for c in cals if getattr(c, 'is_primary', False)), cals[0] if cals else None)
        default = {'calendar_id': str(primary.id)} if primary else None
        return opts, default

    if kind == KIND_FORWARD_MESSAGE:
        forwards = draft.get('forwards') or []
        opts = []
        for i, f in enumerate(forwards):
            un = (f.get('username') or '').strip()
            if not un:
                continue
            opts.append({
                'id': f'{un}:{i}',
                'label': un,
                'value': {'username': un},
            })
        all_usernames = [o['value']['username'] for o in opts]
        default = {'usernames': all_usernames} if all_usernames else None
        return opts, default

    if kind == KIND_CUSTOM_API:
        return [{'id': 'default', 'label': 'External API', 'value': {}}], {}

    if kind == KIND_EMAIL:
        return [], None

    return [], None


def _commit_task(orchestrator, draft: dict, destination: dict | None, commit_context: dict):
    from django.contrib.contenttypes.models import ContentType

    from decision.models import Decision
    from task.models import Task

    from .generation_registry import validate_recommended_tasks

    project = orchestrator.project

    tasks_data = validate_recommended_tasks(draft.get('recommended_tasks') or [])
    if not tasks_data:
        raise ValueError('No tasks in draft')

    decision = None
    did = commit_context.get('decision_id')
    if did:
        try:
            decision = Decision.objects.get(id=did, project=project)
        except Decision.DoesNotExist:
            decision = None

    if decision:
        decision_ct = ContentType.objects.get_for_model(Decision)
        link_kwargs = {'content_type': decision_ct, 'object_id': str(decision.id)}
        desc_suffix = f' (Decision: {decision.title})'
    else:
        link_kwargs = {}
        desc_suffix = ''

    input_data = commit_context.get('input_data') or {}
    analysis = commit_context.get('analysis_result') or input_data.get('analysis_result')

    created_ids = []
    created_tasks = []
    for idx, task_data in enumerate(tasks_data):
        original_index = task_data.get('index', idx)
        try:
            original_index = int(original_index)
        except Exception:
            original_index = idx
        summary = (task_data.get('summary') or 'AI Agent Generated Task')[:255]
        ai_description = (task_data.get('description') or '').strip()
        description = ai_description or f'Auto-generated from AI analysis{desc_suffix}'
        task = Task.objects.create(
            summary=summary,
            description=description,
            type=task_data.get('type', 'optimization'),
            priority=task_data.get('priority', 'MEDIUM'),
            project=project,
            created_by=orchestrator.user,
            owner=orchestrator.user,
            **link_kwargs,
        )
        created_ids.append(task.id)
        created_tasks.append(
            {
                'index': original_index,
                'task_id': task.id,
                'task_slug': task.slug,
                'summary': summary,
            }
        )

    wf_patch = {'created_tasks': created_ids}
    out = {**input_data, 'analysis_result': analysis}
    sse = [
        {
            'type': 'task_created',
            'content': f'Created {len(created_ids)} tasks.',
            'data': {
                'task_ids': created_ids,
                'created_tasks': created_tasks,
                'decision_id': decision.id if decision else None,
            },
        }
    ]
    return out, sse, wf_patch


def _commit_decision_tree(orchestrator, draft: dict, destination: dict | None, commit_context: dict):
    from .decision_tree_service import commit_decision_tree

    tree = draft.get('recommended_decision_tree') or {}
    nodes = tree.get('nodes') or []
    if not nodes:
        raise ValueError('No decision tree nodes in draft')

    session = orchestrator.session
    result = commit_decision_tree(
        project=orchestrator.project,
        user=orchestrator.user,
        tree=tree,
        agent_session_id=getattr(session, 'id', None),
    )

    input_data = commit_context.get('input_data') or {}
    analysis = commit_context.get('analysis_result') or input_data.get('analysis_result')
    decision_ids = result['decision_ids']
    created_decisions = result['created_decisions']

    wf_patch = {'created_decisions': decision_ids}
    out = {**input_data, 'analysis_result': analysis}
    sse = [
        {
            'type': 'decision_draft',
            'content': f'Created {len(decision_ids)} decision draft(s).',
            'data': {
                'decision_ids': decision_ids,
                'created_decisions': created_decisions,
            },
        }
    ]
    return out, sse, wf_patch


def _commit_miro_board(orchestrator, draft: dict, destination: dict | None, commit_context: dict):
    from .miro_board_service import create_board_from_snapshot
    from .models import AgentWorkflowRun

    project = orchestrator.project

    run_id = commit_context.get('workflow_run_id')
    if not run_id:
        raise ValueError('workflow_run_id required for miro commit')
    workflow_run = AgentWorkflowRun.objects.get(id=run_id, session=orchestrator.session)
    snapshot = draft.get('snapshot') or workflow_run.miro_snapshot
    if not snapshot:
        raise ValueError('No miro snapshot in draft or workflow run')

    board, persisted_snapshot = create_board_from_snapshot(
        project=project,
        session=orchestrator.session,
        workflow_run=workflow_run,
        snapshot=snapshot,
    )
    workflow_run.miro_board = board
    workflow_run.miro_snapshot = persisted_snapshot
    workflow_run.save(update_fields=['miro_board', 'miro_snapshot', 'updated_at'])
    wf_patch = {'miro_board_id': str(board.id), 'miro_snapshot': persisted_snapshot}
    out = {
        **commit_context.get('merge_output', {}),
        'miro_snapshot': persisted_snapshot,
        'miro_board_id': str(board.id),
    }
    sse = [
        {
            'type': 'miro_board_created',
            'content': f'Miro board created: {board.title}',
            # Emit board_slug so the agent's Miro deep-link is slug-only
            # (the board UUID never appears in the URL).
            'data': {'board_id': str(board.id), 'board_slug': board.slug},
        }
    ]
    return out, sse, wf_patch


def _commit_calendar_events(orchestrator, draft: dict, destination: dict | None, commit_context: dict):
    from calendars.models import Calendar as CalendarModel, Event as EventModel
    from dateutil import parser as date_parser
    import pytz

    org_id = commit_context.get('organization_id')
    if not org_id:
        raise ValueError('organization_id required')

    cal_id = (destination or {}).get('calendar_id')
    if not cal_id:
        raise ValueError('calendar_id destination required')
    cal = CalendarModel.objects.get(
        id=cal_id,
        organization_id=org_id,
        owner=orchestrator.user,
        is_deleted=False,
    )

    user_tz_name = commit_context.get('user_timezone') or 'UTC'
    try:
        user_tz = pytz.timezone(user_tz_name)
    except pytz.UnknownTimeZoneError:
        user_tz = pytz.utc

    def _parse_dt(dt_str):
        if not dt_str:
            return None
        raw = str(dt_str).strip()
        date_part = raw.split(' ')[0] if ' ' in raw else raw
        dt = date_parser.parse(date_part)
        if dt.tzinfo is None and user_tz:
            dt = user_tz.localize(dt)
        elif dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        return dt

    events_specs = draft.get('events') or []
    created = []
    for spec in events_specs:
        if not isinstance(spec, dict):
            continue
        ev = EventModel.objects.create(
            organization_id=org_id,
            calendar=cal,
            created_by=orchestrator.user,
            title=spec.get('title', 'New Event'),
            description=spec.get('description', ''),
            start_datetime=_parse_dt(spec.get('start_datetime')),
            end_datetime=_parse_dt(spec.get('end_datetime')),
            timezone=str(user_tz),
            location=spec.get('location') or '',
        )
        created.append(str(ev.id))

    sse = []
    if created:
        sse.append({'type': 'calendar_updated', 'content': '', 'data': {'event_ids': created}})
    return {}, sse, {'created_event_ids': created}


def _commit_forward(orchestrator, draft: dict, destination: dict | None, commit_context: dict):
    from .services import _forward_to_users

    forwards = draft.get('forwards') or []
    if destination and destination.get('usernames'):
        allow = {u.lower() for u in destination['usernames']}
        forwards = [f for f in forwards if (f.get('username') or '').lower() in allow]
    results = _forward_to_users(forwards, orchestrator.user, orchestrator.project)
    sent = [r['username'] for r in results if r.get('status') == 'sent']
    failed = [r['username'] for r in results if r.get('status') != 'sent']
    sse = []
    if sent:
        sse.append({'type': 'text', 'content': f'Message forwarded to: {", ".join(sent)}'})
    if failed:
        sse.append({'type': 'text', 'content': f'Could not forward to: {", ".join(failed)}'})
    return {}, sse, {}


def _commit_custom_api(_orchestrator, draft: dict, _destination: dict | None, commit_context: dict):
    method = (draft.get('method') or commit_context.get('method') or 'POST').upper()
    url = draft.get('url') or commit_context.get('url')
    headers = draft.get('headers') or commit_context.get('headers') or {}
    body = draft.get('body')
    timeout = int(commit_context.get('timeout', 30))
    if not url:
        raise ValueError('No URL for custom API commit')
    if body is not None and not isinstance(body, (str, bytes)):
        body = json.dumps(body)
    resp = http_requests.request(method, url, headers=headers, data=body, timeout=timeout)
    resp.raise_for_status()
    try:
        api_json = resp.json()
    except Exception:
        api_json = {'raw': resp.text[:2000]}
    out = {**commit_context.get('merge_output', {}), 'api_response': api_json}
    sse = [
        {
            'type': 'text',
            'content': f'Custom API call completed ({resp.status_code}).',
        }
    ]
    return out, sse, {}


COMMIT_REGISTRY = {
    KIND_TASK: _commit_task,
    KIND_DECISION_TREE: _commit_decision_tree,
    KIND_MIRO_BOARD: _commit_miro_board,
    KIND_CALENDAR_EVENT: _commit_calendar_events,
    KIND_FORWARD_MESSAGE: _commit_forward,
    KIND_CUSTOM_API: _commit_custom_api,
}


def run_commit(
    *,
    orchestrator,
    kind: str,
    draft: dict,
    destination: dict | None,
    commit_context: dict,
) -> ExternalCommitResult:
    fn = COMMIT_REGISTRY.get(kind)
    if not fn:
        raise ValueError(f'Unknown external outcome kind: {kind}')
    output_data, sse_events, wf_patch = fn(orchestrator, draft, destination, commit_context)
    return ExternalCommitResult(
        paused=False,
        sse_events=sse_events,
        pending_id=None,
        output_data=output_data,
        workflow_run_patch=wf_patch or {},
    )


def request_external_commit(
    *,
    orchestrator,
    workflow_run,
    step_execution,
    kind: str,
    draft: dict,
    commit_context: dict,
) -> ExternalCommitResult:
    """
    If session.approval_required: create pending row and return paused=True.
    Otherwise run commit immediately.
    """
    from .models import AgentPendingExternalApproval

    session = orchestrator.session
    session.refresh_from_db(fields=['approval_required'])
    project = orchestrator.project
    user = orchestrator.user

    if not session.approval_required:
        _, default_dest = build_destination_options(
            kind=kind,
            session=session,
            project=project,
            user=user,
            draft=draft,
            commit_context=commit_context,
        )
        return run_commit(
            orchestrator=orchestrator,
            kind=kind,
            draft=draft,
            destination=default_dest,
            commit_context=commit_context,
        )

    _dest_opts, default_dest = build_destination_options(
        kind=kind,
        session=session,
        project=project,
        user=user,
        draft=draft,
        commit_context=commit_context,
    )

    pending = AgentPendingExternalApproval.objects.create(
        session=session,
        workflow_run=workflow_run,
        step_execution=step_execution,
        kind=kind,
        status='pending',
        draft=draft,
        commit_context=commit_context,
        destination_options=[],
        default_destination=default_dest,
    )

    sse = [
        {
            'type': 'approval_request',
            'content': f'Approval required before {kind.replace("_", " ")}.',
            'data': {
                'approval_id': str(pending.id),
                'kind': kind,
                'draft': draft,
            },
        }
    ]
    return ExternalCommitResult(
        paused=True,
        sse_events=sse,
        pending_id=str(pending.id),
        output_data=None,
        workflow_run_patch=None,
    )


def resolve_pending(
    *,
    orchestrator,
    pending_id,
    decision: str,
    draft: dict | None,
    destination: dict | None,
) -> ExternalCommitResult:
    from .models import AgentPendingExternalApproval

    try:
        pending = AgentPendingExternalApproval.objects.select_related(
            'session', 'workflow_run', 'step_execution'
        ).get(id=pending_id, session=orchestrator.session, is_deleted=False)
    except AgentPendingExternalApproval.DoesNotExist as e:
        raise ValueError('Approval request not found.') from e

    if pending.status != 'pending':
        raise ValueError('Approval is no longer pending')

    if decision == 'reject':
        pending.status = 'rejected'
        pending.save(update_fields=['status', 'updated_at'])
        return ExternalCommitResult(
            paused=False,
            sse_events=[
                {
                    'type': 'text',
                    'content': f'Action rejected ({pending.kind}).',
                    'data': {'approval_id': str(pending.id), 'rejected': True},
                }
            ],
            pending_id=str(pending.id),
            output_data=None,
            workflow_run_patch={'rejected': True, 'kind': pending.kind},
        )

    merged_draft = {**pending.draft, **(draft or {})}
    # Destination overrides are no longer accepted from the client.
    dest = pending.default_destination
    result = run_commit(
        orchestrator=orchestrator,
        kind=pending.kind,
        draft=merged_draft,
        destination=dest,
        commit_context=pending.commit_context,
    )
    pending.status = 'approved'
    pending.save(update_fields=['status', 'updated_at'])
    return ExternalCommitResult(
        paused=False,
        sse_events=result.sse_events,
        pending_id=str(pending.id),
        output_data=result.output_data,
        workflow_run_patch=result.workflow_run_patch,
    )
