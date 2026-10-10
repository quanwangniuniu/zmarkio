from unittest.mock import patch, Mock

from agent.executors import CreateMiroBoardExecutor, GenerateMiroSnapshotExecutor
from agent.miro_generation import (
    call_gemini_miro_generator,
    deserialize_miro_generation_context,
    normalize_miro_snapshot_layout,
    serialize_miro_generation_context,
)
from agent.miro_board_service import _materialize_snapshot_ids
from agent.services import AgentOrchestrator
from agent.services.miro import (
    MIRO_LEGACY_BG_QUEUED_MESSAGE,
    _enqueue_miro_generation_for_workflow_run,
    _generate_miro_board_for_workflow_run,
)


def _test_snapshot():
    return {
        "viewport": {"x": 0, "y": 0, "zoom": 0.75},
        "items": [
            {
                "id": "frame-1",
                "type": "frame",
                "x": 100,
                "y": 100,
                "width": 300,
                "height": 300,
                "style": {"label": "Summary"},
                "content": "Summary",
                "z_index": 1,
            },
            {
                "id": "note-1",
                "type": "sticky_note",
                "parent_item_id": "frame-1",
                "x": 130,
                "y": 180,
                "width": 180,
                "height": 100,
                "style": {"backgroundColor": "#fef08a"},
                "content": "Anomaly",
                "z_index": 2,
            },
        ],
    }


def _persisted_id_map():
    return {
        "frame-1": "persisted-frame-1",
        "note-1": "persisted-note-1",
    }


def _overlapping_snapshot():
    return {
        "viewport": {"x": 0, "y": 0, "zoom": 0.75},
        "items": [
            {
                "id": "frame-decision",
                "type": "frame",
                "x": 100,
                "y": 100,
                "width": 340,
                "height": 280,
                "style": {"label": "Decision"},
                "content": "Decision",
                "z_index": 1,
            },
            {
                "id": "decision-title",
                "type": "shape",
                "parent_item_id": "frame-decision",
                "x": 130,
                "y": 140,
                "width": 260,
                "height": 60,
                "style": {"shapeType": "roundRect", "fontWeight": "bold"},
                "content": "Reduce spend on underperforming prospecting segment",
                "z_index": 2,
            },
            {
                "id": "decision-reason",
                "type": "text",
                "parent_item_id": "frame-decision",
                "x": 130,
                "y": 150,
                "width": 260,
                "height": 24,
                "style": {"fontSize": 14, "fontWeight": "bold"},
                "content": "Reasoning: Efficiency loss in prospecting. Scale back while diagnosing audience quality and reserve extra vertical room for wrapped text.",
                "z_index": 2,
            },
            {
                "id": "decision-action",
                "type": "sticky_note",
                "parent_item_id": "frame-decision",
                "x": 130,
                "y": 170,
                "width": 220,
                "height": 60,
                "style": {"fontSize": 14},
                "content": "Selected action:\nReduce prospecting budget by 20%",
                "z_index": 2,
            },
        ],
    }


class _WorkflowRunStub:
    def __init__(self):
        self.id = "run-1"
        self.status = "creating_tasks"
        self.analysis_result = {"anomalies": [{"metric": "ROAS"}]}
        self.created_tasks = []
        self.decision = None
        self.decision_id = None
        self.miro_snapshot = None
        self.miro_board = None
        self.saved_update_fields = []

    def save(self, update_fields=None):
        self.saved_update_fields.append(update_fields)


class _StepStub:
    def __init__(self, step_type):
        self.step_type = step_type
        self.config = {}


class _SessionStub:
    def __init__(self):
        self.id = "session-1"
        self.title = "Analysis session"
        self.approval_required = False
        self.project = type("ProjectStub", (), {"id": 99})()
        self.user = type("UserStub", (), {"id": 7, "is_authenticated": True})()
        messages = [
            type("MessageStub", (), {"role": "user", "content": "Analyze this"})(),
            type("MessageStub", (), {"role": "assistant", "content": "Found anomalies"})(),
        ]
        self.messages = type("MessageManagerStub", (), {"order_by": lambda self, *_args: messages})()

    def refresh_from_db(self, *args, **kwargs):
        # Agent approval gate expects a Django model; unit tests use a stub.
        return None


class _OrchestratorStub:
    def __init__(self):
        self.user = type("UserStub", (), {"id": 7})()
        self.project = type("ProjectStub", (), {"id": 99})()
        self.session = _SessionStub()


def test_materialize_snapshot_ids_converts_ids_and_parent_references():
    persisted = _materialize_snapshot_ids(
        _test_snapshot(),
        _persisted_id_map(),
    )

    assert persisted["viewport"]["zoom"] == 0.75
    assert len(persisted["items"]) == 2
    assert persisted["items"][0]["id"] == "persisted-frame-1"
    assert persisted["items"][1]["id"] == "persisted-note-1"
    assert persisted["items"][1]["parent_item_id"] == persisted["items"][0]["id"]


def test_miro_generation_context_payload_round_trips():
    context = {
        "session": {"id": "session-1", "title": "Analysis session", "project_id": 99},
        "workflow_run": {"id": "run-1", "status": "creating_tasks"},
        "analysis": {
            "anomalies": [{"metric": "ROAS", "movement": "down"}],
            "suggested_decision": {
                "id": "decision-1",
                "title": "Reduce spend",
                "options": [{"id": "option-1", "text": "Pause segment", "is_selected": True}],
            },
            "recommended_tasks": [{"id": 7, "summary": "Audit placements", "priority": "HIGH"}],
        },
    }

    payload = serialize_miro_generation_context(context)
    rehydrated = deserialize_miro_generation_context(payload)

    assert rehydrated == context
    assert rehydrated is not context
    assert rehydrated["analysis"] is not context["analysis"]


@patch("agent.miro_generation.call_gemini_miro_generator")
@patch("agent.miro_generation.build_miro_generation_context_from_run")
def test_generate_miro_snapshot_executor_saves_snapshot(mock_build_context, mock_call_gemini):
    workflow_run = _WorkflowRunStub()
    orchestrator = _OrchestratorStub()
    executor = GenerateMiroSnapshotExecutor(_StepStub("generate_miro_snapshot"), workflow_run, orchestrator)

    mock_build_context.return_value = {"chat_context": "[user]: Analyze this"}
    mock_call_gemini.return_value = _test_snapshot()

    result = executor.execute({"analysis_result": workflow_run.analysis_result})

    assert result.success is True
    assert result.output_data["miro_snapshot"]["items"][0]["type"] == "frame"
    assert workflow_run.miro_snapshot["items"][1]["type"] == "sticky_note"
    assert ['miro_snapshot'] in workflow_run.saved_update_fields
    assert result.sse_events[0]["type"] == "miro_snapshot_generated"


@patch("agent.miro_board_service.create_board_from_snapshot")
def test_create_miro_board_executor_persists_board_and_snapshot(mock_create_board):
    workflow_run = _WorkflowRunStub()
    workflow_run.miro_snapshot = _test_snapshot()
    orchestrator = _OrchestratorStub()
    executor = CreateMiroBoardExecutor(_StepStub("create_miro_board"), workflow_run, orchestrator)

    board = type("BoardStub", (), {"id": "board-1", "title": "Agent Miro - Analysis session"})()
    persisted_snapshot = _materialize_snapshot_ids(_test_snapshot(), _persisted_id_map())
    mock_create_board.return_value = (board, persisted_snapshot)

    result = executor.execute({"miro_snapshot": workflow_run.miro_snapshot})

    assert result.success is True
    assert result.output_data["miro_board_id"] == "board-1"
    assert workflow_run.miro_board is board
    assert workflow_run.miro_snapshot["items"][0]["id"] != "frame-1"
    assert ['miro_board', 'miro_snapshot'] in workflow_run.saved_update_fields
    assert result.sse_events[0]["type"] == "miro_board_created"


@patch("agent.models.AgentPendingExternalApproval.objects.filter")
@patch("agent.miro_board_service.create_board_from_snapshot")
@patch("agent.miro_generation.call_gemini_miro_generator")
@patch("agent.miro_generation.build_miro_generation_context_from_run")
def test_generate_miro_board_for_workflow_run_updates_run(
    mock_build_context,
    mock_call_gemini,
    mock_create_board,
    mock_pending_filter,
):
    workflow_run = _WorkflowRunStub()
    orchestrator = _OrchestratorStub()
    mock_build_context.return_value = {"analysis": {"anomalies": []}}
    mock_call_gemini.return_value = _test_snapshot()
    board = type("BoardStub", (), {"id": "board-legacy-1", "title": "Agent Miro - Analysis session"})()
    persisted_snapshot = _materialize_snapshot_ids(_test_snapshot(), _persisted_id_map())
    mock_create_board.return_value = (board, persisted_snapshot)
    mock_pending_filter.return_value.update.return_value = 0

    snapshot, created_board = _generate_miro_board_for_workflow_run(orchestrator, workflow_run)

    assert created_board.id == "board-legacy-1"
    assert snapshot["items"][0]["id"] != "frame-1"
    assert workflow_run.miro_board is board
    assert workflow_run.miro_snapshot == persisted_snapshot
    assert ['miro_snapshot', 'miro_board'] in workflow_run.saved_update_fields


@patch("agent.models.AgentPendingExternalApproval.objects.filter")
@patch("agent.miro_board_service.create_board_from_snapshot")
@patch("agent.miro_generation.call_gemini_miro_generator")
@patch("agent.miro_generation.build_miro_generation_context_from_run")
def test_generate_miro_board_for_workflow_run_uses_context_payload(
    mock_build_context,
    mock_call_gemini,
    mock_create_board,
    mock_pending_filter,
):
    workflow_run = _WorkflowRunStub()
    orchestrator = _OrchestratorStub()
    context_payload = serialize_miro_generation_context({
        "session": {"id": "session-1", "title": "Payload session", "project_id": 99},
        "workflow_run": {"id": "run-1", "status": "creating_tasks"},
        "analysis": {"recommended_tasks": [{"summary": "Payload task"}]},
    })
    mock_call_gemini.return_value = _test_snapshot()
    board = type("BoardStub", (), {"id": "board-legacy-1", "title": "Agent Miro - Analysis session"})()
    persisted_snapshot = _materialize_snapshot_ids(_test_snapshot(), _persisted_id_map())
    mock_create_board.return_value = (board, persisted_snapshot)
    mock_pending_filter.return_value.update.return_value = 0

    _generate_miro_board_for_workflow_run(
        orchestrator,
        workflow_run,
        context_payload=context_payload,
    )

    mock_build_context.assert_not_called()
    mock_call_gemini.assert_called_once()
    assert mock_call_gemini.call_args.args[0] == context_payload


@patch("agent.tasks.generate_miro_board_for_workflow_run_task.delay")
def test_enqueue_miro_generation_passes_serialized_context_payload(mock_delay):
    workflow_run = _WorkflowRunStub()
    workflow_run.analysis_result = {
        "recommended_tasks": [{"summary": "Audit placements", "priority": "HIGH"}],
    }
    orchestrator = _OrchestratorStub()

    _enqueue_miro_generation_for_workflow_run(orchestrator, workflow_run)

    mock_delay.assert_called_once()
    assert mock_delay.call_args.args == (str(workflow_run.id),)
    payload = mock_delay.call_args.kwargs["context_payload"]
    assert payload["session"]["id"] == "session-1"
    assert payload["workflow_run"]["id"] == "run-1"
    assert payload["analysis"]["recommended_tasks"][0]["summary"] == "Audit placements"


@patch("task.models.Task.objects.create")
def test_create_tasks_from_analysis_creates_tasks_without_queueing_miro(mock_task_create):
    orchestrator = AgentOrchestrator(
        user=type("UserStub", (), {"id": 7})(),
        project=type("ProjectStub", (), {"id": 99})(),
        session=_SessionStub(),
    )
    workflow_run = _WorkflowRunStub()
    workflow_run.analysis_result = {
        "recommended_tasks": [
            {"summary": "Audit placements", "priority": "HIGH", "type": "optimization"},
            {"summary": "Revise budget pacing", "priority": "MEDIUM", "type": "execution"},
        ]
    }
    created = []

    def _make_task(**kwargs):
        task_id = len(created) + 1
        task = type("TaskStub", (), {"id": task_id, "slug": f"task-{task_id}", **kwargs})()
        created.append(task)
        return task

    mock_task_create.side_effect = _make_task

    events = list(orchestrator.create_tasks_from_analysis(workflow_run))

    assert events[0]["type"] == "text"
    assert events[1]["type"] == "task_created"
    assert workflow_run.created_tasks == [1, 2]
    assert workflow_run.status == "completed"
    assert ['created_tasks'] in workflow_run.saved_update_fields
    assert ['status'] in workflow_run.saved_update_fields
    assert len(events) == 2


@patch("task.models.Task.objects.create")
def test_create_tasks_from_analysis_is_idempotent_when_tasks_and_miro_exist(mock_task_create):
    orchestrator = AgentOrchestrator(
        user=type("UserStub", (), {"id": 7})(),
        project=type("ProjectStub", (), {"id": 99})(),
        session=_SessionStub(),
    )
    workflow_run = _WorkflowRunStub()
    workflow_run.created_tasks = [101, 102]
    workflow_run.miro_board_id = "board-1"
    workflow_run.miro_board = type("BoardStub", (), {"title": "Agent Miro - Existing"})()

    events = list(orchestrator.create_tasks_from_analysis(workflow_run))

    assert events[0]["type"] == "text"
    assert events[1]["type"] == "task_created"
    assert events[1]["data"]["task_ids"] == [101, 102]
    mock_task_create.assert_not_called()
    assert len(events) == 2


@patch.object(AgentOrchestrator, "_legacy_start_miro_background_if_needed")
def test_generate_miro_action_yields_miro_status_when_generation_claim_succeeds(mock_claim):
    orchestrator = AgentOrchestrator(
        user=type("UserStub", (), {"id": 7})(),
        project=type("ProjectStub", (), {"id": 99})(),
        session=_SessionStub(),
    )
    workflow_run = _WorkflowRunStub()
    workflow_run.analysis_result = {"recommended_tasks": [{"summary": "Audit placements"}]}
    workflow_run.created_tasks = [11, 12]
    mock_claim.return_value = ("started", workflow_run, None)

    events = list(orchestrator._legacy_confirm("generate_miro", workflow_run))

    assert events[0]["type"] == "miro_status"
    assert events[0]["content"] == MIRO_LEGACY_BG_QUEUED_MESSAGE
    mock_claim.assert_called_once_with(workflow_run)


def test_normalize_miro_snapshot_layout_pushes_overlapping_items_down():
    snapshot = _overlapping_snapshot()

    normalized = normalize_miro_snapshot_layout(snapshot)
    frame = normalized["items"][0]
    title = normalized["items"][1]
    reason = normalized["items"][2]
    action = normalized["items"][3]

    assert title["y"] >= frame["y"] + 72
    assert reason["y"] >= title["y"] + title["height"] + 24
    assert action["y"] >= reason["y"] + reason["height"] + 24
    assert action["y"] + action["height"] <= frame["y"] + frame["height"] - 24


@patch("agent.llm_client.call_llm")
def test_call_gemini_miro_generator_normalizes_layout_before_validation(mock_call_llm):
    import json as _json
    mock_call_llm.return_value = {'text': _json.dumps(_overlapping_snapshot()), 'usage': {'input': 10, 'output': 20}}

    snapshot = call_gemini_miro_generator({"analysis": {"anomalies": []}}, user_id=1)
    title = snapshot["items"][1]
    reason = snapshot["items"][2]
    action = snapshot["items"][3]

    assert reason["y"] >= title["y"] + title["height"] + 24
    assert action["y"] >= reason["y"] + reason["height"] + 24


