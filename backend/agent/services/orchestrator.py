import logging
import requests
from django.conf import settings
from django.contrib.contenttypes.models import ContentType

from spreadsheet.providers import (
    SpreadsheetDataProvider,
)
from task.models import Task
from ..models import AgentMessage
from ..agent_utils import serialize_agent_messages
from .analysis import AnalysisMixin
from .approvals import ApprovalMixin
from .calendar import CalendarMixin
from .commits import CommitMixin
from .drafts import DraftMixin
from .followup import FollowUpMixin, _call_gemini_chat, _serialize_project_members
from .insights import InsightsMixin
from .miro import MIRO_LEGACY_BG_QUEUED_MESSAGE, MiroMixin
from .workflow import WorkflowEngineMixin

logger = logging.getLogger(__name__)


class AgentOrchestrator(
    WorkflowEngineMixin,
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
