"""Workflow step engine: resolve a workflow, prepare input, run and resume steps."""
import logging

from django.core.cache import cache

from ..models import (
    AgentWorkflowRun, ImportedCSVFile,
    AgentWorkflowDefinition, AgentStepExecution,
)
from .. import data_service
from core.services import file_parser

logger = logging.getLogger(__name__)


class WorkflowEngineMixin:
    """Workflow resolution and step execution for AgentOrchestrator."""

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
