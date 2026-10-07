import anthropic
import httpx
import requests
from unittest.mock import MagicMock, call, patch
from agent.executors import AnalyzeDataExecutor, CallLLMExecutor, DetectColumnsExecutor, GenerateCriteriaExecutor
from django.test import TestCase

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
        self.success_criteria = None

    def save(self, update_fields=None):
        self.saved_update_fields.append(update_fields)

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

class _StepStub:
    def __init__(self, step_type, config=None):
        self.step_type = step_type
        self.config = config or {}

class _OrchestratorStub:
    def __init__(self):
        self.user = type("UserStub", (), {"id": 7})()
        self.project = type("ProjectStub", (), {"id": 99})()
        self.session = _SessionStub()
    

class LLMRetryPolicyTests(TestCase):
    @patch('agent.executors.time.sleep')
    @patch('agent.services._run_analysis')
    def test_call_llm_retries_out_success(self, mock_run_analysis, mock_sleep):
        step = _StepStub('analyze_data')
        orchestrator = _OrchestratorStub()
        workflow_run = _WorkflowRunStub()
        executor = AnalyzeDataExecutor(step, workflow_run, orchestrator)
        mock_run_analysis.side_effect = [RuntimeError('API timeout'), RuntimeError('API timeout'), {"recommended_tasks": [], "recommended_decision_tree": {}}]

        result = executor.execute({'spreadsheet_data': 'some data'})
        self.assertEqual(mock_run_analysis.call_count, 3)
        self.assertTrue(result.success)


    @patch('agent.executors.time.sleep')
    @patch('agent.services._run_analysis')
    def test_call_llm_retries_respects_per_step_config_override(self, mock_run_analysis, mock_sleep):
        """
        step.config overrides the decorator's default max_retries.
        AnalyzeDataExecutor normally retries 3 times; with
        config={'max_retries': 1}, retry_policy should try only once and
        skip straight to the on_exhausted branch instead of sleeping and
        retrying.
        """
        step = _StepStub('analyze_data', config={'max_retries': 1})
        orchestrator = _OrchestratorStub()
        workflow_run = _WorkflowRunStub()
        executor = AnalyzeDataExecutor(step, workflow_run, orchestrator)
        mock_run_analysis.side_effect = RuntimeError('API timeout')

        result = executor.execute({'spreadsheet_data': 'some data'})

        self.assertEqual(mock_run_analysis.call_count, 1)
        mock_sleep.assert_not_called()
        self.assertFalse(result.success)
        self.assertFalse(result.skipped)


    @patch('agent.executors.time.sleep')
    @patch('agent.services._run_analysis')
    def test_call_llm_retries_out_failure_failed_step(self, mock_run_analysis, mock_sleep):
        step = _StepStub('analyze_data')
        orchestrator = _OrchestratorStub()
        workflow_run = _WorkflowRunStub()
        executor = AnalyzeDataExecutor(step, workflow_run, orchestrator)
        mock_run_analysis.side_effect = [RuntimeError('API timeout'), RuntimeError('API timeout'), RuntimeError('API timeout')]

        result = executor.execute({'spreadsheet_data': 'some data'})
        self.assertEqual(mock_run_analysis.call_count, 3)
        self.assertFalse(result.success)
        self.assertFalse(result.skipped)


    @patch('agent.executors.time.sleep')
    @patch('agent.column_registry.detect_columns') 
    def test_call_llm_retries_out_failure_skip_step(self, mock_detect_columns, mock_sleep):
        step = _StepStub('detect_columns')
        orchestrator = _OrchestratorStub()
        workflow_run = _WorkflowRunStub()
        executor = DetectColumnsExecutor(step, workflow_run, orchestrator)
        mock_detect_columns.side_effect = [
        RuntimeError('API timeout'),
        RuntimeError('API timeout'),
        RuntimeError('API timeout'),
        ] 
        result = executor.execute({'spreadsheet_data': {'sheets': [{'columns': ['a', 'b'], 'rows': []}]}})
        self.assertEqual(mock_detect_columns.call_count, 3)
        self.assertFalse(result.success)
        self.assertTrue(result.skipped)
    
    
    # The executor now uses the unified caller; patch its local import alias.
    @patch('agent.executors._call_llm_unified', autospec=True)
    @patch('agent.services._get_llm_client')
    def test_call_llm_success_unused_retries(self, mock_get_client, mock_call_llm):
        mock_get_client.return_value = MagicMock()
        mock_call_llm.return_value = {
            "text": '{"anomalies": [], "recommended_tasks": []}',
            "usage": {"input": 10, "output": 2},
        }

        step = _StepStub('call_llm')
        orchestrator = _OrchestratorStub()
        workflow_run = _WorkflowRunStub()
        executor = CallLLMExecutor(step, workflow_run, orchestrator)
        result = executor.execute({'spreadsheet_data': 'some data'})
        self.assertTrue(result.success)
        self.assertEqual(mock_call_llm.call_count, 1)


    @patch('core.services.gemini_client.time.sleep')
    @patch('core.services.gemini_client._get_api_key')
    @patch('core.services.gemini_client.requests.post')
    @patch('agent.llm_client.call_llm')
    def test_generate_criteria_gemini_429_exhausted_skips_without_extra_retries(
        self, mock_call_llm, mock_post, mock_gemini_key, mock_sleep,
    ):
        from core.services.gemini_client import _gemini_request_with_retry

        mock_gemini_key.return_value = 'fake-key'

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response
        )
        mock_post.return_value = mock_response

        def call_llm_side_effect(*args, **kwargs):
            # Runs the real global retry/backoff loop against the mocked 429 response.
            _gemini_request_with_retry('https://example.invalid', {})

        mock_call_llm.side_effect = call_llm_side_effect

        step = _StepStub('generate_criteria')
        orchestrator = _OrchestratorStub()
        workflow_run = _WorkflowRunStub()
        executor = GenerateCriteriaExecutor(step, workflow_run, orchestrator)

        result = executor.execute({
            'spreadsheet_data': {'sheets': [{'columns': ['a', 'b'], 'rows': []}]},
        })

        self.assertEqual(mock_post.call_count, 4)
        self.assertEqual(mock_call_llm.call_count, 1)
        mock_sleep.assert_has_calls([call(2.0), call(4.0), call(8.0)])
        self.assertEqual(mock_sleep.call_count, 3)

        self.assertFalse(result.success)
        self.assertTrue(result.skipped)


    @patch('agent.executors.time.sleep')
    @patch('agent.executors._call_llm_unified', autospec=True)
    @patch('agent.services._get_llm_client')
    def test_call_llm_anthropic_timeout_no_extra_retries(
        self, mock_get_client, mock_call_llm, mock_sleep,
    ):
        mock_get_client.return_value = MagicMock()
        mock_call_llm.side_effect = anthropic.APITimeoutError(
            request=httpx.Request('POST', 'https://api.anthropic.com/v1/messages')
        )

        step = _StepStub('call_llm')
        orchestrator = _OrchestratorStub()
        workflow_run = _WorkflowRunStub()
        executor = CallLLMExecutor(step, workflow_run, orchestrator)

        result = executor.execute({'spreadsheet_data': 'some data'})

        self.assertEqual(mock_call_llm.call_count, 1)
        mock_sleep.assert_not_called()

        self.assertFalse(result.success)
        self.assertFalse(result.skipped)




    






