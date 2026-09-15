"""Tests for functions inside executors.py."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .approval_gate import ExternalCommitResult
from .column_registry import ColumnDetectionResult
from .executors import (
    AnalyzeDataExecutor,
    AwaitConfirmationExecutor,
    BaseStepExecutor,
    CallDifyExecutor,
    CallLLMExecutor,
    CreateDecisionExecutor,
    CreateMiroBoardExecutor,
    CreateTasksExecutor,
    CustomAPIExecutor,
    DetectColumnsExecutor,
    FlowControlExecutor,
    GenerateCriteriaExecutor,
    GenerateMiroSnapshotExecutor,
    NormalizeDataExecutor,
    StepResult,
    _compute_column_stats,
    _infer_value_type,
    get_executor,
)
from core.services.gemini_client import GeminiRetriesExhausted
from .services import _ANALYSIS_SYSTEM_PROMPT


class _StepStub:
    """Small stand-in for a workflow step with executor configuration."""

    def __init__(self, config=None, step_type="custom_api"):
        self.config = config or {}
        self.step_type = step_type


class _WorkflowRunStub:
    """In-memory workflow run that records which fields were saved."""

    def __init__(self, **overrides):
        self.id = "run-123"
        self.success_criteria = None
        self.analysis_result = None
        self.created_decisions = []
        self.created_tasks = []
        self.decision = None
        self.miro_snapshot = None
        self.miro_board = None
        self.saved_update_fields = []
        for name, value in overrides.items():
            setattr(self, name, value)

    def save(self, update_fields=None):
        self.saved_update_fields.append(update_fields)


class _OrchestratorStub:
    """Small collection of collaborators read by executor implementations."""

    def __init__(self, approval_required=False):
        self.user = SimpleNamespace(id=7)
        self.project = SimpleNamespace(id=99)
        self.session = SimpleNamespace(
            id="session-123",
            approval_required=approval_required,
        )


class StepResultTests(SimpleTestCase):
    def test_optional_fields_have_safe_defaults(self):
        result = StepResult(success=True)

        self.assertTrue(result.success)
        self.assertIsNone(result.output_data)
        self.assertIsNone(result.error)
        self.assertEqual(result.sse_events, [])
        self.assertFalse(result.pause_external_approval)
        self.assertFalse(result.skipped)

    def test_provided_fields_are_preserved(self):
        event = {"type": "text", "content": "Done"}
        result = StepResult(
            success=False,
            output_data={"partial": True},
            error="Stopped",
            sse_events=[event],
            pause_external_approval=True,
            skipped=True,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.output_data, {"partial": True})
        self.assertEqual(result.error, "Stopped")
        self.assertEqual(result.sse_events, [event])
        self.assertTrue(result.pause_external_approval)
        self.assertTrue(result.skipped)


class BaseStepExecutorTests(SimpleTestCase):
    def test_constructor_keeps_dependencies_and_step_configuration(self):
        step = _StepStub({"timeout": 5})
        workflow_run = object()
        orchestrator = object()

        executor = BaseStepExecutor(step, workflow_run, orchestrator)

        self.assertIs(executor.step, step)
        self.assertIs(executor.workflow_run, workflow_run)
        self.assertIs(executor.orchestrator, orchestrator)
        self.assertEqual(executor.config, {"timeout": 5})
        self.assertIsNone(executor.step_execution)

    def test_constructor_uses_empty_configuration_when_step_config_is_none(self):
        step = _StepStub()
        step.config = None

        executor = BaseStepExecutor(step, object(), object())

        self.assertEqual(executor.config, {})

    def test_execute_must_be_implemented_by_subclass(self):
        executor = BaseStepExecutor(_StepStub(), object(), object())

        with self.assertRaises(NotImplementedError):
            executor.execute({})


class ExecutorLookupTests(SimpleTestCase):
    def test_every_registered_step_type_returns_its_executor(self):
        expected_executors = {
            "analyze_data": AnalyzeDataExecutor,
            "call_dify": CallDifyExecutor,
            "call_llm": CallLLMExecutor,
            "create_decision": CreateDecisionExecutor,
            "create_tasks": CreateTasksExecutor,
            "generate_miro_snapshot": GenerateMiroSnapshotExecutor,
            "create_miro_board": CreateMiroBoardExecutor,
            "await_confirmation": AwaitConfirmationExecutor,
            "custom_api": CustomAPIExecutor,
            "detect_columns": DetectColumnsExecutor,
            "normalize_data": NormalizeDataExecutor,
            "generate_criteria": GenerateCriteriaExecutor,
            "if_else": FlowControlExecutor,
            "merge": FlowControlExecutor,
            "loop": FlowControlExecutor,
        }
        workflow_run = object()
        orchestrator = object()

        for step_type, expected_class in expected_executors.items():
            with self.subTest(step_type=step_type):
                executor = get_executor(
                    _StepStub(step_type=step_type), workflow_run, orchestrator
                )

                self.assertIs(type(executor), expected_class)
                self.assertIs(executor.workflow_run, workflow_run)
                self.assertIs(executor.orchestrator, orchestrator)

    def test_unknown_step_type_raises_clear_error(self):
        step = _StepStub(step_type="not_registered")

        with self.assertRaisesMessage(ValueError, "Unknown step type: not_registered"):
            get_executor(step, object(), object())


class SimpleExecutorTests(SimpleTestCase):
    def test_call_dify_reports_that_legacy_step_is_unsupported(self):
        executor = CallDifyExecutor(_StepStub(), object(), object())

        result = executor.execute({"source": "upload"})

        self.assertFalse(result.success)
        self.assertEqual(
            result.error,
            "call_dify step type is no longer supported. Use analyze_data instead.",
        )

    def test_await_confirmation_uses_default_message_and_run_id(self):
        workflow_run = _WorkflowRunStub(id="run-456")
        input_data = {"analysis_result": {"anomalies": []}}
        executor = AwaitConfirmationExecutor(
            _StepStub(), workflow_run, _OrchestratorStub()
        )

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(result.output_data, input_data)
        self.assertEqual(
            result.sse_events,
            [
                {
                    "type": "confirmation_request",
                    "content": "Please confirm to continue.",
                    "data": {"workflow_run_id": "run-456"},
                }
            ],
        )

    def test_await_confirmation_uses_configured_message(self):
        executor = AwaitConfirmationExecutor(
            _StepStub({"message": "Approve these columns?"}),
            _WorkflowRunStub(),
            _OrchestratorStub(),
        )

        result = executor.execute({})

        self.assertEqual(result.sse_events[0]["content"], "Approve these columns?")

    def test_flow_control_passes_the_same_input_through(self):
        input_data = {"rows": [1, 2]}
        executor = FlowControlExecutor(_StepStub(), object(), object())

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(result.output_data, input_data)
        self.assertEqual(result.sse_events, [])


class InferValueTypeTests(SimpleTestCase):
    def test_empty_list_defaults_to_string(self):
        values = []
        result = _infer_value_type(values)
        self.assertEqual(result, "string")

    def test_empty_markers_default_to_string(self):
        values = [None, "", "-"]
        result = _infer_value_type(values)
        self.assertEqual(result, "string")

    def test_numeric_values_are_inferred_as_number(self):
        values = ["10", "20.5", "1,000", "-10,000,000", ".245"]
        result = _infer_value_type(values)
        self.assertEqual(result, "number")

    def test_boolean_values_are_inferred_as_boolean(self):
        values = ["true", "false", "yes", "no"]
        result = _infer_value_type(values)
        self.assertEqual(result, "boolean")

    def test_text_values_are_inferred_as_string(self):
        values = ["alice", "bob", "maxwell", "ray"]
        result = _infer_value_type(values)
        self.assertEqual(result, "string")

    def test_exactly_80_percent_numeric_is_number(self):
        values = ["10", "20", "30", "40", "unknown"]
        result = _infer_value_type(values)
        self.assertEqual(result, "number")

    def test_less_than_80_percent_numeric_is_string(self):
        values = ["10", "20", "30", "apple", "pear"]
        result = _infer_value_type(values)
        self.assertEqual(result, "string")

    def test_zero_and_one_are_treated_as_boolean(self):
        values = ["1", "1", "0", "1", "0", "0"]
        result = _infer_value_type(values)
        self.assertEqual(result, "boolean")


class ComputeColumnStatsTests(SimpleTestCase):
    def test_empty_markers_are_counted_as_null(self):
        values = [None, "", "-"]
        result = _compute_column_stats(values, "string")
        self.assertEqual(result["null_count"], 3)
        self.assertEqual(result["unique_count"], 0)
        self.assertIsNone(result["min_value"])
        self.assertIsNone(result["max_value"])
        self.assertEqual(result["sample_values"], [])

    def test_empty_markers_do_not_affect_number(self):
        values = [None, "", "-", "42", "24"]
        result = _compute_column_stats(values, "number")
        self.assertEqual(result["null_count"], 3)
        self.assertEqual(result["unique_count"], 2)
        self.assertEqual(result["min_value"], "24.0")
        self.assertEqual(result["max_value"], "42.0")
        self.assertCountEqual(result["sample_values"], ["42", "24"])

    def test_duplicate_text_are_counted_once(self):
        values = ["Alice", "Alice", "Bob"]
        result = _compute_column_stats(values, "string")
        self.assertEqual(result["null_count"], 0)
        self.assertEqual(result["unique_count"], 2)
        self.assertIsNone(result["min_value"])
        self.assertIsNone(result["max_value"])
        self.assertCountEqual(result["sample_values"], ["Alice", "Bob"])

    def test_numeric_stats(self):
        values = ["10", "20", "5", "30"]
        result = _compute_column_stats(values, "number")
        self.assertEqual(result["null_count"], 0)
        self.assertEqual(result["unique_count"], 4)
        self.assertEqual(result["min_value"], "5.0")
        self.assertEqual(result["max_value"], "30.0")
        self.assertCountEqual(result["sample_values"], ["30", "5", "20", "10"])

    def test_comma_numbers(self):
        values = ["10,000", "2,000", "34,234", "5"]
        result = _compute_column_stats(values, "number")
        self.assertEqual(result["null_count"], 0)
        self.assertEqual(result["unique_count"], 4)
        self.assertEqual(result["min_value"], "5.0")
        self.assertEqual(result["max_value"], "34234.0")
        self.assertCountEqual(result["sample_values"], ["34,234", "5", "10,000", "2,000"])

    def test_numeric_column_in_string_type(self):
        values = ["10,000", "2,000", "34,234", "5"]
        result = _compute_column_stats(values, "string")
        self.assertEqual(result["null_count"], 0)
        self.assertEqual(result["unique_count"], 4)
        self.assertIsNone(result["min_value"])
        self.assertIsNone(result["max_value"])
        self.assertCountEqual(result["sample_values"], ["34,234", "5", "10,000", "2,000"])

    def test_invalid_numbers(self):
        values = ["25", "invalid"]
        result = _compute_column_stats(values, "number")
        self.assertEqual(result["null_count"], 0)
        self.assertEqual(result["unique_count"], 2)
        self.assertIsNone(result["min_value"])
        self.assertIsNone(result["max_value"])
        self.assertCountEqual(result["sample_values"], ["25", "invalid"])

    def test_string_conversion(self):
        values = [1, "1"]
        result = _compute_column_stats(values, "string")
        self.assertEqual(result["null_count"], 0)
        self.assertEqual(result["unique_count"], 1)
        self.assertIsNone(result["min_value"])
        self.assertIsNone(result["max_value"])
        self.assertCountEqual(result["sample_values"], ["1"])

    def test_sample_limit(self):
        values = ["A", "B", "C", "D", "E", "F", "G", "H"]
        result = _compute_column_stats(values, "string")
        self.assertEqual(result["null_count"], 0)
        self.assertEqual(result["unique_count"], 8)
        self.assertIsNone(result["min_value"])
        self.assertIsNone(result["max_value"])
        self.assertEqual(len(result["sample_values"]), 5)
        self.assertTrue(set(result["sample_values"]).issubset(set(values)))


class CustomAPIExecutorTests(SimpleTestCase):
    # The executor imports this function when execute() runs. Patching it here
    # keeps the executor real while preventing database work or an HTTP request
    @patch("agent.approval_gate.request_external_commit", autospec=True)
    def test_success_serializes_input_and_uses_approval_gate_output(
        self, mock_request_commit
    ):
        input_data = {"campaign_id": 123}
        gate_events = [
            {
                "type": "text",
                "content": "Custom API call completed.",
            }
        ]
        gate_output = {
            "campaign_id": 123,
            "api_response": {"ok": True},
        }
        # A real result object makes the mock follow the approval gate's actual
        # return structure instead of silently inventing attributes as needed
        mock_request_commit.return_value = ExternalCommitResult(
            paused=False,
            sse_events=gate_events,
            pending_id=None,
            output_data=gate_output,
            workflow_run_patch={},
        )
        step = _StepStub(
            {
                "method": "post",
                "url": "https://example.test/webhook",
                "headers": {"Authorization": "Bearer test-token"},
                "body_template": "__input__",
                "timeout": 5,
            }
        )
        workflow_run = object()
        orchestrator = object()
        step_execution = object()
        executor = CustomAPIExecutor(
            step=step,
            workflow_run=workflow_run,
            orchestrator=orchestrator,
        )
        # The workflow engine normally attaches this record just before execute()
        # This unit test supplies an empty object because no database record is needed
        executor.step_execution = step_execution

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertEqual(result.output_data, gate_output)
        self.assertEqual(result.sse_events, gate_events)
        self.assertFalse(result.pause_external_approval)
        # This checks the contract between the executor and approval gate
        # including method normalization and JSON serialization of the input
        mock_request_commit.assert_called_once_with(
            orchestrator=orchestrator,
            workflow_run=workflow_run,
            step_execution=step_execution,
            kind="custom_api",
            draft={
                "method": "POST",
                "url": "https://example.test/webhook",
                "headers": {"Authorization": "Bearer test-token"},
                "body": '{"campaign_id": 123}',
            },
            commit_context={
                "method": "POST",
                "url": "https://example.test/webhook",
                "headers": {"Authorization": "Bearer test-token"},
                "timeout": 5,
                "merge_output": {"campaign_id": 123},
            },
        )

    @patch("agent.approval_gate.request_external_commit", autospec=True)
    def test_missing_url_returns_failure_without_calling_gate(
        self, mock_request_commit
    ):
        executor = CustomAPIExecutor(
            step=_StepStub({"method": "POST"}),
            workflow_run=object(),
            orchestrator=object(),
        )

        result = executor.execute({"campaign_id": 123})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "No URL configured for custom API step")
        mock_request_commit.assert_not_called()

    @patch("agent.approval_gate.request_external_commit", autospec=True)
    def test_raw_body_template_is_passed_unchanged(self, mock_request_commit):
        mock_request_commit.return_value = ExternalCommitResult(
            paused=False,
            sse_events=[],
            pending_id=None,
            output_data={"status": "sent"},
            workflow_run_patch={},
        )
        executor = CustomAPIExecutor(
            step=_StepStub(
                {
                    "url": "https://example.test/webhook",
                    "body_template": '{"fixed": true}',
                }
            ),
            workflow_run=object(),
            orchestrator=object(),
        )

        result = executor.execute({"campaign_id": 123})

        self.assertTrue(result.success)
        self.assertEqual(result.output_data, {"status": "sent"})
        # call_args records the keyword arguments received by the patched gate
        self.assertEqual(
            mock_request_commit.call_args.kwargs["draft"]["body"],
            '{"fixed": true}',
        )

    @patch("agent.approval_gate.request_external_commit", autospec=True)
    def test_defaults_are_used_and_missing_gate_output_falls_back_to_input(
        self, mock_request_commit
    ):
        input_data = {"campaign_id": 123}
        mock_request_commit.return_value = ExternalCommitResult(
            paused=False,
            sse_events=[],
            pending_id=None,
            output_data=None,
            workflow_run_patch={},
        )
        executor = CustomAPIExecutor(
            step=_StepStub({"url": "https://example.test/webhook"}),
            workflow_run=object(),
            orchestrator=object(),
        )

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertEqual(result.output_data, input_data)
        # Equal contents but a different object proves the fallback is a copy
        # so top-level changes to the output cannot mutate the original dictionary
        self.assertIsNot(result.output_data, input_data)
        gate_arguments = mock_request_commit.call_args.kwargs
        self.assertEqual(
            gate_arguments["draft"],
            {
                "method": "POST",
                "url": "https://example.test/webhook",
                "headers": {},
                "body": None,
            },
        )
        self.assertEqual(gate_arguments["commit_context"]["timeout"], 30)

    @patch("agent.approval_gate.request_external_commit", autospec=True)
    def test_paused_gate_requests_external_approval(self, mock_request_commit):
        input_data = {"campaign_id": 123}
        gate_events = [
            {
                "type": "approval_request",
                "content": "Approval required.",
                "data": {"pending_id": "pending-123"},
            }
        ]
        mock_request_commit.return_value = ExternalCommitResult(
            paused=True,
            sse_events=gate_events,
            pending_id="pending-123",
            output_data=None,
            workflow_run_patch=None,
        )
        executor = CustomAPIExecutor(
            step=_StepStub({"url": "https://example.test/webhook"}),
            workflow_run=object(),
            orchestrator=object(),
        )

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(result.output_data, input_data)
        self.assertEqual(result.sse_events, gate_events)
        self.assertTrue(result.pause_external_approval)

    @patch("agent.approval_gate.request_external_commit", autospec=True)
    def test_gate_exception_is_converted_to_failed_result(
        self, mock_request_commit
    ):
        # side_effect makes the patched function raise when the executor calls it
        mock_request_commit.side_effect = RuntimeError("API service unavailable")
        executor = CustomAPIExecutor(
            step=_StepStub({"url": "https://example.test/webhook"}),
            workflow_run=object(),
            orchestrator=object(),
        )

        result = executor.execute({"campaign_id": 123})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "API service unavailable")


class AnalyzeDataExecutorTests(SimpleTestCase):
    def test_missing_spreadsheet_returns_failure(self):
        executor = AnalyzeDataExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "No spreadsheet_data in input")

    @patch("agent.executors.cache.get", return_value={"timezone": "Australia/Adelaide"})
    @patch("agent.services._run_analysis")
    def test_success_saves_analysis_and_builds_summary(
        self, mock_run_analysis, mock_cache_get
    ):
        spreadsheet_data = {"sheets": [{"columns": ["Spend"], "rows": []}]}
        analysis = {
            "recommended_tasks": [{"summary": "Review spend"}],
            "recommended_decision_tree": {"nodes": [{"ref": "root"}]},
            "private_debug_value": "not for the browser",
        }
        mock_run_analysis.return_value = analysis
        workflow_run = _WorkflowRunStub(success_criteria={"source": "saved"})
        orchestrator = _OrchestratorStub()
        executor = AnalyzeDataExecutor(_StepStub(), workflow_run, orchestrator)
        input_data = {
            "spreadsheet_data": spreadsheet_data,
            "success_criteria": {"source": "input"},
            "column_mapping": {"Spend": "amount_spent"},
            "generation_outputs": [
                "recommended_tasks",
                "recommended_decision_tree",
            ],
        }

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertEqual(workflow_run.analysis_result, analysis)
        self.assertIn(["analysis_result"], workflow_run.saved_update_fields)
        self.assertEqual(result.output_data["analysis_result"], analysis)
        self.assertCountEqual(
            result.output_data["generation_outputs"],
            ["recommended_tasks", "recommended_decision_tree"],
        )
        self.assertEqual(
            result.sse_events[0]["content"],
            "Found 1 recommended task(s) and 1 decision node(s).",
        )
        self.assertEqual(
            result.sse_events[0]["data"],
            {
                "recommended_tasks": analysis["recommended_tasks"],
                "recommended_decision_tree": analysis[
                    "recommended_decision_tree"
                ],
            },
        )

        call_arguments = mock_run_analysis.call_args
        self.assertEqual(call_arguments.args, (spreadsheet_data,))
        self.assertEqual(call_arguments.kwargs["user_id"], "7")
        self.assertEqual(call_arguments.kwargs["success_criteria"], {"source": "input"})
        self.assertEqual(
            call_arguments.kwargs["column_mapping"], {"Spend": "amount_spent"}
        )
        self.assertEqual(
            call_arguments.kwargs["user_context"],
            {"timezone": "Australia/Adelaide"},
        )
        self.assertIs(call_arguments.kwargs["agent_session"], orchestrator.session)
        mock_cache_get.assert_called_once_with("agent:context:run-123")

    @patch("agent.executors.cache.get", return_value=None)
    @patch("agent.services._run_analysis", return_value={})
    def test_analysis_without_recommendations_uses_plain_summary(
        self, mock_run_analysis, mock_cache_get
    ):
        executor = AnalyzeDataExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({"spreadsheet_data": {"sheets": []}})

        self.assertTrue(result.success)
        self.assertEqual(result.sse_events[0]["content"], "Analysis complete.")
        mock_run_analysis.assert_called_once()
        mock_cache_get.assert_called_once()

    @patch("agent.executors.cache.get", return_value=None)
    @patch("agent.services._run_analysis", return_value={})
    def test_saved_success_criteria_are_used_when_input_omits_them(
        self, mock_run_analysis, mock_cache_get
    ):
        saved_criteria = {"minimum_roas": 2.5}
        workflow_run = _WorkflowRunStub(success_criteria=saved_criteria)
        executor = AnalyzeDataExecutor(
            _StepStub(), workflow_run, _OrchestratorStub()
        )

        result = executor.execute({"spreadsheet_data": {"sheets": []}})

        self.assertTrue(result.success)
        self.assertEqual(
            mock_run_analysis.call_args.kwargs["success_criteria"], saved_criteria
        )
        mock_cache_get.assert_called_once()

    @patch("agent.executors.cache.get", return_value=None)
    @patch("agent.services._run_analysis")
    def test_invalid_generation_outputs_returns_failure(
        self, mock_run_analysis, mock_cache_get
    ):
        executor = AnalyzeDataExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(
            {
                "spreadsheet_data": {"sheets": []},
                "generation_outputs": ["not_supported"],
            }
        )

        self.assertFalse(result.success)
        self.assertIn("Unknown generation output", result.error)
        mock_run_analysis.assert_not_called()
        mock_cache_get.assert_called_once()

    @patch("agent.executors.cache.get", return_value=None)
    @patch("agent.services._run_analysis")
    def test_gemini_retry_exhaustion_returns_failure(
        self, mock_run_analysis, mock_cache_get
    ):
        mock_run_analysis.side_effect = GeminiRetriesExhausted("Gemini rate limited")
        executor = AnalyzeDataExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({"spreadsheet_data": {"sheets": []}})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Gemini rate limited")
        mock_cache_get.assert_called_once()

    @patch("agent.executors.cache.get", return_value=None)
    @patch("agent.services._run_analysis")
    def test_unexpected_analysis_error_returns_failure(
        self, mock_run_analysis, mock_cache_get
    ):
        mock_run_analysis.side_effect = ValueError("Malformed analysis")
        executor = AnalyzeDataExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({"spreadsheet_data": {"sheets": []}})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Malformed analysis")
        mock_cache_get.assert_called_once()


class CallLLMExecutorTests(SimpleTestCase):
    # Patch the imported name used by the executor, keeping billing and HTTP out.
    @patch("agent.executors._call_llm_unified", autospec=True)
    @patch("agent.services._get_llm_client", return_value=None)
    def test_missing_client_returns_configuration_failure(
        self, mock_get_client, mock_call_llm
    ):
        executor = CallLLMExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({"spreadsheet_data": {"rows": []}})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "No LLM API key configured")
        mock_get_client.assert_called_once_with()
        mock_call_llm.assert_not_called()

    @patch("agent.executors._call_llm_unified", autospec=True)
    @patch("agent.services._get_llm_client")
    def test_success_returns_analysis_and_spreadsheet(
        self, mock_get_client, mock_call_llm
    ):
        client = object()
        mock_get_client.return_value = client
        analysis = {"anomalies": [], "recommended_tasks": []}
        llm_response = {
            "text": json.dumps(analysis), "usage": {"input": 10, "output": 2}
        }
        mock_call_llm.return_value = llm_response
        spreadsheet_data = {"rows": [{"spend": 10}]}
        orchestrator = _OrchestratorStub()
        executor = CallLLMExecutor(_StepStub(), _WorkflowRunStub(), orchestrator)

        result = executor.execute({"spreadsheet_data": spreadsheet_data})

        self.assertTrue(result.success)
        self.assertEqual(result.output_data["analysis_result"], analysis)
        self.assertIs(result.output_data["spreadsheet_data"], spreadsheet_data)
        self.assertEqual(result.sse_events[0]["content"], "LLM analysis completed.")
        mock_call_llm.assert_called_once_with(
            provider="anthropic",
            model="claude-sonnet-5",
            user_prompt=json.dumps(spreadsheet_data),
            system_prompt=_ANALYSIS_SYSTEM_PROMPT,
            agent_session=orchestrator.session,
        )

    @patch("agent.executors._call_llm_unified", autospec=True)
    @patch("agent.services._get_llm_client")
    def test_raw_input_is_used_when_spreadsheet_key_is_absent(
        self, mock_get_client, mock_call_llm
    ):
        client = object()
        mock_get_client.return_value = client
        mock_call_llm.return_value = {
            "text": '{"anomalies": [], "recommended_tasks": []}',
            "usage": {"input": 10, "output": 2},
        }
        input_data = {"rows": [{"spend": 10}]}
        orchestrator = _OrchestratorStub()
        executor = CallLLMExecutor(_StepStub(), _WorkflowRunStub(), orchestrator)

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(result.output_data["spreadsheet_data"], input_data)
        mock_call_llm.assert_called_once_with(
            provider="anthropic",
            model="claude-sonnet-5",
            user_prompt=json.dumps(input_data),
            system_prompt=_ANALYSIS_SYSTEM_PROMPT,
            agent_session=orchestrator.session,
        )

    @patch("agent.executors._call_llm_unified", autospec=True)
    @patch("agent.services._get_llm_client", return_value=object())
    def test_invalid_json_returns_failure(self, _mock_get_client, mock_call_llm):
        mock_call_llm.return_value = {
            "text": "Not JSON", "usage": {"input": 10, "output": 2}
        }
        executor = CallLLMExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({"spreadsheet_data": {"rows": []}})

        self.assertFalse(result.success)
        self.assertIsNone(result.output_data)
        self.assertIn("Expecting value", result.error)

    @patch("agent.executors._call_llm_unified", autospec=True)
    @patch("agent.services._get_llm_client", return_value=object())
    def test_json_non_objects_return_failure(self, _mock_get_client, mock_call_llm):
        for response in ([], None, "Plain reply", 7):
            with self.subTest(response=response):
                mock_call_llm.return_value = {
                    "text": json.dumps(response), "usage": {"input": 10, "output": 2}
                }
                executor = CallLLMExecutor(
                    _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
                )

                result = executor.execute({"spreadsheet_data": {"rows": []}})

                self.assertFalse(result.success)
                self.assertIsNone(result.output_data)
                self.assertEqual(result.error, "Analysis response must be a JSON object")

    @patch(
        "agent.executors._call_llm_unified",
        autospec=True,
        side_effect=ValueError("Invalid LLM response"),
    )
    @patch("agent.services._get_llm_client", return_value=object())
    def test_unexpected_llm_error_returns_failure(
        self, mock_get_client, mock_call_llm
    ):
        executor = CallLLMExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({"raw": "input"})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Invalid LLM response")
        mock_get_client.assert_called_once()
        mock_call_llm.assert_called_once()


class CreateDecisionExecutorTests(SimpleTestCase):
    def test_missing_analysis_returns_failure(self):
        executor = CreateDecisionExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "No analysis_result in input")

    @patch("agent.approval_gate.request_external_commit")
    def test_empty_decision_tree_is_successful_no_op(self, mock_request_commit):
        input_data = {"analysis_result": {"recommended_decision_tree": {}}}
        executor = CreateDecisionExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(result.output_data, input_data)
        self.assertEqual(result.sse_events[0]["content"], "No decision nodes to create.")
        mock_request_commit.assert_not_called()

    @patch("agent.approval_gate.request_external_commit")
    def test_paused_gate_requests_external_approval(self, mock_request_commit):
        input_data = {
            "analysis_result": {
                "recommended_decision_tree": {"nodes": [{"ref": "root"}]}
            }
        }
        gate_events = [{"type": "approval_request", "data": {"pending_id": "p-1"}}]
        mock_request_commit.return_value = ExternalCommitResult(
            paused=True,
            sse_events=gate_events,
            pending_id="p-1",
            output_data=None,
            workflow_run_patch=None,
        )
        executor = CreateDecisionExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(result.output_data, input_data)
        self.assertEqual(result.sse_events, gate_events)
        self.assertTrue(result.pause_external_approval)

    @patch("agent.approval_gate.request_external_commit")
    def test_success_saves_created_decisions_and_merges_output(
        self, mock_request_commit
    ):
        tree = {"nodes": [{"ref": "root"}]}
        input_data = {"analysis_result": {"recommended_decision_tree": tree}}
        gate_events = [{"type": "text", "content": "Decision created"}]
        mock_request_commit.return_value = ExternalCommitResult(
            paused=False,
            sse_events=gate_events,
            pending_id=None,
            output_data={"gate_value": "kept"},
            workflow_run_patch={"created_decisions": ["decision-1"]},
        )
        workflow_run = _WorkflowRunStub()
        orchestrator = _OrchestratorStub()
        step_execution = object()
        executor = CreateDecisionExecutor(_StepStub(), workflow_run, orchestrator)
        executor.step_execution = step_execution

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertEqual(workflow_run.created_decisions, ["decision-1"])
        self.assertIn(["created_decisions"], workflow_run.saved_update_fields)
        self.assertEqual(
            result.output_data,
            {"gate_value": "kept", "created_decision_ids": ["decision-1"]},
        )
        self.assertEqual(result.sse_events, gate_events)
        mock_request_commit.assert_called_once_with(
            orchestrator=orchestrator,
            workflow_run=workflow_run,
            step_execution=step_execution,
            kind="decision_tree",
            draft={"recommended_decision_tree": tree},
            commit_context={
                "input_data": input_data,
                "analysis_result": input_data["analysis_result"],
            },
        )

    @patch(
        "agent.approval_gate.request_external_commit",
        side_effect=RuntimeError("Decision service unavailable"),
    )
    def test_gate_error_returns_failure(self, mock_request_commit):
        executor = CreateDecisionExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )
        input_data = {
            "analysis_result": {
                "recommended_decision_tree": {"nodes": [{"ref": "root"}]}
            }
        }

        result = executor.execute(input_data)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Decision service unavailable")
        mock_request_commit.assert_called_once()


class CreateTasksExecutorTests(SimpleTestCase):
    def test_missing_analysis_returns_failure(self):
        executor = CreateTasksExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "No analysis_result in input")

    @patch("agent.approval_gate.request_external_commit")
    def test_unconfirmed_anomalies_return_failure(self, mock_request_commit):
        executor = CreateTasksExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )
        input_data = {
            "analysis_result": {
                "anomalies": [{"metric": "ROAS"}],
                "recommended_tasks": [{"summary": "Review ROAS"}],
            }
        }

        result = executor.execute(input_data)

        self.assertFalse(result.success)
        self.assertEqual(
            result.error, "Anomalies must be confirmed before creating tasks."
        )
        mock_request_commit.assert_not_called()

    @patch("agent.approval_gate.request_external_commit")
    def test_all_excluded_anomalies_still_submit_recommended_tasks(self, mock_request_commit):
        input_data = {
            "analysis_result": {
                "anomalies": [{"metric": "ROAS"}],
                "anomalies_confirmed": True,
                "reviewed_anomalies": [{"metric": "ROAS", "included": False}],
                "recommended_tasks": [{"summary": "Review ROAS"}],
            }
        }
        # Task recommendations remain actionable when every anomaly is excluded.
        mock_request_commit.return_value = ExternalCommitResult(
            paused=False,
            sse_events=[{"type": "text", "content": "Task created"}],
            pending_id=None,
            output_data=None,
            workflow_run_patch={"created_tasks": ["task-1"]},
        )
        workflow_run = _WorkflowRunStub()
        executor = CreateTasksExecutor(_StepStub(), workflow_run, _OrchestratorStub())

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertEqual(result.output_data["created_task_ids"], ["task-1"])
        self.assertEqual(workflow_run.created_tasks, ["task-1"])
        mock_request_commit.assert_called_once()
        context = mock_request_commit.call_args.kwargs["commit_context"]
        self.assertEqual(context["included_anomalies"], [])
        self.assertEqual(
            context["reviewed_anomalies"], [{"metric": "ROAS", "included": False}]
        )

    @patch("agent.approval_gate.request_external_commit")
    def test_missing_recommended_tasks_returns_failure(self, mock_request_commit):
        executor = CreateTasksExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({"analysis_result": {"anomalies": []}})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "No recommended_tasks in analysis.")
        mock_request_commit.assert_not_called()

    @patch("agent.approval_gate.request_external_commit")
    def test_paused_gate_requests_external_approval(self, mock_request_commit):
        input_data = {
            "analysis_result": {
                "anomalies": [],
                "recommended_tasks": [{"summary": "Review spend"}],
            }
        }
        gate_events = [{"type": "approval_request", "data": {"pending_id": "p-2"}}]
        mock_request_commit.return_value = ExternalCommitResult(
            paused=True,
            sse_events=gate_events,
            pending_id="p-2",
            output_data=None,
            workflow_run_patch=None,
        )
        executor = CreateTasksExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(result.output_data, input_data)
        self.assertEqual(result.sse_events, gate_events)
        self.assertTrue(result.pause_external_approval)

    @patch("agent.approval_gate.request_external_commit")
    def test_success_saves_created_tasks_and_forwards_review_context(
        self, mock_request_commit
    ):
        reviewed = [{"metric": "ROAS", "included": True}]
        tasks = [{"summary": "Review ROAS"}]
        analysis = {
            "anomalies": [{"metric": "ROAS"}],
            "anomalies_confirmed": True,
            "reviewed_anomalies": reviewed,
            "recommended_tasks": tasks,
        }
        input_data = {"analysis_result": analysis}
        gate_events = [{"type": "text", "content": "Task created"}]
        mock_request_commit.return_value = ExternalCommitResult(
            paused=False,
            sse_events=gate_events,
            pending_id=None,
            output_data=None,
            workflow_run_patch={"created_tasks": ["task-1"]},
        )
        workflow_run = _WorkflowRunStub(decision=SimpleNamespace(id="decision-9"))
        orchestrator = _OrchestratorStub()
        step_execution = object()
        executor = CreateTasksExecutor(_StepStub(), workflow_run, orchestrator)
        executor.step_execution = step_execution

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertEqual(workflow_run.created_tasks, ["task-1"])
        self.assertIn(["created_tasks"], workflow_run.saved_update_fields)
        self.assertEqual(result.output_data["created_task_ids"], ["task-1"])
        self.assertEqual(result.output_data["analysis_result"], analysis)
        self.assertEqual(result.sse_events, gate_events)
        mock_request_commit.assert_called_once_with(
            orchestrator=orchestrator,
            workflow_run=workflow_run,
            step_execution=step_execution,
            kind="task",
            draft={"recommended_tasks": tasks},
            commit_context={
                "input_data": input_data,
                "analysis_result": analysis,
                "decision_id": "decision-9",
                "included_anomalies": reviewed,
                "reviewed_anomalies": reviewed,
            },
        )

    @patch(
        "agent.approval_gate.request_external_commit",
        side_effect=RuntimeError("Task service unavailable"),
    )
    def test_gate_error_returns_failure(self, mock_request_commit):
        executor = CreateTasksExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )
        input_data = {
            "analysis_result": {"recommended_tasks": [{"summary": "Review"}]}
        }

        result = executor.execute(input_data)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Task service unavailable")
        mock_request_commit.assert_called_once()


class GenerateCriteriaExecutorTests(SimpleTestCase):
    @patch("agent.llm_client.call_llm")
    @patch("core.services.gemini_client._get_api_key", return_value="")
    def test_missing_api_key_skips_generation(self, mock_get_key, mock_call_llm):
        input_data = {"spreadsheet_data": {"sheets": []}}
        executor = GenerateCriteriaExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(result.output_data, input_data)
        self.assertIn("skipped (no API key)", result.sse_events[0]["content"])
        mock_get_key.assert_called_once_with()
        mock_call_llm.assert_not_called()

    @patch("agent.llm_client.call_llm")
    @patch("core.services.gemini_client._get_api_key", return_value="test-key")
    def test_missing_columns_skips_generation(self, mock_get_key, mock_call_llm):
        input_data = {"spreadsheet_data": {"sheets": [{"rows": []}]}}
        executor = GenerateCriteriaExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(result.output_data, input_data)
        self.assertIn("skipped (no columns)", result.sse_events[0]["content"])
        mock_get_key.assert_called_once_with()
        mock_call_llm.assert_not_called()

    @patch("agent.llm_client.call_llm")
    @patch("core.services.gemini_client._get_api_key", return_value="test-key")
    def test_success_deduplicates_columns_saves_criteria_and_builds_summary(
        self, mock_get_key, mock_call_llm
    ):
        criteria = {
            "schema_type": "Advertising report",
            "criteria": [
                {
                    "column": "Spend",
                    "anomaly_rule": "Spend must not be negative",
                },
                {"column": "Ignored because no rule"},
            ],
            "analysis_goals": ["Find inefficient campaigns"],
        }
        mock_call_llm.return_value = {"text": json.dumps(criteria)}
        workflow_run = _WorkflowRunStub()
        orchestrator = _OrchestratorStub()
        executor = GenerateCriteriaExecutor(_StepStub(), workflow_run, orchestrator)
        input_data = {
            "spreadsheet_data": {
                "sheets": [
                    {"columns": ["Campaign", "Spend"]},
                    {"columns": ["Spend", "Clicks"]},
                ]
            }
        }

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertEqual(workflow_run.success_criteria, criteria)
        self.assertIn(["success_criteria"], workflow_run.saved_update_fields)
        self.assertEqual(result.output_data["success_criteria"], criteria)
        self.assertIn("Advertising report", result.sse_events[0]["content"])
        self.assertIn("Spend must not be negative", result.sse_events[0]["content"])
        self.assertIn("Find inefficient campaigns", result.sse_events[0]["content"])

        llm_arguments = mock_call_llm.call_args.kwargs
        self.assertIs(llm_arguments["agent_session"], orchestrator.session)
        self.assertEqual(llm_arguments["provider"], "gemini")
        self.assertEqual(llm_arguments["model"], "gemini-2.5-flash-lite")
        self.assertIn('["Campaign", "Spend", "Clicks"]', llm_arguments["user_prompt"])
        self.assertEqual(llm_arguments["call_purpose"], "criteria_generation")
        mock_get_key.assert_called_once()

    @patch(
        "agent.llm_client.call_llm",
        side_effect=GeminiRetriesExhausted("Gemini rate limited"),
    )
    @patch("core.services.gemini_client._get_api_key", return_value="test-key")
    def test_gemini_retry_exhaustion_marks_step_skipped(
        self, mock_get_key, mock_call_llm
    ):
        executor = GenerateCriteriaExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(
            {"spreadsheet_data": {"sheets": [{"columns": ["Spend"]}]}}
        )

        self.assertFalse(result.success)
        self.assertTrue(result.skipped)
        self.assertEqual(result.error, "Gemini rate limited")
        mock_get_key.assert_called_once()
        mock_call_llm.assert_called_once()

    @patch(
        "agent.llm_client.call_llm",
        side_effect=RuntimeError("Gemini request timed out"),
    )
    @patch("core.services.gemini_client._get_api_key", return_value="test-key")
    def test_runtime_error_is_handled_by_retry_policy(
        self, mock_get_key, mock_call_llm
    ):
        # One attempt keeps this test fast while still exercising the hand-off
        # from the executor's RuntimeError branch to its retry decorator.
        step = _StepStub({"max_retries": 1})
        executor = GenerateCriteriaExecutor(
            step, _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(
            {"spreadsheet_data": {"sheets": [{"columns": ["Spend"]}]}}
        )

        self.assertFalse(result.success)
        self.assertTrue(result.skipped)
        self.assertEqual(result.error, "Gemini request timed out")
        mock_get_key.assert_called_once()
        mock_call_llm.assert_called_once()

    @patch("agent.llm_client.call_llm", return_value={"text": "not-json"})
    @patch("core.services.gemini_client._get_api_key", return_value="test-key")
    def test_invalid_llm_response_continues_without_criteria(
        self, mock_get_key, mock_call_llm
    ):
        input_data = {
            "spreadsheet_data": {"sheets": [{"columns": ["Spend"]}]}
        }
        executor = GenerateCriteriaExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(result.output_data, input_data)
        self.assertIn("generation failed", result.sse_events[0]["content"])
        mock_get_key.assert_called_once()
        mock_call_llm.assert_called_once()


class DetectColumnsExecutorTests(SimpleTestCase):
    def test_missing_spreadsheet_returns_failure(self):
        executor = DetectColumnsExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "No spreadsheet_data in input")

    @patch("agent.column_registry.detect_columns")
    def test_success_uses_first_sheet_and_first_three_sample_rows(
        self, mock_detect_columns
    ):
        rows = [{"Spend": value} for value in (10, 20, 30, 40)]
        detection = ColumnDetectionResult(
            schema_key="ads",
            schema_name="Advertising report",
            source="rule",
            confidence=0.875,
            mappings={"Spend": "amount_spent"},
            categories={"amount_spent": "financial"},
            unrecognized=[],
        )
        mock_detect_columns.return_value = detection
        orchestrator = _OrchestratorStub()
        executor = DetectColumnsExecutor(
            _StepStub(), _WorkflowRunStub(), orchestrator
        )
        input_data = {
            "spreadsheet_data": {
                "sheets": [{"columns": ["Spend"], "rows": rows}]
            },
            "file_id": "file-1",
        }

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertEqual(
            result.output_data["column_mapping"], {"Spend": "amount_spent"}
        )
        self.assertEqual(
            result.output_data["column_detection"], detection.to_dict()
        )
        self.assertEqual(result.sse_events[0]["type"], "column_mapping")
        self.assertIn("confidence 88%", result.sse_events[0]["content"])
        mock_detect_columns.assert_called_once_with(
            ["Spend"],
            sample_rows=rows[:3],
            agent_session=orchestrator.session,
        )

    @patch("agent.column_registry.detect_columns")
    def test_empty_sheets_still_runs_detection_with_empty_samples(
        self, mock_detect_columns
    ):
        mock_detect_columns.return_value = ColumnDetectionResult(
            schema_key=None,
            schema_name="Unknown format",
            source="none",
            confidence=0.0,
            mappings={},
            categories={},
            unrecognized=[],
        )
        executor = DetectColumnsExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({"spreadsheet_data": {"sheets": []}})

        self.assertTrue(result.success)
        mock_detect_columns.assert_called_once_with(
            [],
            sample_rows=[],
            agent_session=executor.orchestrator.session,
        )

    @patch(
        "agent.column_registry.detect_columns",
        side_effect=GeminiRetriesExhausted("Gemini rate limited"),
    )
    def test_gemini_retry_exhaustion_marks_step_skipped(self, mock_detect_columns):
        executor = DetectColumnsExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({"spreadsheet_data": {"sheets": []}})

        self.assertFalse(result.success)
        self.assertTrue(result.skipped)
        self.assertEqual(result.error, "Gemini rate limited")
        mock_detect_columns.assert_called_once()

    @patch(
        "agent.column_registry.detect_columns",
        side_effect=ValueError("Invalid detection result"),
    )
    def test_unexpected_detection_error_returns_failure(self, mock_detect_columns):
        executor = DetectColumnsExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({"spreadsheet_data": {"sheets": []}})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Invalid detection result")
        mock_detect_columns.assert_called_once()


class NormalizeDataExecutorTests(SimpleTestCase):
    def test_missing_spreadsheet_returns_failure(self):
        executor = NormalizeDataExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "No spreadsheet_data in input")

    @patch("agent.column_registry.normalize_spreadsheet")
    def test_missing_mapping_passes_original_input_through(
        self, mock_normalize_spreadsheet
    ):
        input_data = {"spreadsheet_data": {"sheets": []}}
        executor = NormalizeDataExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(result.output_data, input_data)
        self.assertIn("using original column names", result.sse_events[0]["content"])
        mock_normalize_spreadsheet.assert_not_called()

    @patch("agent.column_registry.normalize_spreadsheet")
    def test_success_returns_normalized_spreadsheet(
        self, mock_normalize_spreadsheet
    ):
        spreadsheet_data = {"sheets": [{"columns": ["Spend"]}]}
        normalized = {"sheets": [{"columns": ["amount_spent"]}]}
        mapping = {"Spend": "amount_spent"}
        mock_normalize_spreadsheet.return_value = normalized
        executor = NormalizeDataExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )
        input_data = {
            "spreadsheet_data": spreadsheet_data,
            "column_mapping": mapping,
            "source": "upload",
        }

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertEqual(result.output_data["spreadsheet_data"], normalized)
        self.assertEqual(result.output_data["source"], "upload")
        self.assertEqual(result.sse_events[0]["type"], "text")
        mock_normalize_spreadsheet.assert_called_once_with(spreadsheet_data, mapping)

    @patch.object(NormalizeDataExecutor, "_persist_metadata")
    @patch("agent.column_registry.normalize_spreadsheet")
    def test_file_id_triggers_metadata_persistence(
        self, mock_normalize_spreadsheet, mock_persist_metadata
    ):
        spreadsheet_data = {"sheets": [{"columns": ["Spend"]}]}
        mapping = {"Spend": "amount_spent"}
        detection = {"categories": {"amount_spent": "financial"}}
        mock_normalize_spreadsheet.return_value = {"normalized": True}
        executor = NormalizeDataExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(
            {
                "file_id": "file-1",
                "spreadsheet_data": spreadsheet_data,
                "column_mapping": mapping,
                "column_detection": detection,
            }
        )

        self.assertTrue(result.success)
        mock_persist_metadata.assert_called_once_with(
            file_id="file-1",
            column_mapping=mapping,
            column_detection=detection,
            spreadsheet_data=spreadsheet_data,
        )

    @patch.object(
        NormalizeDataExecutor,
        "_persist_metadata",
        side_effect=RuntimeError("Database temporarily unavailable"),
    )
    @patch("agent.column_registry.normalize_spreadsheet", return_value={"normalized": True})
    def test_metadata_error_is_non_fatal(
        self, mock_normalize_spreadsheet, mock_persist_metadata
    ):
        executor = NormalizeDataExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(
            {
                "file_id": "file-1",
                "spreadsheet_data": {"sheets": []},
                "column_mapping": {"Spend": "amount_spent"},
            }
        )

        self.assertTrue(result.success)
        self.assertEqual(result.output_data["spreadsheet_data"], {"normalized": True})
        mock_normalize_spreadsheet.assert_called_once()
        mock_persist_metadata.assert_called_once()

    @patch(
        "agent.column_registry.normalize_spreadsheet",
        side_effect=ValueError("Invalid column mapping"),
    )
    def test_normalization_error_returns_failure(self, mock_normalize_spreadsheet):
        executor = NormalizeDataExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute(
            {
                "spreadsheet_data": {"sheets": []},
                "column_mapping": {"Spend": "amount_spent"},
            }
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Invalid column mapping")
        mock_normalize_spreadsheet.assert_called_once()


class PersistMetadataTests(SimpleTestCase):
    def setUp(self):
        self.executor = NormalizeDataExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

    def test_missing_file_stops_without_deleting_or_creating_metadata(self):
        class MissingFile(Exception):
            pass

        with (
            patch("agent.models.ImportedCSVFile") as file_model,
            patch("agent.models.ImportedDataField") as field_model,
            patch("agent.models.ImportedDataRecord") as record_model,
        ):
            file_model.DoesNotExist = MissingFile
            file_model.objects.get.side_effect = MissingFile

            self.executor._persist_metadata(
                file_id="missing-file",
                column_mapping={"Spend": "amount_spent"},
                column_detection={},
                spreadsheet_data={"sheets": []},
            )

        file_model.objects.get.assert_called_once_with(
            id="missing-file", is_deleted=False
        )
        field_model.objects.filter.assert_not_called()
        record_model.objects.filter.assert_not_called()

    def test_builds_fields_records_quality_scores_and_batches_inserts(self):
        rows = [
            {
                "Spend": None if index == 500 else str(index),
                "Mystery": "first" if index == 0 else None,
            }
            for index in range(501)
        ]
        spreadsheet_data = {"sheets": [{"rows": rows}]}
        column_mapping = {
            "Spend": "amount_spent",
            "Mystery": "unknown",
        }
        column_detection = {
            "categories": {"amount_spent": "financial"},
            "column_confidences": {"Spend": 0.87654, "Mystery": 0.2},
        }
        csv_file = SimpleNamespace(project=SimpleNamespace(id=99))

        with (
            patch("agent.models.ImportedCSVFile") as file_model,
            patch("agent.models.ImportedDataField") as field_model,
            patch("agent.models.ImportedDataRecord") as record_model,
            patch("agent.models.FieldCategory.get_or_create_by_name") as category_get,
        ):
            file_model.objects.get.return_value = csv_file
            field_model.side_effect = lambda **values: SimpleNamespace(**values)
            field_model.objects.bulk_create.side_effect = lambda values: list(values)
            record_model.side_effect = lambda **values: SimpleNamespace(**values)
            record_model.objects.bulk_create.side_effect = lambda values: list(values)

            self.executor._persist_metadata(
                file_id="file-1",
                column_mapping=column_mapping,
                column_detection=column_detection,
                spreadsheet_data=spreadsheet_data,
            )

        file_model.objects.get.assert_called_once_with(id="file-1", is_deleted=False)
        field_model.objects.filter.assert_called_once_with(file=csv_file)
        field_model.objects.filter.return_value.delete.assert_called_once_with()
        record_model.objects.filter.assert_called_once_with(file=csv_file)
        record_model.objects.filter.return_value.delete.assert_called_once_with()

        created_fields = field_model.objects.bulk_create.call_args.args[0]
        self.assertEqual(len(created_fields), 2)
        spend_field, mystery_field = created_fields
        self.assertEqual(spend_field.canonical_name, "amount_spent")
        self.assertEqual(spend_field.value_type, "number")
        self.assertEqual(spend_field.category, "financial")
        self.assertEqual(spend_field.confidence, 0.877)
        self.assertEqual(spend_field.min_value, "0.0")
        self.assertEqual(spend_field.max_value, "49.0")
        self.assertEqual(mystery_field.canonical_name, "unknown")
        self.assertEqual(mystery_field.category, "unknown")
        category_get.assert_any_call("financial", project=csv_file.project)
        category_get.assert_any_call("unknown", project=csv_file.project)

        # 501 records are split into one batch of 500 and one batch of 1.
        self.assertEqual(record_model.objects.bulk_create.call_count, 2)
        first_batch = record_model.objects.bulk_create.call_args_list[0].args[0]
        second_batch = record_model.objects.bulk_create.call_args_list[1].args[0]
        self.assertEqual(len(first_batch), 500)
        self.assertEqual(len(second_batch), 1)
        self.assertEqual(first_batch[0].data["amount_spent"], "0")
        self.assertEqual(first_batch[0].data["Mystery"], "first")
        self.assertEqual(first_batch[0].quality_score, 1.0)
        self.assertEqual(second_batch[0].quality_score, 0.0)


class GenerateMiroSnapshotExecutorTests(SimpleTestCase):
    @patch("agent.miro_generation.call_gemini_miro_generator")
    @patch("agent.miro_generation.build_miro_generation_context_from_run")
    def test_success_saves_snapshot_and_reports_item_count(
        self, mock_build_context, mock_generate_snapshot
    ):
        context = {"analysis": {"recommended_tasks": []}}
        snapshot = {"items": [{"type": "frame"}, {"type": "sticky_note"}]}
        mock_build_context.return_value = context
        mock_generate_snapshot.return_value = snapshot
        workflow_run = _WorkflowRunStub()
        orchestrator = _OrchestratorStub()
        executor = GenerateMiroSnapshotExecutor(
            _StepStub(), workflow_run, orchestrator
        )
        input_data = {"analysis_result": {"recommended_tasks": []}}

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertEqual(workflow_run.miro_snapshot, snapshot)
        self.assertIn(["miro_snapshot"], workflow_run.saved_update_fields)
        self.assertEqual(result.output_data["miro_snapshot"], snapshot)
        self.assertEqual(result.sse_events[0]["data"]["item_count"], 2)
        mock_build_context.assert_called_once_with(
            session=orchestrator.session,
            workflow_run=workflow_run,
        )
        mock_generate_snapshot.assert_called_once_with(
            context,
            user_id="7",
            agent_session=orchestrator.session,
        )

    @patch(
        "agent.miro_generation.call_gemini_miro_generator",
        side_effect=GeminiRetriesExhausted("Gemini rate limited"),
    )
    @patch("agent.miro_generation.build_miro_generation_context_from_run")
    def test_gemini_retry_exhaustion_returns_failure(
        self, mock_build_context, mock_generate_snapshot
    ):
        context = {"analysis": {"recommended_tasks": []}}
        mock_build_context.return_value = context
        workflow_run = _WorkflowRunStub()
        orchestrator = _OrchestratorStub()
        executor = GenerateMiroSnapshotExecutor(
            _StepStub(), workflow_run, orchestrator
        )

        result = executor.execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Gemini rate limited")
        mock_build_context.assert_called_once_with(
            session=orchestrator.session,
            workflow_run=workflow_run,
        )
        mock_generate_snapshot.assert_called_once_with(
            context,
            user_id="7",
            agent_session=orchestrator.session,
        )

    @patch(
        "agent.miro_generation.build_miro_generation_context_from_run",
        side_effect=ValueError("Invalid Miro context"),
    )
    def test_unexpected_generation_error_returns_failure(self, mock_build_context):
        executor = GenerateMiroSnapshotExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Invalid Miro context")
        mock_build_context.assert_called_once()

    @patch(
        "agent.miro_generation.build_miro_generation_context_from_run",
        side_effect=RuntimeError("Gemini request timed out"),
    )
    def test_runtime_error_is_handled_by_retry_policy(self, mock_build_context):
        # The production decorator normally retries three times. One attempt is
        # sufficient here because retry counts are covered in test_retry_policy.py.
        step = _StepStub({"max_retries": 1})
        executor = GenerateMiroSnapshotExecutor(
            step, _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({})

        self.assertFalse(result.success)
        self.assertFalse(result.skipped)
        self.assertEqual(result.error, "Gemini request timed out")
        mock_build_context.assert_called_once()


class CreateMiroBoardExecutorTests(SimpleTestCase):
    def test_missing_snapshot_returns_failure(self):
        executor = CreateMiroBoardExecutor(
            _StepStub(), _WorkflowRunStub(), _OrchestratorStub()
        )

        result = executor.execute({})

        self.assertFalse(result.success)
        self.assertEqual(
            result.error, "No miro_snapshot available for board creation"
        )

    @patch("agent.approval_gate.request_external_commit")
    @patch("agent.miro_board_service.create_board_from_snapshot")
    def test_without_approval_creates_board_immediately(
        self, mock_create_board, mock_request_commit
    ):
        original_snapshot = {"items": [{"id": "temporary"}]}
        persisted_snapshot = {"items": [{"id": "saved-item"}]}
        board = SimpleNamespace(id="board-1", title="Analysis board", slug="analysis")
        mock_create_board.return_value = (board, persisted_snapshot)
        workflow_run = _WorkflowRunStub(miro_snapshot=original_snapshot)
        orchestrator = _OrchestratorStub(approval_required=False)
        executor = CreateMiroBoardExecutor(_StepStub(), workflow_run, orchestrator)

        result = executor.execute({"source": "workflow"})

        self.assertTrue(result.success)
        self.assertIs(workflow_run.miro_board, board)
        self.assertEqual(workflow_run.miro_snapshot, persisted_snapshot)
        self.assertIn(
            ["miro_board", "miro_snapshot"], workflow_run.saved_update_fields
        )
        self.assertEqual(result.output_data["miro_board_id"], "board-1")
        self.assertEqual(result.output_data["miro_snapshot"], persisted_snapshot)
        self.assertEqual(result.sse_events[0]["data"]["board_slug"], "analysis")
        mock_create_board.assert_called_once_with(
            project=orchestrator.project,
            session=orchestrator.session,
            workflow_run=workflow_run,
            snapshot=original_snapshot,
        )
        mock_request_commit.assert_not_called()

    @patch("agent.approval_gate.request_external_commit")
    @patch("agent.miro_board_service.create_board_from_snapshot")
    def test_approval_gate_can_pause_board_creation(
        self, mock_create_board, mock_request_commit
    ):
        snapshot = {"items": []}
        input_data = {"miro_snapshot": snapshot}
        gate_events = [{"type": "approval_request", "data": {"pending_id": "p-3"}}]
        mock_request_commit.return_value = ExternalCommitResult(
            paused=True,
            sse_events=gate_events,
            pending_id="p-3",
            output_data=None,
            workflow_run_patch=None,
        )
        workflow_run = _WorkflowRunStub()
        orchestrator = _OrchestratorStub(approval_required=True)
        executor = CreateMiroBoardExecutor(_StepStub(), workflow_run, orchestrator)

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(result.output_data, input_data)
        self.assertEqual(result.sse_events, gate_events)
        self.assertTrue(result.pause_external_approval)
        mock_create_board.assert_not_called()

    @patch("miro.models.Board.objects.get")
    @patch("agent.approval_gate.request_external_commit")
    @patch("agent.miro_board_service.create_board_from_snapshot")
    def test_approved_gate_attaches_board_and_persisted_snapshot(
        self, mock_create_board, mock_request_commit, mock_get_board
    ):
        snapshot = {"items": [{"id": "temporary"}]}
        persisted_snapshot = {"items": [{"id": "saved-item"}]}
        board = SimpleNamespace(id="board-2")
        gate_events = [{"type": "miro_board_created"}]
        mock_get_board.return_value = board
        mock_request_commit.return_value = ExternalCommitResult(
            paused=False,
            sse_events=gate_events,
            pending_id=None,
            output_data=None,
            workflow_run_patch={
                "miro_board_id": "board-2",
                "miro_snapshot": persisted_snapshot,
            },
        )
        workflow_run = _WorkflowRunStub()
        orchestrator = _OrchestratorStub(approval_required=True)
        step_execution = object()
        executor = CreateMiroBoardExecutor(_StepStub(), workflow_run, orchestrator)
        executor.step_execution = step_execution
        input_data = {"miro_snapshot": snapshot, "source": "workflow"}

        result = executor.execute(input_data)

        self.assertTrue(result.success)
        self.assertIs(workflow_run.miro_board, board)
        self.assertEqual(workflow_run.miro_snapshot, persisted_snapshot)
        self.assertEqual(result.output_data["miro_board_id"], "board-2")
        self.assertEqual(result.output_data["miro_snapshot"], persisted_snapshot)
        self.assertEqual(result.sse_events, gate_events)
        mock_get_board.assert_called_once_with(id="board-2")
        mock_create_board.assert_not_called()
        mock_request_commit.assert_called_once_with(
            orchestrator=orchestrator,
            workflow_run=workflow_run,
            step_execution=step_execution,
            kind="miro_board",
            draft={"snapshot": snapshot},
            commit_context={
                "workflow_run_id": "run-123",
                "merge_output": input_data,
            },
        )

    @patch(
        "agent.approval_gate.request_external_commit",
        side_effect=RuntimeError("Miro service unavailable"),
    )
    def test_gate_error_returns_failure(self, mock_request_commit):
        executor = CreateMiroBoardExecutor(
            _StepStub(),
            _WorkflowRunStub(),
            _OrchestratorStub(approval_required=True),
        )

        result = executor.execute({"miro_snapshot": {"items": []}})

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Miro service unavailable")
        mock_request_commit.assert_called_once()
