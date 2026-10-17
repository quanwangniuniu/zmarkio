"""Resuming workflows after an external approval is resolved."""


class ApprovalMixin:
    """External approval resolution for AgentOrchestrator."""

    def resolve_external_approval_stream(
        self,
        approval_id,
        decision,
        draft=None,
        destination=None,
    ):
        """Complete or reject a pending external commit; resume workflow when applicable."""
        from django.utils import timezone as tz

        from ..approval_gate import resolve_pending
        from ..models import AgentPendingExternalApproval
        from miro.models import Board

        try:
            result = resolve_pending(
                orchestrator=self,
                pending_id=str(approval_id),
                decision=decision,
                draft=draft or {},
                destination=destination,
            )
        except ValueError as e:
            yield {'type': 'error', 'content': str(e)}
            return

        for ev in result.sse_events:
            yield ev

        try:
            pending = AgentPendingExternalApproval.objects.get(
                id=approval_id, session=self.session, is_deleted=False,
            )
        except AgentPendingExternalApproval.DoesNotExist:
            return

        wr = pending.workflow_run
        ex = pending.step_execution

        if decision == 'reject':
            if wr:
                wr.status = 'failed'
                wr.error_message = 'External action rejected by user.'
                wr.save(update_fields=['status', 'error_message', 'updated_at'])
            if ex:
                ex.status = 'failed'
                ex.error_message = 'Rejected by user'
                ex.completed_at = tz.now()
                ex.save(update_fields=['status', 'error_message', 'completed_at', 'updated_at'])
            return

        if ex and wr and wr.workflow_definition_id:
            ex.status = 'completed'
            ex.output_data = result.output_data
            ex.completed_at = tz.now()
            ex.save(update_fields=['status', 'output_data', 'completed_at', 'updated_at'])

            wf_patch = result.workflow_run_patch or {}
            uf = ['status', 'current_step_order', 'updated_at', 'error_message']
            wr.error_message = None
            if 'created_tasks' in wf_patch:
                wr.created_tasks = wf_patch['created_tasks']
                uf.append('created_tasks')
            if 'created_decisions' in wf_patch:
                wr.created_decisions = wf_patch['created_decisions']
                uf.append('created_decisions')
            if wf_patch.get('miro_board_id'):
                wr.miro_board = Board.objects.get(id=wf_patch['miro_board_id'])
                uf.append('miro_board')
            if wf_patch.get('miro_snapshot') is not None:
                wr.miro_snapshot = wf_patch['miro_snapshot']
                uf.append('miro_snapshot')

            wr.status = 'analyzing'
            wr.current_step_order = ex.step_order + 1
            wr.save(update_fields=uf)

            yield from self._execute_steps(wr, result.output_data or {})
