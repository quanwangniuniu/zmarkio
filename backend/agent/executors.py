"""
Step executors — strategy pattern for workflow step types.

Each step_type maps to an Executor subclass that encapsulates
the logic for that particular action.
"""
import logging
import os
from django.core.cache import cache
import time
import anthropic
from core.services.gemini_client import GeminiRetriesExhausted


logger = logging.getLogger(__name__)

def retry_policy(max_retries=3,  retry_delay= 5,on_exhausted='fail'):

    """
    retry_policy is a decorator that wraps a function with retry logic. 
    It attempts to execute the function up to max_retries times in case of 
    specific exceptions (anthropic.APITimeoutError or RuntimeError). 
    If all retries are exhausted, it returns a StepResult indicating failure or skip based on the on_exhausted parameter.

    Args:
        max_retries (int): The maximum number of retry attempts (including the initial attempt).
        retry_delay (int): The delay in seconds between retry attempts.
        on_exhausted (str): The behavior when retries are exhausted ('fail' or 'skip').

    Returns:
        StepResult: The result of the function execution, indicating success or failure.

    Example usage:
        @retry_policy(max_retries=5, on_exhausted='skip')
        def call_llm():
            #llm call logic

    """
    def decorator(func):
        def wrapper(*args, **kwargs):

            #The retry properties are overriden by per-step configuration if that exists.
            effective_max_retries = args[0].config.get('max_retries', max_retries)
            effective_retry_delay = args[0].config.get('retry_delay', retry_delay)
            effective_on_exhausted = args[0].config.get('on_exhausted', on_exhausted)

            for attempt in range(effective_max_retries):
                try:
                    return func(*args, **kwargs)
                except (anthropic.APITimeoutError, RuntimeError) as e:
                    if attempt == effective_max_retries -1:
                        if effective_on_exhausted == 'fail':
                            return StepResult(success=False, error=str(e), skipped=False)
                        else:
                            return StepResult(success=False, error=str(e), skipped=True)
                    else:
                        logger.warning(
                            "LLM call failed: %s. Retry %s/%s in %ss.",
                            e,
                            attempt + 1,
                            effective_max_retries - 1,
                            effective_retry_delay,
                        )
                        time.sleep(effective_retry_delay)
        return wrapper
    return decorator

class StepResult:
    """Unified return type for all executors."""

    def __init__(
        self,
        success,
        output_data=None,
        error=None,
        sse_events=None,
        pause_external_approval=False,
        skipped = False
    ):
        self.success = success
        self.output_data = output_data
        self.error = error
        self.sse_events = sse_events or []
        self.pause_external_approval = pause_external_approval
        self.skipped = skipped

class BaseStepExecutor:
    """Base class — subclasses implement execute()."""

    def __init__(self, step, workflow_run, orchestrator):
        self.step = step
        self.workflow_run = workflow_run
        self.orchestrator = orchestrator
        self.config = step.config or {}
        self.step_execution = None  # set by workflow engine before execute()

    def execute(self, input_data: dict) -> StepResult:
        raise NotImplementedError


class AnalyzeDataExecutor(BaseStepExecutor):
    """Runs the Dify->Claude analysis fallback chain via _run_analysis()."""
    
    @retry_policy(max_retries=3, retry_delay=5, on_exhausted='fail')
    def execute(self, input_data):
        from .services import _run_analysis

        spreadsheet_data = input_data.get('spreadsheet_data')
        if not spreadsheet_data:
            return StepResult(success=False, error='No spreadsheet_data in input')

        try:
            from .generation_registry import (
                GenerationValidationError,
                filter_sse_analysis_payload,
                normalize_generation_outputs,
            )

            user_id = str(self.orchestrator.user.id)
            success_criteria = (
                input_data.get('success_criteria')
                or (self.workflow_run.success_criteria if self.workflow_run.success_criteria else None)
            )
            user_context = cache.get(f"agent:context:{self.workflow_run.id}")
            generation_outputs = input_data.get('generation_outputs')
            requested = frozenset(normalize_generation_outputs(generation_outputs))

            analysis = _run_analysis(
                spreadsheet_data,
                user_id=user_id,
                success_criteria=success_criteria,
                column_mapping=input_data.get('column_mapping'),
                user_context=user_context,
                generation_outputs=list(requested),
                agent_session=self.orchestrator.session,
            )

            self.workflow_run.analysis_result = analysis
            self.workflow_run.save(update_fields=['analysis_result'])

            tasks = analysis.get('recommended_tasks', [])
            tree = analysis.get('recommended_decision_tree') or {}
            tree_nodes = tree.get('nodes') or []
            parts = []
            if tasks:
                parts.append(f"{len(tasks)} recommended task(s)")
            if tree_nodes:
                parts.append(f"{len(tree_nodes)} decision node(s)")
            if parts:
                content = f"Found {' and '.join(parts)}."
            else:
                content = "Analysis complete."

            sse_data = filter_sse_analysis_payload(analysis, requested)
            sse_events = [{
                'type': 'analysis',
                'content': content,
                'data': sse_data,
            }]

            return StepResult(
                success=True,
                output_data={
                    'analysis_result': analysis,
                    'spreadsheet_data': spreadsheet_data,
                    'generation_outputs': list(requested),
                },
                sse_events=sse_events,
            )
        except (anthropic.APITimeoutError, RuntimeError) as e:
            logger.warning("AnalyzeDataExecutor: LLM API timeout: %s", e)
            raise
        except GenerationValidationError as e:
            logger.warning("AnalyzeDataExecutor validation failed: %s", e)
            return StepResult(success=False, error=str(e))
        except GeminiRetriesExhausted as e:
            logger.warning("AnalyzeDataExecutor: Gemini 429 retries exhausted: %s", e)
            return StepResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("AnalyzeDataExecutor failed")
            return StepResult(success=False, error=str(e))


class CallDifyExecutor(BaseStepExecutor):
    """Legacy step type replaced by call_llm. Returns error if called."""

    def execute(self, input_data):
        return StepResult(
            success=False,
            error='call_dify step type is no longer supported. Use analyze_data instead.',
        )


class CallLLMExecutor(BaseStepExecutor):
    """Calls Claude directly, supports per-step config override."""
    
    #Anthropic API call has default retry logic, so the retry policy is simple to avoid double retrying.
    @retry_policy(max_retries=1, retry_delay=0, on_exhausted='fail')
    def execute(self, input_data):
        from .services import _call_llm, _get_llm_client

        spreadsheet_data = input_data.get('spreadsheet_data', input_data)
        try:
            client = _get_llm_client()
            if not client:
                return StepResult(success=False, error='No LLM API key configured')

            result = _call_llm(client, spreadsheet_data, agent_session=self.orchestrator.session)

            return StepResult(
                success=True,
                output_data={
                    'analysis_result': result,
                    'spreadsheet_data': spreadsheet_data,
                },
                sse_events=[{'type': 'text', 'content': 'LLM analysis completed.'}],
            )
        except anthropic.APITimeoutError as e:
            logger.warning("CallLLMExecutor: LLM API timeout: %s", e)
            raise
        except Exception as e:
            logger.exception("CallLLMExecutor failed")
            return StepResult(success=False, error=str(e))


class CreateDecisionExecutor(BaseStepExecutor):
    """Creates Decision tree from analysis recommended_decision_tree via the approval gate."""

    def execute(self, input_data):
        from .approval_gate import KIND_DECISION_TREE, request_external_commit

        analysis = input_data.get('analysis_result')
        if not analysis:
            return StepResult(success=False, error='No analysis_result in input')

        try:
            tree = analysis.get('recommended_decision_tree') or {}
            nodes = tree.get('nodes') or []
            if not nodes:
                return StepResult(
                    success=True,
                    output_data=input_data,
                    sse_events=[{
                        'type': 'text',
                        'content': 'No decision nodes to create.',
                    }],
                )

            draft = {'recommended_decision_tree': tree}
            commit_context = {
                'input_data': input_data,
                'analysis_result': analysis,
            }
            gate = request_external_commit(
                orchestrator=self.orchestrator,
                workflow_run=self.workflow_run,
                step_execution=self.step_execution,
                kind=KIND_DECISION_TREE,
                draft=draft,
                commit_context=commit_context,
            )

            if gate.paused:
                return StepResult(
                    success=True,
                    output_data=input_data,
                    sse_events=gate.sse_events,
                    pause_external_approval=True,
                )

            wf = gate.workflow_run_patch or {}
            created_ids = wf.get('created_decisions') or []
            self.workflow_run.created_decisions = created_ids
            self.workflow_run.save(update_fields=['created_decisions'])

            base_out = gate.output_data or {**input_data, 'analysis_result': analysis}
            return StepResult(
                success=True,
                output_data={**base_out, 'created_decision_ids': created_ids},
                sse_events=gate.sse_events,
            )
        except Exception as e:
            logger.exception("CreateDecisionExecutor failed")
            return StepResult(success=False, error=str(e))


class CreateTasksExecutor(BaseStepExecutor):
    """Creates Tasks from analysis recommended_tasks via the external approval gate."""

    def execute(self, input_data):
        from .approval_gate import KIND_TASK, request_external_commit

        analysis = input_data.get('analysis_result')
        if not analysis:
            return StepResult(success=False, error='No analysis_result in input')

        try:
            # Anomaly confirmation gate: analyses that surfaced anomalies must
            # have them reviewed + confirmed before tasks are created, so
            # data-quality issues are not committed downstream unreviewed. The
            # lightweight in-sheet "spreadsheet insights" flow auto-confirms
            # (anomalies_confirmed + _source='spreadsheet_insights') and is not
            # blocked here. Zero-anomaly analyses proceed unchanged.
            had_anomalies = bool(analysis.get('anomalies'))
            is_insights_flow = analysis.get('_source') == 'spreadsheet_insights'
            if (
                had_anomalies
                and not is_insights_flow
                and not analysis.get('anomalies_confirmed')
            ):
                return StepResult(
                    success=False,
                    error='Anomalies must be confirmed before creating tasks.',
                )

            tasks_data = analysis.get('recommended_tasks', [])
            if not tasks_data:
                return StepResult(success=False, error='No recommended_tasks in analysis.')

            reviewed = analysis.get('reviewed_anomalies') or []
            included_anomalies = [a for a in reviewed if a.get('included', True)]

            decision = self.workflow_run.decision
            draft = {'recommended_tasks': tasks_data}
            commit_context = {
                'input_data': input_data,
                'analysis_result': analysis,
                'decision_id': decision.id if decision else None,
                'included_anomalies': included_anomalies,
                'reviewed_anomalies': reviewed,
            }
            gate = request_external_commit(
                orchestrator=self.orchestrator,
                workflow_run=self.workflow_run,
                step_execution=self.step_execution,
                kind=KIND_TASK,
                draft=draft,
                commit_context=commit_context,
            )

            if gate.paused:
                return StepResult(
                    success=True,
                    output_data=input_data,
                    sse_events=gate.sse_events,
                    pause_external_approval=True,
                )

            wf = gate.workflow_run_patch or {}
            created_ids = wf.get('created_tasks') or []
            self.workflow_run.created_tasks = created_ids
            self.workflow_run.save(update_fields=['created_tasks'])

            base_out = gate.output_data or {**input_data, 'analysis_result': analysis}
            return StepResult(
                success=True,
                output_data={**base_out, 'created_task_ids': created_ids},
                sse_events=gate.sse_events,
            )
        except Exception as e:
            logger.exception("CreateTasksExecutor failed")
            return StepResult(success=False, error=str(e))


class GenerateMiroSnapshotExecutor(BaseStepExecutor):
    """Generate a validated Miro snapshot from workflow context via Gemini."""
    
    @retry_policy(max_retries=3, retry_delay=5, on_exhausted='fail')
    def execute(self, input_data):
        from .miro_generation import (
            build_miro_generation_context_from_run,
            call_gemini_miro_generator,
        )

        try:
            context = build_miro_generation_context_from_run(
                session=self.orchestrator.session,
                workflow_run=self.workflow_run,
            )
            snapshot = call_gemini_miro_generator(
                context,
                user_id=str(self.orchestrator.user.id),
                agent_session=self.orchestrator.session,
            )

            self.workflow_run.miro_snapshot = snapshot
            self.workflow_run.save(update_fields=['miro_snapshot'])

            return StepResult(
                success=True,
                output_data={**input_data, 'miro_snapshot': snapshot},
                sse_events=[{
                    'type': 'miro_snapshot_generated',
                    'content': 'Generated Miro snapshot from workflow results.',
                    'data': {'item_count': len(snapshot.get('items', []))},
                }],
            )
        except RuntimeError as e:
            logger.warning("Gemini API Timeout Error: %s", e)
            raise
        except GeminiRetriesExhausted as e:
            logger.warning("GenerateMiroSnapshotExecutor: Gemini 429 retries exhausted: %s", e)
            return StepResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("GenerateMiroSnapshotExecutor failed")
            return StepResult(success=False, error=str(e))


class CreateMiroBoardExecutor(BaseStepExecutor):
    """Create a persisted Miro board from a validated snapshot."""

    def execute(self, input_data):
        from .approval_gate import KIND_MIRO_BOARD, request_external_commit
        from .miro_board_service import create_board_from_snapshot

        snapshot = input_data.get('miro_snapshot') or self.workflow_run.miro_snapshot
        if not snapshot:
            return StepResult(success=False, error='No miro_snapshot available for board creation')

        try:
            # For sessions that don't require approval, persist immediately.
            # This also keeps unit tests (which use stub workflow runs) isolated from DB lookups.
            if not bool(getattr(self.orchestrator.session, 'approval_required', False)):
                board, persisted_snapshot = create_board_from_snapshot(
                    project=self.orchestrator.project,
                    session=self.orchestrator.session,
                    workflow_run=self.workflow_run,
                    snapshot=snapshot,
                )
                self.workflow_run.miro_board = board
                self.workflow_run.miro_snapshot = persisted_snapshot
                self.workflow_run.save(update_fields=['miro_board', 'miro_snapshot'])
                return StepResult(
                    success=True,
                    output_data={
                        **input_data,
                        'miro_snapshot': persisted_snapshot,
                        'miro_board_id': str(getattr(board, 'id', None)),
                    },
                    sse_events=[{
                        'type': 'miro_board_created',
                        'content': f'Miro board created: {getattr(board, "title", "")}'.strip(),
                        'data': {'board_id': str(getattr(board, 'id', None)), 'board_slug': getattr(board, 'slug', None)},
                    }],
                )

            draft = {'snapshot': snapshot}
            commit_context = {
                'workflow_run_id': str(self.workflow_run.id),
                'merge_output': {**input_data},
            }
            gate = request_external_commit(
                orchestrator=self.orchestrator,
                workflow_run=self.workflow_run,
                step_execution=self.step_execution,
                kind=KIND_MIRO_BOARD,
                draft=draft,
                commit_context=commit_context,
            )
            if gate.paused:
                return StepResult(
                    success=True,
                    output_data=input_data,
                    sse_events=gate.sse_events,
                    pause_external_approval=True,
                )

            wf = gate.workflow_run_patch or {}
            bid = wf.get('miro_board_id')
            persisted_snapshot = wf.get('miro_snapshot')
            if bid:
                # Only hit DB if we actually need to attach the persisted Board object.
                from miro.models import Board

                self.workflow_run.miro_board = Board.objects.get(id=bid)
            if persisted_snapshot is not None:
                self.workflow_run.miro_snapshot = persisted_snapshot
            self.workflow_run.save(update_fields=['miro_board', 'miro_snapshot'])

            return StepResult(
                success=True,
                output_data=gate.output_data
                or {
                    **input_data,
                    'miro_snapshot': persisted_snapshot,
                    'miro_board_id': bid,
                },
                sse_events=gate.sse_events,
            )
        except Exception as e:
            logger.exception("CreateMiroBoardExecutor failed")
            return StepResult(success=False, error=str(e))


class AwaitConfirmationExecutor(BaseStepExecutor):
    """Pauses workflow and sends a confirmation_request SSE event."""

    def execute(self, input_data):
        message = self.config.get('message', 'Please confirm to continue.')
        return StepResult(
            success=True,
            output_data=input_data,
            sse_events=[{
                'type': 'confirmation_request',
                'content': message,
                'data': {'workflow_run_id': str(self.workflow_run.id)},
            }],
        )


class CustomAPIExecutor(BaseStepExecutor):
    """Configurable HTTP request to an external endpoint."""

    def execute(self, input_data):
        import json

        from .approval_gate import KIND_CUSTOM_API, request_external_commit

        method = self.config.get('method', 'POST').upper()
        url = self.config.get('url')
        headers = self.config.get('headers', {})
        body_template = self.config.get('body_template')
        timeout = self.config.get('timeout', 30)

        if not url:
            return StepResult(success=False, error='No URL configured for custom API step')

        try:
            body = None
            if body_template:
                body = json.dumps(input_data) if body_template == '__input__' else body_template

            draft = {'method': method, 'url': url, 'headers': dict(headers), 'body': body}
            commit_context = {
                'method': method,
                'url': url,
                'headers': dict(headers),
                'timeout': timeout,
                'merge_output': {**input_data},
            }
            gate = request_external_commit(
                orchestrator=self.orchestrator,
                workflow_run=self.workflow_run,
                step_execution=self.step_execution,
                kind=KIND_CUSTOM_API,
                draft=draft,
                commit_context=commit_context,
            )
            if gate.paused:
                return StepResult(
                    success=True,
                    output_data=input_data,
                    sse_events=gate.sse_events,
                    pause_external_approval=True,
                )

            return StepResult(
                success=True,
                output_data=gate.output_data or {**input_data},
                sse_events=gate.sse_events,
            )
        except Exception as e:
            logger.exception("CustomAPIExecutor failed")
            return StepResult(success=False, error=str(e))


class DetectColumnsExecutor(BaseStepExecutor):
    """Detect what each column in the uploaded spreadsheet represents.

    Runs rule-based matching first; falls back to LLM for unknown formats.
    Stores the detection result in output_data so the next step can use it.
    Also emits a column_mapping SSE event so the frontend can render a
    confirmation UI for the user to review or correct the mappings.
    """
    #If the Gemini API call fails, we retry a few times before skipping the step. This is non-fatal because workflow can run without column detection.
    @retry_policy(max_retries=3, retry_delay=5, on_exhausted='skip')
    def execute(self, input_data):
        from .column_registry import detect_columns

        spreadsheet_data = input_data.get('spreadsheet_data')
        if not spreadsheet_data:
            return StepResult(success=False, error='No spreadsheet_data in input')

        try:
            # Collect headers and sample rows from the first sheet.
            headers = []
            sample_rows = []
            sheets = spreadsheet_data.get('sheets', [])
            if sheets:
                first_sheet = sheets[0]
                headers = first_sheet.get('columns', [])
                sample_rows = first_sheet.get('rows', [])[:3]

            detection = detect_columns(headers, sample_rows=sample_rows, agent_session=self.orchestrator.session)
            detection_dict = detection.to_dict()

            return StepResult(
                success=True,
                output_data={
                    **input_data,
                    'column_detection': detection_dict,
                    # column_mapping starts as the detected mapping;
                    # the user may override it via confirm_columns.
                    'column_mapping': detection_dict['mappings'],
                },
                sse_events=[{
                    'type': 'column_mapping',
                    'content': (
                        f"Detected schema: {detection.schema_name} "
                        f"(confidence {detection.confidence:.0%}, source: {detection.source})"
                    ),
                    'data': detection_dict,
                }],
            )
        except RuntimeError as e:
            logger.warning("Gemini API Timeout Error: %s", e)
            raise
        except GeminiRetriesExhausted as e:
            logger.warning("DetectColumnsExecutor: Gemini 429 retries exhausted: %s", e)
            return StepResult(success=False, error=str(e), skipped=True)
        except Exception as e:
            logger.exception("DetectColumnsExecutor failed")
            return StepResult(success=False, error=str(e))


class NormalizeDataExecutor(BaseStepExecutor):
    """Rename spreadsheet columns using the approved column_mapping.

    Reads column_mapping from input_data (set by DetectColumnsExecutor or
    overridden by the user during the confirm_columns step).

    After normalisation, persists the confirmed schema to ImportedDataField and
    all row values to ImportedDataRecord so downstream queries never need to
    re-parse the source file or rely on hard-coded column names.
    """

    def execute(self, input_data):
        from .column_registry import normalize_spreadsheet

        spreadsheet_data = input_data.get('spreadsheet_data')
        column_mapping = input_data.get('column_mapping')

        if not spreadsheet_data:
            return StepResult(success=False, error='No spreadsheet_data in input')

        if not column_mapping:
            # No mapping provided — pass data through unchanged
            return StepResult(
                success=True,
                output_data=input_data,
                sse_events=[{
                    'type': 'text',
                    'content': 'No column mapping provided; using original column names.',
                }],
            )

        try:
            normalized = normalize_spreadsheet(spreadsheet_data, column_mapping)

            # Persist schema + row data to the metadata tables when a file_id is
            # available. Failures here are non-fatal: analysis can still proceed
            # using the in-memory normalized data.
            file_id = input_data.get('file_id')
            if file_id:
                try:
                    self._persist_metadata(
                        file_id=file_id,
                        column_mapping=column_mapping,
                        column_detection=input_data.get('column_detection', {}),
                        spreadsheet_data=spreadsheet_data,
                    )
                except Exception:
                    logger.exception(
                        "NormalizeDataExecutor: metadata persistence failed for file_id=%s "
                        "(non-fatal, analysis continues)",
                        file_id,
                    )

            return StepResult(
                success=True,
                output_data={
                    **input_data,
                    'spreadsheet_data': normalized,
                },
                sse_events=[{
                    'type': 'text',
                    'content': 'Column names normalized. Starting analysis...',
                }],
            )
        except Exception as e:
            logger.exception("NormalizeDataExecutor failed")
            return StepResult(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist_metadata(self, file_id, column_mapping, column_detection, spreadsheet_data):
        """
        Save ImportedDataField and ImportedDataRecord rows for the confirmed mapping.

        Clears any existing rows for the file first so re-uploads always reflect
        the latest confirmation.
        """
        from .models import ImportedCSVFile, ImportedDataField, ImportedDataRecord, FieldCategory
        from .column_registry import auto_categorize_by_name, CAT_UNKNOWN

        try:
            csv_file = ImportedCSVFile.objects.get(id=file_id, is_deleted=False)
        except ImportedCSVFile.DoesNotExist:
            logger.warning("NormalizeDataExecutor: ImportedCSVFile %s not found", file_id)
            return

        # Clear existing metadata for this file (re-upload scenario)
        ImportedDataField.objects.filter(file=csv_file).delete()
        ImportedDataRecord.objects.filter(file=csv_file).delete()

        categories = column_detection.get('categories', {})
        confidences = column_detection.get('column_confidences', {})

        # Infer value types from the first sheet's rows
        sheets = spreadsheet_data.get('sheets', [])
        first_rows = sheets[0].get('rows', [])[:50] if sheets else []

        # Build ImportedDataField rows
        fields_to_create = []
        field_by_original = {}  # original_name -> ImportedDataField (to be created)

        for position, (original_name, canonical_name) in enumerate(column_mapping.items()):
            raw_category = categories.get(canonical_name, CAT_UNKNOWN)

            # Auto-categorise unknowns via keyword rules
            if raw_category == CAT_UNKNOWN:
                raw_category = auto_categorize_by_name(
                    original_name if canonical_name == CAT_UNKNOWN else canonical_name
                )

            # Ensure the category exists in FieldCategory (creates if new)
            FieldCategory.get_or_create_by_name(
                raw_category, project=csv_file.project
            )

            col_values = [
                row.get(original_name)
                for row in first_rows
                if row.get(original_name) not in (None, '', '-')
            ]
            value_type = _infer_value_type(col_values)
            stats = _compute_column_stats(
                [row.get(original_name) for row in first_rows],
                value_type,
            )

            field = ImportedDataField(
                file=csv_file,
                original_name=original_name,
                canonical_name=canonical_name,
                value_type=value_type,
                category=raw_category,
                confidence=round(confidences.get(original_name, 0.0), 3),
                position=position,
                null_count=stats['null_count'],
                unique_count=stats['unique_count'],
                min_value=stats['min_value'],
                max_value=stats['max_value'],
                sample_values=stats['sample_values'],
            )
            fields_to_create.append(field)
            field_by_original[original_name] = field

        created_fields = ImportedDataField.objects.bulk_create(fields_to_create)

        # Rebuild lookup after bulk_create assigns PKs
        field_pk_by_original = {f.original_name: f for f in created_fields}

        # Build ImportedDataRecord rows
        total_confirmed = len([
            f for f in created_fields if f.canonical_name != CAT_UNKNOWN
        ])

        records_to_create = []
        for row_index, row in enumerate(first_rows if not sheets else sheets[0].get('rows', [])):
            data_dict = {}
            non_null_confirmed = 0

            for original_name, canonical_name in column_mapping.items():
                key = canonical_name if canonical_name != CAT_UNKNOWN else original_name
                value = row.get(original_name)
                data_dict[key] = value
                if canonical_name != CAT_UNKNOWN and value not in (None, '', '-'):
                    non_null_confirmed += 1

            quality = non_null_confirmed / total_confirmed if total_confirmed else 1.0

            records_to_create.append(ImportedDataRecord(
                file=csv_file,
                row_index=row_index,
                data=data_dict,
                quality_score=round(quality, 3),
            ))

        # Bulk-insert in batches to avoid very large INSERT statements
        batch_size = 500
        for i in range(0, len(records_to_create), batch_size):
            ImportedDataRecord.objects.bulk_create(records_to_create[i:i + batch_size])

        logger.info(
            "NormalizeDataExecutor: persisted %d fields and %d records for file_id=%s",
            len(fields_to_create),
            len(records_to_create),
            file_id,
        )


# ---------------------------------------------------------------------------
# Column metadata helpers used by NormalizeDataExecutor
# ---------------------------------------------------------------------------

def _infer_value_type(values: list) -> str:
    """Infer the value_type ('number', 'boolean', 'string') from a sample of values."""
    non_null = [v for v in values if v not in (None, '', '-')]
    if not non_null:
        return 'string'

    num_count = 0
    bool_count = 0
    for v in non_null[:20]:
        sv = str(v).strip().lower()
        if sv in ('true', 'false', '1', '0', 'yes', 'no'):
            bool_count += 1
        else:
            try:
                float(str(v).replace(',', ''))
                num_count += 1
            except (ValueError, TypeError):
                pass

    sample_size = min(len(non_null), 20)
    if num_count / sample_size >= 0.8:
        return 'number'
    if bool_count / sample_size >= 0.8:
        return 'boolean'
    return 'string'


def _compute_column_stats(values: list, value_type: str) -> dict:
    """Compute null_count, unique_count, min/max, and sample_values for one column."""
    null_count = sum(1 for v in values if v in (None, '', '-'))
    non_null = [v for v in values if v not in (None, '', '-')]
    unique_count = len(set(str(v) for v in non_null))
    sample_values = list({str(v) for v in non_null if v is not None})[:5]

    min_value = None
    max_value = None
    if value_type == 'number' and non_null:
        try:
            nums = [float(str(v).replace(',', '')) for v in non_null]
            min_value = str(min(nums))
            max_value = str(max(nums))
        except (ValueError, TypeError):
            pass

    return {
        'null_count': null_count,
        'unique_count': unique_count,
        'min_value': min_value,
        'max_value': max_value,
        'sample_values': sample_values,
    }


_CRITERIA_SYSTEM_PROMPT = """\
You are a data analysis expert specialising in digital advertising and marketing performance data.

You will receive a JSON array of spreadsheet column names. Your task is to:
1. Identify what type of dataset this is (e.g. Meta Ads Performance, Google Ads, GA4, etc.).
2. For each column, define what valid data looks like and what anomaly rules to apply during analysis.
3. List the key analysis goals for this dataset.

Return ONLY valid JSON with this exact structure (no markdown, no explanation):

{
  "schema_type": "brief name for this dataset type",
  "key_columns": ["list", "of", "most", "important", "columns"],
  "criteria": [
    {
      "column": "exact column name from input",
      "required": true,
      "data_type": "one of: numeric, string, date, boolean",
      "expectation": "description of what valid values look like",
      "anomaly_rule": "description of what counts as suspicious or invalid"
    }
  ],
  "analysis_goals": [
    "specific analysis objective inferred from these columns"
  ]
}

Rules:
- Only include columns that are analytically meaningful (skip pure identifiers like IDs and names unless they are needed for scope).
- For financial columns (spend, cost, revenue, ROAS), always set required: true.
- For ratio columns (CTR, CVR, ROAS), anomaly_rule should specify sensible thresholds (e.g. CTR > 10% is suspicious for most campaigns).
- Return ONLY the JSON object, nothing else.\
"""


class GenerateCriteriaExecutor(BaseStepExecutor):
    """Call Gemini to generate per-column success criteria.

    Sends the column names extracted from the uploaded file to Gemini and
    receives a structured success_criteria JSON that tells the downstream
    analysis step what to look for and how to judge the data.

    The criteria are stored on workflow_run.success_criteria so they persist
    across step boundaries and are forwarded in output_data for the next step.
    """
    #If the Gemini API call fails, we retry a few times before skipping the step. This is non-fatal because analysis can still run without criteria.
    @retry_policy(max_retries=3, retry_delay=5, on_exhausted='skip')
    def execute(self, input_data):
        import json
        from core.services.gemini_client import _get_api_key as _gemini_key
        from .llm_client import call_llm as _call_llm_unified

        if not _gemini_key():
            logger.warning("GenerateCriteriaExecutor: GEMINI_API_KEY not set; skipping")
            return StepResult(
                success=True,
                output_data=input_data,
                sse_events=[{
                    'type': 'text',
                    'content': 'Success criteria generation skipped (no API key).',
                }],
            )

        # Collect all column names from every sheet
        spreadsheet_data = input_data.get('spreadsheet_data', {})
        column_names = []
        for sheet in spreadsheet_data.get('sheets', []):
            column_names.extend(sheet.get('columns', []))
        column_names = list(dict.fromkeys(column_names))  # deduplicate, preserve order

        if not column_names:
            logger.warning("GenerateCriteriaExecutor: no column names found; skipping")
            return StepResult(
                success=True,
                output_data=input_data,
                sse_events=[{
                    'type': 'text',
                    'content': 'Success criteria generation skipped (no columns).',
                }],
            )

        try:
            _criteria_result = _call_llm_unified(
                agent_session=self.orchestrator.session,
                provider='gemini',
                model='gemini-2.5-flash-lite',
                system_prompt=_CRITERIA_SYSTEM_PROMPT,
                user_prompt=(
                    f"Column names:\n{json.dumps(column_names)}\n\n"
                    f"Generate success criteria for these columns now."
                ),
                temperature=0.2,
                max_output_tokens=2048,
                response_mime_type='application/json',
                call_purpose='criteria_generation',
            )
            criteria = json.loads(_criteria_result['text'])

            # Persist on the workflow run so downstream steps can always access it
            self.workflow_run.success_criteria = criteria
            self.workflow_run.save(update_fields=['success_criteria'])

            logger.info(
                "GenerateCriteriaExecutor: criteria generated for %d columns (schema_type=%s)",
                len(column_names),
                criteria.get('schema_type', 'unknown'),
            )

            # Build a human-readable summary of the criteria for the chat UI
            criteria_lines = [
                f"Success criteria generated for **{criteria.get('schema_type', 'this dataset')}**:\n"
            ]
            for item in criteria.get('criteria', []):
                col = item.get('column', '')
                rule = item.get('anomaly_rule', '')
                if col and rule:
                    criteria_lines.append(f"- **{col}**: {rule}")
            if criteria.get('analysis_goals'):
                criteria_lines.append('\nAnalysis goals:')
                for goal in criteria['analysis_goals']:
                    criteria_lines.append(f'  • {goal}')
            criteria_text = '\n'.join(criteria_lines) if len(criteria_lines) > 1 else criteria_lines[0]

            return StepResult(
                success=True,
                output_data={**input_data, 'success_criteria': criteria},
                sse_events=[{
                    'type': 'text',
                    'content': criteria_text,
                }],
            )
        except RuntimeError as e:
            logger.warning("GenerateCriteriaExecutor: LLM API Timeout error: %s", e)
            raise
        except GeminiRetriesExhausted as e:
            logger.warning("GenerateCriteriaExecutor: Gemini 429 retries exhausted: %s", e)
            return StepResult(success=False, error=str(e), skipped=True)
        except Exception as e:
            # Non-fatal: analysis can still run without criteria
            logger.exception("GenerateCriteriaExecutor failed; continuing without criteria")
            return StepResult(
                success=True,
                output_data=input_data,
                sse_events=[{
                    'type': 'text',
                    'content': 'Success criteria generation failed; continuing with analysis.',
                }],
            )


class FlowControlExecutor(BaseStepExecutor):
    """No-op pass-through for UI-only flow control steps (if_else, merge, loop).

    These step types are rendered visually on the canvas but do not yet have
    runtime execution logic.  The executor simply forwards input_data unchanged
    so existing pipelines are not disrupted when flow-control steps are present.
    """

    def execute(self, input_data: dict) -> StepResult:
        return StepResult(success=True, output_data=input_data, sse_events=[])


# Executor registry — maps step_type to executor class
EXECUTOR_REGISTRY = {
    'analyze_data': AnalyzeDataExecutor,
    'call_dify': CallDifyExecutor,
    'call_llm': CallLLMExecutor,
    'create_decision': CreateDecisionExecutor,
    'create_tasks': CreateTasksExecutor,
    'generate_miro_snapshot': GenerateMiroSnapshotExecutor,
    'create_miro_board': CreateMiroBoardExecutor,
    'await_confirmation': AwaitConfirmationExecutor,
    'custom_api': CustomAPIExecutor,
    'detect_columns': DetectColumnsExecutor,
    'normalize_data': NormalizeDataExecutor,
    'generate_criteria': GenerateCriteriaExecutor,
    # Flow-control types (UI canvas; no runtime logic yet)
    'if_else': FlowControlExecutor,
    'merge': FlowControlExecutor,
    'loop': FlowControlExecutor,
}


def get_executor(step, workflow_run, orchestrator):
    """Look up and instantiate the executor for a given step."""
    executor_cls = EXECUTOR_REGISTRY.get(step.step_type)
    if not executor_cls:
        raise ValueError(f"Unknown step type: {step.step_type}")
    return executor_cls(step, workflow_run, orchestrator)
