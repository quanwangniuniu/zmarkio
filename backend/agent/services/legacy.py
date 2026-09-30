"""Legacy (pre-workflow) action confirmation and free-text message handling."""
import logging

from ..models import AgentMessage
from ..agent_utils import serialize_agent_messages
from .followup import _call_ollama_chat, _serialize_project_members
from .miro import MIRO_LEGACY_BG_QUEUED_MESSAGE

logger = logging.getLogger(__name__)


class LegacyMixin:
    """Legacy confirm / chat paths for AgentOrchestrator."""

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
                    result = _call_ollama_chat(
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
