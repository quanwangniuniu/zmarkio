"""Committing analysis results: confirmed anomalies, decisions and tasks."""
from ..models import AgentMessage


class CommitMixin:
    """Anomaly / decision / task commit entry points for AgentOrchestrator."""

    def _workflow_run_analysis(self, workflow_run):
        """Return analysis payload for a run, including completed step output."""
        analysis = workflow_run.analysis_result
        if isinstance(analysis, dict) and analysis.get('recommended_tasks'):
            return analysis

        last_execution = workflow_run.step_executions.filter(
            status='completed'
        ).order_by('-step_order').first()
        output_data = getattr(last_execution, 'output_data', None) or {}
        if isinstance(output_data, dict):
            step_analysis = output_data.get('analysis_result')
            if isinstance(step_analysis, dict) and step_analysis.get('recommended_tasks'):
                workflow_run.analysis_result = step_analysis
                workflow_run.save(update_fields=['analysis_result', 'updated_at'])
                return step_analysis

        return analysis if isinstance(analysis, dict) else {}

    def confirm_anomalies(self, workflow_run, reviewed_anomalies):
        """Persist the user's reviewed anomaly list and unlock task creation.

        Guard order is deliberate: the already-confirmed no-op runs BEFORE any
        payload validation so a reload/retry that sends a partial or stale
        payload no-ops cleanly instead of raising a validation error.
        """
        # 1. Missing run/analysis.
        analysis = self._workflow_run_analysis(workflow_run) if workflow_run else {}
        if not workflow_run or not isinstance(analysis, dict) or not analysis:
            yield {"type": "error", "content": "No analysis to confirm."}
            return

        # 2. Already confirmed -> safe idempotent no-op (before validation).
        if analysis.get('anomalies_confirmed'):
            yield {
                "type": "anomalies_confirmed",
                "content": "Anomalies already confirmed.",
                "data": analysis,
                "already_confirmed": True,
            }
            return

        stored = analysis.get('anomalies') or []
        existing_by_id = {a.get('id'): a for a in stored if isinstance(a, dict)}

        payload = reviewed_anomalies if isinstance(reviewed_anomalies, list) else []
        payload_ids = [
            entry.get('id') for entry in payload if isinstance(entry, dict)
        ]

        # 3. Completeness check (atomic): payload ids must exactly equal the
        #    stored anomaly id set -- reject on unknown / missing / duplicate.
        stored_ids = set(existing_by_id.keys())
        payload_id_set = set(payload_ids)
        unknown = sorted(i for i in payload_id_set if i not in stored_ids)
        missing = sorted(i for i in stored_ids if i not in payload_id_set)
        duplicate = sorted({i for i in payload_ids if payload_ids.count(i) > 1})
        if unknown or missing or duplicate:
            yield {
                "type": "error",
                "content": (
                    f"Anomaly review incomplete: unknown={unknown}, "
                    f"missing={missing}, duplicate={duplicate}"
                ),
            }
            return

        # 4. Validate + merge each entry onto the full stored anomaly object.
        valid_severities = {'critical', 'warning', 'info'}
        merged = []
        for entry in payload:
            anomaly_id = entry.get('id')
            base = dict(existing_by_id[anomaly_id])
            severity = entry.get('severity')
            if severity is not None:
                if severity not in valid_severities:
                    yield {
                        "type": "error",
                        "content": f"Invalid severity '{severity}' for {anomaly_id}.",
                    }
                    return
                base['severity'] = severity
            description = entry.get('description')
            if description is not None:
                if not isinstance(description, str):
                    yield {
                        "type": "error",
                        "content": f"Invalid description for {anomaly_id}.",
                    }
                    return
                base['description'] = description.strip()[:1000]
            base['included'] = bool(entry.get('included', True))
            merged.append(base)

        # 5. Persist reviewed list + confirmation flag (keep original anomalies).
        analysis['reviewed_anomalies'] = merged
        analysis['anomalies_confirmed'] = True
        workflow_run.analysis_result = analysis
        workflow_run.save(update_fields=['analysis_result', 'updated_at'])

        # Update the stored analysis message so a reload restores the same card
        # in its locked, reviewed state (rather than an editable duplicate).
        analysis_message = (
            AgentMessage.objects
            .filter(session=self.session, role='assistant', metadata__has_key='anomalies')
            .order_by('-created_at')
            .first()
        )
        if analysis_message:
            meta = analysis_message.metadata or {}
            meta['anomalies_confirmed'] = True
            meta['reviewed_anomalies'] = merged
            analysis_message.metadata = meta
            analysis_message.save(update_fields=['metadata'])

        included_count = sum(1 for a in merged if a.get('included'))
        # 6. Emit confirmation with full merged objects so the UI can re-render.
        yield {
            "type": "anomalies_confirmed",
            "content": f"Anomalies confirmed ({included_count} included).",
            "data": analysis,
        }

    def create_decisions_from_analysis(self, workflow_run):
        """Create Decision tree directly from analysis results."""
        yield {'type': 'text', 'content': 'Creating decisions...'}

        existing_decision_ids = getattr(workflow_run, 'created_decisions', []) or []
        if existing_decision_ids:
            yield {
                'type': 'decision_draft',
                'content': f'Decisions already created ({len(existing_decision_ids)}).',
                'data': {
                    'decision_ids': existing_decision_ids,
                },
            }
            return

        analysis = self._workflow_run_analysis(workflow_run)
        tree = (analysis or {}).get('recommended_decision_tree') or {}
        nodes = tree.get('nodes') or []
        if not nodes:
            yield {'type': 'text', 'content': 'No decision nodes found in analysis.'}
            return

        from ..approval_gate import KIND_DECISION_TREE, request_external_commit

        draft = {'recommended_decision_tree': tree}
        commit_context = {
            'input_data': {'analysis_result': analysis},
            'analysis_result': analysis,
        }
        gate = request_external_commit(
            orchestrator=self,
            workflow_run=workflow_run,
            step_execution=None,
            kind=KIND_DECISION_TREE,
            draft=draft,
            commit_context=commit_context,
        )
        for ev in gate.sse_events:
            yield ev
        if gate.paused:
            return

        decision_ids = (gate.workflow_run_patch or {}).get('created_decisions') or []
        workflow_run.created_decisions = decision_ids
        workflow_run.save(update_fields=['created_decisions'])

    def create_tasks_from_analysis(self, workflow_run):
        """Create Tasks directly from analysis recommended_tasks.

        Recommended tasks are independent of anomaly review state; explicit
        create_tasks always commits from ``recommended_tasks`` when present.
        """
        yield {"type": "text", "content": "Creating tasks..."}

        existing_task_ids = getattr(workflow_run, "created_tasks", []) or []
        if existing_task_ids:
            decision = workflow_run.decision
            yield {
                "type": "task_created",
                "content": f"Tasks already created ({len(existing_task_ids)}).",
                "data": {
                    "task_ids": existing_task_ids,
                    "decision_id": decision.id if decision else None,
                },
            }
            return

        analysis = self._workflow_run_analysis(workflow_run)

        # Anomaly confirmation gate: when the analysis surfaced anomalies, they
        # must be reviewed + confirmed before tasks are created, so data-quality
        # issues do not silently propagate downstream. The lightweight in-sheet
        # "spreadsheet insights" path auto-confirms (it sets anomalies_confirmed
        # and _source='spreadsheet_insights'), so it is not blocked here.
        had_anomalies = bool(analysis.get('anomalies'))
        is_insights_flow = analysis.get('_source') == 'spreadsheet_insights'
        if (
            had_anomalies
            and not is_insights_flow
            and not analysis.get('anomalies_confirmed')
        ):
            yield {
                "type": "error",
                "content": "Anomalies must be confirmed before creating tasks.",
            }
            return

        recommended_tasks = analysis.get("recommended_tasks", [])
        if not recommended_tasks:
            yield {"type": "error", "content": "No recommended tasks found in analysis."}
            return

        reviewed = analysis.get('reviewed_anomalies') or []
        included_anomalies = [a for a in reviewed if a.get('included', True)]

        decision = workflow_run.decision
        from ..approval_gate import KIND_TASK, request_external_commit

        draft = {'recommended_tasks': recommended_tasks}
        commit_context = {
            'input_data': {'analysis_result': analysis},
            'analysis_result': analysis,
            'decision_id': decision.id if decision else None,
            'included_anomalies': included_anomalies,
            'reviewed_anomalies': reviewed,
        }
        gate = request_external_commit(
            orchestrator=self,
            workflow_run=workflow_run,
            step_execution=None,
            kind=KIND_TASK,
            draft=draft,
            commit_context=commit_context,
        )
        for ev in gate.sse_events:
            yield ev
        if gate.paused:
            return

        task_ids = (gate.workflow_run_patch or {}).get('created_tasks') or []
        workflow_run.created_tasks = task_ids
        workflow_run.save(update_fields=['created_tasks'])

        workflow_run.status = 'completed'
        workflow_run.save(update_fields=['status'])
