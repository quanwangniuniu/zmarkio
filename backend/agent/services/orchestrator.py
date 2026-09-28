import logging
import requests
from django.core.cache import cache
from django.conf import settings
from django.contrib.contenttypes.models import ContentType

from spreadsheet.providers import (
    SpreadsheetDataProvider,
)
from task.models import Task
from ..models import (
    AgentMessage, AgentWorkflowRun, ImportedCSVFile,
    AgentWorkflowDefinition, AgentStepExecution,
)
from .. import data_service
from core.services import file_parser
from ..agent_utils import serialize_agent_messages
from .analysis import AnalysisMixin
from .approvals import ApprovalMixin
from .calendar import CalendarMixin
from .commits import CommitMixin
from .drafts import DraftMixin
from .followup import FollowUpMixin, _call_gemini_chat, _serialize_project_members
from .insights import InsightsMixin
from .miro import MIRO_LEGACY_BG_QUEUED_MESSAGE, MiroMixin

logger = logging.getLogger(__name__)


class AgentOrchestrator(
    AnalysisMixin,
    InsightsMixin,
    CalendarMixin,
    DraftMixin,
    FollowUpMixin,
    CommitMixin,
    ApprovalMixin,
    MiroMixin,
):
    def __init__(self, user, project, session):
        self.user = user
        self.project = project
        self.session = session
        self.spreadsheet_provider = SpreadsheetDataProvider(user)

    def handle_message(self, message, spreadsheet_id=None, sheet_id=None, csv_filename=None,
                       action=None, file_id=None, calendar_context=None,
                       draft_context=None,
                       workflow_id=None, column_mapping=None,
                       approval_id=None, approval_decision=None,
                       approval_draft=None, generation_outputs=None, user_context=None,
                       reviewed_anomalies=None):
        """Main entry point. Routes calendar context first, then workflow engine or legacy logic.

        Yields SSE chunks as dicts.
        """
        if action == 'analyze_spreadsheet_insights':
            yield from self.analyze_spreadsheet_insights(
                spreadsheet_id, sheet_id=sheet_id,
            )
            yield {"type": "done"}
            return

        if action == 'confirm_anomalies':
            latest_run = self.session.workflow_runs.filter(
                is_deleted=False
            ).order_by('-created_at').first()
            yield from self.confirm_anomalies(latest_run, reviewed_anomalies)
            yield {"type": "done"}
            return

        if action == 'resolve_external_approval':
            if not approval_id or not approval_decision:
                yield {'type': 'error', 'content': 'approval_id and approval_decision are required.'}
                return
            yield from self.resolve_external_approval_stream(
                approval_id,
                approval_decision,
                draft=approval_draft,
                destination=None,
            )
            yield {'type': 'done'}
            return

        # --- Calendar context takes priority over all other routing ---
        if calendar_context:
            yield from self.answer_calendar_question(message, calendar_context)
            yield {"type": "done"}
            return

        # --- Draft context: answer using the draft's real content (read-only) ---
        if draft_context:
            yield from self.answer_draft_question(message, draft_context)
            yield {"type": "done"}
            return

        if action == 'create_decisions':
            latest_run = self.session.workflow_runs.filter(
                is_deleted=False
            ).order_by('-created_at').first()

            if latest_run and self._workflow_run_analysis(latest_run).get(
                'recommended_decision_tree', {}
            ).get('nodes'):
                yield from self.create_decisions_from_analysis(latest_run)
                yield {'type': 'done'}
                return
            if latest_run and latest_run.workflow_definition:
                yield from self._resume_workflow(latest_run)
                yield {'type': 'done'}
                return
            yield {'type': 'error', 'content': 'No analysis found to create decisions from.'}
            yield {'type': 'done'}
            return

        # --- Resume a paused workflow ---
        if action == 'create_tasks':
            latest_run = self.session.workflow_runs.filter(
                is_deleted=False
            ).order_by('-created_at').first()

            if latest_run and self._workflow_run_analysis(latest_run).get('recommended_tasks'):
                # Commit tasks from the stored analysis instead of resuming the
                # workflow, which may pause again on legacy await_confirmation steps.
                yield from self.create_tasks_from_analysis(latest_run)
                yield {"type": "done"}
                return
            if latest_run and latest_run.workflow_definition:
                yield from self._resume_workflow(latest_run)
                yield {"type": "done"}
                return
            yield from self._legacy_confirm(action, latest_run)
            yield {"type": "done"}
            return

        # Resume a workflow paused at await_confirmation (user clicked Continue in chat).
        if action == 'resume_workflow':
            latest_run = self.session.workflow_runs.filter(
                status='awaiting_confirmation',
                is_deleted=False,
            ).order_by('-created_at').first()
            if latest_run and latest_run.workflow_definition:
                yield from self._resume_workflow(latest_run)
            else:
                yield {
                    'type': 'text',
                    'content': (
                        'This workflow has already finished or was continued. '
                        'Start a new message to run it again.'
                    ),
                }
            yield {'type': 'done'}
            return

        # Resume after user confirms / edits the detected column mapping.
        if action == 'confirm_columns':
            latest_run = self.session.workflow_runs.filter(
                is_deleted=False
            ).order_by('-created_at').first()

            if latest_run and latest_run.workflow_definition:
                # Inject the user-approved mapping so NormalizeDataExecutor
                # can pick it up from input_data.
                extra = {'column_mapping': column_mapping} if column_mapping else {}
                yield from self._resume_workflow(latest_run, extra_input=extra)
            else:
                yield {"type": "error", "content": "No paused workflow to confirm."}
            yield {"type": "done"}
            return

        if action == 'generate_miro':
            latest_run = self.session.workflow_runs.filter(
                is_deleted=False
            ).order_by('-created_at').first()
            yield from self._legacy_confirm(action, latest_run)
            yield {"type": "done"}
            return

        if action == 'start_follow_up':
            latest_run = self.session.workflow_runs.filter(
                status='awaiting_confirmation',
                analysis_result__isnull=False,
                is_deleted=False,
            ).order_by('-created_at').first()
            yield from self._start_follow_up(latest_run)
            yield {"type": "done"}
            return

        if action == 'cancel_follow_up':
            latest_run = self.session.workflow_runs.filter(
                status='awaiting_confirmation',
                analysis_result__isnull=False,
                is_deleted=False,
            ).order_by('-created_at').first()
            yield from self._cancel_follow_up(latest_run)
            yield {"type": "done"}
            return

        # --- Start a new workflow (file upload / analyze action / explicit workflow_id) ---
        if file_id or spreadsheet_id or csv_filename or (action == 'analyze') or workflow_id:
            workflow_def = self._resolve_workflow(
                workflow_id=workflow_id,
                action=action,
                file_id=file_id,
                user_message=message
            )
            if workflow_def:
                yield from self._start_workflow(
                    workflow_def,
                    file_id=file_id,
                    spreadsheet_id=spreadsheet_id,
                    csv_filename=csv_filename,
                    generation_outputs=generation_outputs,
                    user_context=user_context,
                )
                yield {"type": "done"}
                return

        # --- No workflow match → full legacy logic (includes follow-up chat) ---
        yield from self._legacy_handle(
            message, spreadsheet_id, csv_filename, action, file_id
        )

    # ------------------------------------------------------------------
    # Workflow engine methods (AGENT-9)
    # ------------------------------------------------------------------

    def _resolve_workflow(
        self,
        workflow_id=None,
        action=None,
        file_id=None,
        spreadsheet_id=None,
        csv_filename=None,
        user_message='',
    ):
        """
        Resolve which workflow definition to run.

        - Explicit workflow_id: user-selected template/workflow (highest priority).
        - File upload / analyze / spreadsheet paths: system default workflow (legacy bot).
        - Plain text without workflow_id: no workflow (handled by legacy chat).
        """
        if workflow_id:
            try:
                return AgentWorkflowDefinition.objects.get(
                    id=workflow_id, status='active', is_deleted=False,
                )
            except AgentWorkflowDefinition.DoesNotExist:
                return None

        if file_id or action == 'analyze' or spreadsheet_id or csv_filename:
            return self._get_system_default_workflow()

        return None

    def _get_system_default_workflow(self):
        """Get system default workflow."""
        return AgentWorkflowDefinition.objects.filter(
            project__isnull=True, is_system=True, is_default=True,
            status='active', is_deleted=False,
        ).first()

    def _prepare_input_data(self, file_id=None, spreadsheet_id=None, csv_filename=None):
        """Build the initial input_data dict for the workflow engine.

        file_id is included in the returned dict so NormalizeDataExecutor can
        persist confirmed column mappings and row data to ImportedDataField /
        ImportedDataRecord without needing a separate DB lookup.
        """
        import os as _os

        if file_id:
            record = ImportedCSVFile.objects.get(
                id=file_id, project=self.project, is_deleted=False,
            )
            csv_dir = data_service._get_csv_dir()
            filepath = _os.path.join(csv_dir, _os.path.basename(record.filename))
            return {
                'spreadsheet_data': file_parser.parse_file_to_json(filepath, record.filename),
                'file_id': str(file_id),
            }

        if spreadsheet_id:
            payload = self.spreadsheet_provider.get_analysis_payload(spreadsheet_id)
            return {
                'spreadsheet_data': payload,
                'spreadsheet_id': payload['id'],
            }

        if csv_filename:
            record = ImportedCSVFile.objects.get(
                filename=csv_filename, project=self.project, is_deleted=False,
            )
            csv_dir = data_service._get_csv_dir()
            filepath = _os.path.join(csv_dir, _os.path.basename(record.filename))
            columns, rows = data_service._read_csv_file(filepath)
            return {
                'spreadsheet_data': {
                    'name': record.original_filename,
                    'sheets': [{'name': 'Sheet1', 'columns': columns, 'rows': rows}],
                },
                'file_id': str(record.id),
            }

        return {}

    def _start_workflow(self, workflow_def, file_id=None, spreadsheet_id=None,
                        csv_filename=None, generation_outputs=None, user_context=None):
        """Create a new WorkflowRun and execute steps."""
        from ..generation_registry import normalize_generation_outputs

        outputs = normalize_generation_outputs(generation_outputs)
        input_data = self._prepare_input_data(
            file_id=file_id,
            spreadsheet_id=spreadsheet_id,
            csv_filename=csv_filename,
        )
        input_data['generation_outputs'] = outputs

        # _prepare_input_data's spreadsheet branch already access-checked and
        # returns the id; otherwise (e.g. file-upload path) re-check the raw id.
        resolved_spreadsheet_id = input_data.get('spreadsheet_id')
        if resolved_spreadsheet_id is None and spreadsheet_id:
            resolved_spreadsheet_id = self.spreadsheet_provider.accessible_spreadsheet_id(
                spreadsheet_id
            )

        workflow_run = AgentWorkflowRun.objects.create(
            session=self.session,
            workflow_definition=workflow_def,
            status='analyzing',
            current_step_order=1,
            spreadsheet_id=resolved_spreadsheet_id,
            generation_outputs_requested=outputs,
        )
        if user_context:
            cache.set(f"agent:context:{workflow_run.id}", user_context, 3600)

        yield from self._execute_steps(workflow_run, input_data)

    def _execute_steps(self, workflow_run, input_data):
        """Run steps in order. Pause on await_confirmation. Record AgentStepExecution."""
        from ..executors import get_executor
        from ..generation_registry import normalize_generation_outputs, should_skip_workflow_step
        from django.utils import timezone as tz

        steps = workflow_run.workflow_definition.steps.filter(
            order__gte=workflow_run.current_step_order, is_deleted=False,
        ).order_by('order')

        total_steps = workflow_run.workflow_definition.steps.filter(
            is_deleted=False
        ).count()
        current_data = input_data
        requested = frozenset(
            normalize_generation_outputs(input_data.get('generation_outputs'))
        )

        if not steps.exists():
            workflow_run.status = 'completed'
            workflow_run.save(update_fields=['status', 'updated_at'])
            yield {
                'type': 'text',
                'content': (
                    f'**{workflow_run.workflow_definition.name}** completed. '
                    'There are no further steps in this workflow.'
                ),
            }
            return

        for step in steps:
            if should_skip_workflow_step(step.step_type, requested):
                yield {
                    'type': 'step_progress',
                    'data': {
                        'step_order': step.order,
                        'step_name': step.name,
                        'step_type': step.step_type,
                        'status': 'skipped',
                        'total_steps': total_steps,
                    },
                }
                workflow_run.current_step_order = step.order + 1
                workflow_run.save(update_fields=['current_step_order'])
                continue

            execution = AgentStepExecution.objects.create(
                workflow_run=workflow_run,
                step=step,
                step_order=step.order,
                step_name=step.name,
                status='running',
                input_data=current_data,
                started_at=tz.now(),
            )

            yield {
                'type': 'step_progress',
                'data': {
                    'step_order': step.order,
                    'step_name': step.name,
                    'step_type': step.step_type,
                    'status': 'running',
                    'total_steps': total_steps,
                },
            }

            executor = get_executor(step, workflow_run, self)
            executor.step_execution = execution
            result = executor.execute(current_data)

            if result.success:
                if getattr(result, 'pause_external_approval', False):
                    execution.status = 'awaiting'
                    execution.save(update_fields=['status', 'updated_at'])
                    workflow_run.status = 'awaiting_external_approval'
                    workflow_run.save(update_fields=['status', 'updated_at'])
                    for event in result.sse_events:
                        yield event
                    return

                execution.status = 'completed'
                execution.output_data = result.output_data
                execution.completed_at = tz.now()
                execution.save()

                for event in result.sse_events:
                    yield event

                if step.step_type == 'analyze_data':
                    yield from self._emit_calendar_events_if_requested(
                        workflow_run, current_data
                    )

                # Pause on await_confirmation — persist status BEFORE yielding SSE
                # so a fast "Continue" click cannot race ahead of the DB write.
                if step.step_type == 'await_confirmation':
                    workflow_run.status = 'awaiting_confirmation'
                    workflow_run.current_step_order = step.order + 1
                    workflow_run.save(
                        update_fields=['status', 'current_step_order', 'updated_at']
                    )
                    for event in result.sse_events:
                        yield event
                    return

                current_data = result.output_data or current_data
            else:
                if result.skipped:
                    execution.status = 'skipped'
                    execution.completed_at = tz.now()
                    execution.save(update_fields=['status', 'updated_at'])

                    logger.warning("Workflow step skipped after retries exhausted: run_id=%s, step=%s, step_type=%s", workflow_run.id, step.name, step.step_type)

                    #Provide explanation for users to understand why this step didn't run
                    yield {
                        'type': 'text',
                        'content': f'The "{step.name}" step was skipped after retries. Continuing with the remaining steps.',
                    }
                    yield {
                        'type': 'step_progress',
                        'data': {
                            'step_order': step.order,
                            'step_name': step.name,
                            'step_type': step.step_type,
                            'status': 'skipped',
                            'total_steps': total_steps,
                        },
                    }
                    workflow_run.current_step_order = step.order + 1
                    workflow_run.save(update_fields=['current_step_order'])
                    continue
                else:
                    execution.status = 'failed'
                    execution.error_message = result.error
                    execution.completed_at = tz.now()
                    execution.save()

                    workflow_run.status = 'failed'
                    workflow_run.error_message = result.error
                    workflow_run.save()

                    yield {'type': 'error', 'content': result.error}
                    return


        workflow_run.status = 'completed'
        workflow_run.save(update_fields=['status', 'updated_at'])
        yield {
            'type': 'text',
            'content': (
                f'**{workflow_run.workflow_definition.name}** completed successfully.'
            ),
        }

    def _resume_workflow(self, workflow_run, extra_input=None):
        """Resume a paused workflow from the last completed step's output.

        extra_input is merged into the input data before execution, allowing
        callers to inject user-provided values (e.g. confirmed column_mapping).
        """
        last_execution = workflow_run.step_executions.filter(
            status='completed'
        ).order_by('-step_order').first()

        input_data = last_execution.output_data if last_execution else {}
        if extra_input:
            input_data = {**input_data, **extra_input}
        yield from self._execute_steps(workflow_run, input_data)

    def _legacy_confirm(self, action, workflow_run):
        """Backward compat: create_tasks for legacy runs."""
        if action == 'create_tasks':
            if workflow_run and workflow_run.analysis_result:
                yield from self.create_tasks_from_analysis(workflow_run)
            else:
                yield {"type": "error", "content": "No analysis found to create tasks from."}
        elif action == 'generate_miro':
            if not workflow_run or not workflow_run.analysis_result:
                yield {"type": "error", "content": "No analysis found to generate a Miro board from."}
                return
            try:
                outcome, locked_run, board_title = self._legacy_start_miro_background_if_needed(
                    workflow_run
                )
            except Exception as e:
                logger.exception(
                    "Failed to enqueue legacy Miro generation for workflow_run=%s",
                    getattr(workflow_run, 'id', workflow_run),
                )
                yield {"type": "error", "content": f"Failed to start Miro generation: {e}"}
                return

            if outcome == 'already_exists':
                logger.info(
                    "Generate Miro requested but board already exists for workflow_run=%s board=%s",
                    locked_run.id,
                    locked_run.miro_board_id,
                )
                yield {
                    "type": "text",
                    "content": f"Miro board already exists: {board_title}",
                }
                return

            yield {
                "type": "miro_status",
                "content": MIRO_LEGACY_BG_QUEUED_MESSAGE,
                "data": {"workflow_run_id": str(locked_run.id), "status": "running"},
            }

    def _legacy_handle(self, message, spreadsheet_id=None, csv_filename=None,
                       action=None, file_id=None):
        """Full legacy logic — preserves original handle_message behavior
        including the follow-up chat path."""
        if file_id:
            yield from self.analyze_file(file_id)
            yield {"type": "done"}
            return
        if action == 'analyze' and csv_filename:
            yield from self.analyze_csv(csv_filename)
        elif action == 'analyze' and spreadsheet_id:
            yield from self.analyze_spreadsheet(spreadsheet_id)
        elif action == 'create_tasks':
            workflow_run = self.session.workflow_runs.filter(
                analysis_result__isnull=False
            ).order_by('-created_at').first()
            if workflow_run and workflow_run.analysis_result:
                yield from self.create_tasks_from_analysis(workflow_run)
            else:
                yield {"type": "error", "content": "No analysis found to create tasks from."}
        else:
            # Follow-up chat path
            latest_run = self.session.workflow_runs.filter(
                status='awaiting_confirmation',
                chat_follow_up_started=True,
                chat_followed_up=False,
            ).order_by('-created_at').first()

            if latest_run:
                yield {"type": "text", "content": "Thinking..."}
                history = AgentMessage.objects.filter(
                    session=self.session
                ).order_by('created_at')
                chat_context = serialize_agent_messages(history)
                full_input = f"{chat_context}\n\n[user]: {message}"
                try:
                    from core.utils.bot_user import get_agent_bot_user

                    bot = get_agent_bot_user()
                    project_members = _serialize_project_members(
                        self.project,
                        excluded_users=[bot],
                    )
                    logger.info(
                        "Running agent follow-up chat for project=%s session=%s workflow_run=%s user=%s project_members=%s",
                        self.project.id,
                        self.session.id,
                        latest_run.id,
                        self.user.id,
                        len(project_members),
                    )
                    result = _call_gemini_chat(
                        full_input,
                        user_id=self.user.id,
                        analysis_result=latest_run.analysis_result,
                        project_members=project_members,
                        current_username=self.user.username or '',
                        agent_session=self.session,
                    )
                    follow_up_status = result.get("status", "completed")
                    reply = result.get("text") or result.get("reply", "")
                    forwards = result.get("forwards", [])
                    close_follow_up = follow_up_status == 'completed' or bool(forwards)
                    logger.info(
                        "Agent follow-up chat completed for workflow_run=%s status=%s forwards=%s close_follow_up=%s",
                        latest_run.id,
                        follow_up_status,
                        len(forwards),
                        close_follow_up,
                    )

                    if close_follow_up:
                        latest_run.chat_followed_up = True
                        latest_run.save(update_fields=['chat_followed_up'])
                    yield {"type": "text", "content": reply}

                    if forwards:
                        from ..approval_gate import KIND_FORWARD_MESSAGE, request_external_commit

                        gate = request_external_commit(
                            orchestrator=self,
                            workflow_run=latest_run,
                            step_execution=None,
                            kind=KIND_FORWARD_MESSAGE,
                            draft={'forwards': forwards},
                            commit_context={},
                        )
                        for ev in gate.sse_events:
                            yield ev
                except Exception as e:
                    logger.error(f"Dify chat call failed: {e}")
                    yield {"type": "error", "content": str(e)}
            else:
                yield {
                    "type": "text",
                    "content": (
                        "I can help you analyze spreadsheet data and recommended tasks. "
                        "To get started, select a spreadsheet and use the 'analyze' action."
                    ),
                }
        yield {"type": "done"}
