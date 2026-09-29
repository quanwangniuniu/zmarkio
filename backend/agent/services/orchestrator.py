from spreadsheet.providers import SpreadsheetDataProvider

from .analysis import AnalysisMixin
from .approvals import ApprovalMixin
from .calendar import CalendarMixin
from .commits import CommitMixin
from .drafts import DraftMixin
from .followup import FollowUpMixin
from .insights import InsightsMixin
from .legacy import LegacyMixin
from .miro import MiroMixin
from .workflow import WorkflowEngineMixin


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
    LegacyMixin,
):
    """Composes the domain mixins; the only place they are combined.

    Rules for mixin modules (enforced by ``ServicesMixinLayeringTests`` in
    ``agent/tests/test_services_package.py``):
    - never import this module, another ``*Mixin`` class, or the package shim;
    - shared helpers may be imported across submodules only while the import
      graph stays acyclic (prefer moving them into ``common``);
    - method names must be unique across mixins, so the MRO never silently
      picks one implementation over another.
    """

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
