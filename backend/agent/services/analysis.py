"""Anomaly / recommended-task analysis of spreadsheets and uploaded files."""
import json
import logging
import os

from django.conf import settings
from core.services.ollama_client import parse_json_text
from spreadsheet.providers import (
    SpreadsheetAccessError,
    AiAnalysisDisabled,
)
from ..models import AgentWorkflowRun, ImportedCSVFile
from .. import data_service
from core.services import file_parser
from ..agent_utils import drain_generator, json_input
from ..llm_client import call_llm as _call_llm_unified
from .analysis_prompts import (
    _ANALYSIS_SYSTEM_PROMPT,
    _CONTEXT_BLOCK_TEMPLATE,
    _CRITERIA_WITH_BLOCK,
    _NO_CRITERIA_BLOCK,
)

logger = logging.getLogger(__name__)


def _get_llm_client():
    """Return an Anthropic client if API key is set, else None."""
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        logger.warning("anthropic package not installed, using mock LLM")
        return None


def _truncation_notice(spreadsheet_data):
    """Human-readable notice when the provider windowed the sheet, else None."""
    if not spreadsheet_data.get("truncated"):
        return None
    windows = [s.get("window", {}) for s in spreadsheet_data.get("sheets", [])]
    max_rows = next((w.get("max_rows") for w in windows if w.get("max_rows")), None)
    if any(w.get("row_limited") for w in windows):
        base = (
            f"Analyzed the first {max_rows} rows"
            if max_rows
            else "Analyzed a limited window of rows"
        )
        return f"{base} — narrow the sheet or a range for a full pass."
    return "Analyzed a subset of the columns — some wide columns were left out."


def _build_criteria_text(success_criteria) -> tuple[str, list]:
    """Parse success_criteria and return (criteria_text, key_columns)."""
    if not success_criteria:
        return '', []
    try:
        if isinstance(success_criteria, str):
            criteria = json.loads(success_criteria)
        else:
            criteria = success_criteria
        # the code below this test expects the criteria to be a dict object
        if not isinstance(criteria, dict):
            return '', []

        key_cols = criteria.get('key_columns', [])
        lines = [f"Dataset type: {criteria.get('schema_type', 'unknown')}"]
        for c in criteria.get('criteria', []):
            if c.get('anomaly_rule'):
                lines.append(f"- {c['column']}: {c['anomaly_rule']}")
        if criteria.get('analysis_goals'):
            lines.append('Analysis goals:')
            for g in criteria['analysis_goals']:
                lines.append(f'  * {g}')
        return '\n'.join(lines), key_cols
    except (json.JSONDecodeError, TypeError):
        return '', []


def _resolve_analysis_columns(key_cols, sheet_columns, column_mapping=None):
    """Map success_criteria key_columns onto normalized spreadsheet column keys.

    After normalize_data, row keys are canonical names (e.g. amount_spent) while
    Ollama criteria often reference display headers (e.g. Amount Spent (USD)).
    """
    if not sheet_columns:
        return list(key_cols or [])

    actual = set(sheet_columns)
    if not key_cols:
        return list(sheet_columns)

    direct = [k for k in key_cols if k in actual]
    if direct:
        return direct

    if not column_mapping:
        logger.warning(
            "success_criteria key_columns do not match sheet columns; using all columns",
        )
        return list(sheet_columns)

    resolved = []
    seen = set()
    for kc in key_cols:
        candidates = []
        if kc in actual:
            candidates = [kc]
        elif kc in column_mapping:
            canon = column_mapping[kc]
            if canon in actual:
                candidates = [canon]
        else:
            kc_lower = kc.lower().strip()
            for orig, canon in column_mapping.items():
                if orig.lower().strip() == kc_lower and canon in actual:
                    candidates = [canon]
                    break
            if not candidates:
                kc_norm = kc.lower().replace(' ', '_').replace('(', '').replace(')', '')
                for col in actual:
                    if col.lower() == kc_norm:
                        candidates = [col]
                        break

        for col in candidates:
            if col not in seen:
                resolved.append(col)
                seen.add(col)

    if resolved:
        return resolved

    logger.warning(
        "Could not resolve success_criteria key_columns; falling back to all sheet columns",
    )
    return list(sheet_columns)


def _preprocess_spreadsheet(spreadsheet_data, success_criteria=None, column_mapping=None):
    """Mirror the Dify code-node preprocessing: return (column_summary, cleaned_data, criteria_text)."""
    criteria_text, key_cols = _build_criteria_text(success_criteria)

    all_rows = []
    columns_info = []
    for sheet in spreadsheet_data.get('sheets', []):
        columns = sheet.get('columns', [])
        columns_info.extend(columns)
        key_cols_to_use = _resolve_analysis_columns(key_cols, columns, column_mapping)
        for row in sheet.get('rows', []):
            clean_row = {k: v for k, v in row.items() if k in key_cols_to_use}
            if clean_row:
                all_rows.append(clean_row)

        if not all_rows and key_cols:
            logger.warning(
                "No rows matched key_columns after resolution; retrying with all sheet columns",
            )
            for row in sheet.get('rows', []):
                clean_row = {k: v for k, v in row.items() if k in columns}
                if clean_row:
                    all_rows.append(clean_row)

    limited = all_rows[:50]
    column_summary = (
        f"Spreadsheet: {spreadsheet_data.get('name', 'Unknown')}, "
        f"Total rows: {len(all_rows)}, Showing: {len(limited)}, "
        f"Columns: {list(set(columns_info))}"
    )
    return column_summary, json.dumps(limited, default=str), criteria_text


_ANALYSIS_VALIDATION_MAX_ATTEMPTS = 3


def _call_ollama_analysis(
    spreadsheet_data,
    user_id=None,
    success_criteria=None,
    column_mapping=None,
    generation_outputs=None,
    user_context=None,
    validation_feedback=None,
    agent_session=None,
):
    """Call Ollama to analyze spreadsheet data."""
    from core.services.ollama_client import call_ollama_json
    from ..generation_registry import (
        build_analysis_prompt,
        normalize_generation_outputs,
    )

    requested = frozenset(normalize_generation_outputs(generation_outputs))
    column_summary, cleaned_data, criteria_text = _preprocess_spreadsheet(
        spreadsheet_data, success_criteria, column_mapping=column_mapping,
    )

    criteria_block = (
        _CRITERIA_WITH_BLOCK.replace("{criteria_text}", criteria_text)
        if criteria_text
        else _NO_CRITERIA_BLOCK
    )
    system_prompt = build_analysis_prompt(requested, criteria_block)
    if user_context:
        system_prompt += _CONTEXT_BLOCK_TEMPLATE.format(user_context=user_context)
    user_prompt = (
        f"Data summary: {column_summary}\n\n"
        f"Analyze the following data:\n\n{cleaned_data}"
    )
    if validation_feedback:
        user_prompt += (
            f"\n\nYour previous JSON response failed validation: {validation_feedback}\n"
            "Fix every issue and return ONLY the corrected JSON object."
        )

    logger.info(
        "Calling Ollama for spreadsheet analysis user_id=%s outputs=%s attempt=%s",
        user_id,
        sorted(requested),
        'retry' if validation_feedback else 'initial',
    )
    if agent_session is None:
        return call_ollama_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,
        )

    result = _call_llm_unified(
        agent_session=agent_session,
        provider='ollama',
        model=settings.AGENT_LLM_MODEL,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        temperature=0.3,
        max_output_tokens=4096,
        response_mime_type='application/json',
        call_purpose='data_analysis',
    )
    return parse_json_text(result['text'])


def _assign_anomaly_ids(analysis):
    """Assign a stable id to every anomaly so the frontend can reference them
    across the review/confirmation round-trip.

    - Idempotent: anomalies that already carry an ``id`` are left untouched, so
      re-analysis or session restore never reshuffles ids.
    - Zero-anomaly datasets are marked ``anomalies_confirmed=True`` so they do
      not block the workflow waiting for a confirmation that has no card. This
      preserves the existing downstream behaviour (tasks still flow from
      ``recommended_tasks``); task creation is only skipped when anomalies were
      detected and the user excluded all of them.
    """
    if not isinstance(analysis, dict):
        return analysis

    anomalies = analysis.get('anomalies') or []
    for i, anomaly in enumerate(anomalies):
        if isinstance(anomaly, dict) and not anomaly.get('id'):
            anomaly['id'] = f"anom_{i}"

    if not anomalies:
        analysis['anomalies_confirmed'] = True

    return analysis


def _coerce_llm_analysis_for_requested(data, requested):
    """Map Claude/legacy full analysis JSON to the requested analysis key set."""
    from ..generation_registry import analysis_keys_for_request, validate_analysis_response

    expected = analysis_keys_for_request(requested)
    if not expected:
        return {}
    subset = {}
    if 'recommended_tasks' in expected:
        subset['recommended_tasks'] = data.get('recommended_tasks', [])
    if 'recommended_decision_tree' in expected:
        subset['recommended_decision_tree'] = data.get(
            'recommended_decision_tree',
            {'nodes': []},
        )
    return validate_analysis_response(subset, requested)


def _run_analysis(
    spreadsheet_data,
    user_id=None,
    success_criteria=None,
    column_mapping=None,
    generation_outputs=None,
    user_context=None,
    agent_session=None,
):
    """Synchronous form of _iter_analysis(); retry progress events are dropped."""
    return drain_generator(_iter_analysis(
        spreadsheet_data,
        user_id=user_id,
        success_criteria=success_criteria,
        column_mapping=column_mapping,
        generation_outputs=generation_outputs,
        user_context=user_context,
        agent_session=agent_session,
    ))


def _iter_analysis(
    spreadsheet_data,
    user_id=None,
    success_criteria=None,
    column_mapping=None,
    generation_outputs=None,
    user_context=None,
    agent_session=None,
):
    """Run analysis using Ollama, with Claude as fallback.

    Generator: yields a ``text`` SSE event before each validation retry so the
    user sees progress live, and returns the analysis dict. Consume it with
    ``analysis = yield from _iter_analysis(...)``.

    Raises RuntimeError if no provider is configured or all providers fail.
    Raises GenerationValidationError if the model JSON does not match the contract.
    """
    from ..generation_registry import (
        GenerationValidationError,
        normalize_generation_outputs,
        validate_analysis_response,
    )
    from stripe_meta.exceptions import QuotaError

    requested = frozenset(normalize_generation_outputs(generation_outputs))

    # 1. Try Ollama (primary)
    from core.services.ollama_client import is_llm_configured
    if is_llm_configured():
        validation_feedback = None
        for attempt in range(1, _ANALYSIS_VALIDATION_MAX_ATTEMPTS + 1):
            try:
                raw = _call_ollama_analysis(
                    spreadsheet_data,
                    user_id,
                    success_criteria=success_criteria,
                    column_mapping=column_mapping,
                    user_context=user_context,
                    generation_outputs=list(requested),
                    validation_feedback=validation_feedback,
                    agent_session=agent_session,
                )
                return _assign_anomaly_ids(validate_analysis_response(raw, requested))
            except GenerationValidationError as exc:
                if attempt >= _ANALYSIS_VALIDATION_MAX_ATTEMPTS:
                    raise
                validation_feedback = str(exc)
                logger.warning(
                    "Ollama analysis validation failed (attempt %s/%s): %s; retrying",
                    attempt,
                    _ANALYSIS_VALIDATION_MAX_ATTEMPTS,
                    exc,
                )
                yield {
                    "type": "text",
                    "content": (
                        "Analysis output failed validation; retrying "
                        f"({attempt + 1}/{_ANALYSIS_VALIDATION_MAX_ATTEMPTS})..."
                    ),
                }
            except QuotaError:
                raise
            except Exception as e:
                logger.error(
                    "Ollama analysis failed, falling back to Anthropic: %s", e,
                )
                break

    # 2. Try Claude API (fallback)
    client = _get_llm_client()
    if client:
        try:
            result = _call_llm_unified(
                provider="anthropic",
                model=settings.AGENT_ANTHROPIC_FALLBACK_MODEL,
                user_prompt=json_input(spreadsheet_data),
                system_prompt=_ANALYSIS_SYSTEM_PROMPT,
                agent_session=agent_session,
            )
            raw = parse_json_text(result['text'])
            return _assign_anomaly_ids(_coerce_llm_analysis_for_requested(raw, requested))
        except QuotaError:
            raise
        except GenerationValidationError:
            raise
        except Exception as e:
            logger.error("Anthropic fallback analysis failed: %s", e)

    # 3. No LLM available
    raise RuntimeError(
        "No analysis provider available."
    )


class AnalysisMixin:
    """Spreadsheet / file / CSV analysis entry points for AgentOrchestrator."""

    def _audit_spreadsheet_analysis(self, event_type, spreadsheet_data, sheet_id=None):
        """Record that this user sent spreadsheet data to an LLM. Counts only."""
        from core.services.audit_events import safe_emit_audit_event

        sheets = spreadsheet_data.get('sheets', [])
        safe_emit_audit_event(
            event_type=event_type,
            actor=self.user,
            organization=getattr(self.project, 'organization', None),
            project=self.project,
            target_type='spreadsheet',
            target_id=spreadsheet_data.get('id'),
            context={
                'sheet_id': sheet_id,
                'sheets_sent': len(sheets),
                'rows_sent': sum(len(s.get('rows', [])) for s in sheets),
                'cols_sent': sum(len(s.get('columns', [])) for s in sheets),
                'cells_sent': sum(
                    s.get('window', {}).get('cells_returned', 0) for s in sheets
                ),
                'truncated': bool(spreadsheet_data.get('truncated')),
                'provider': 'ollama',
                'model': settings.AGENT_LLM_MODEL,
            },
        )

    def analyze_file(self, file_id):
        """Analyse any uploaded file (CSV/Excel) by its DB id."""
        yield {"type": "text", "content": "Analyzing file data..."}

        try:
            record = ImportedCSVFile.objects.get(
                id=file_id, project=self.project, is_deleted=False,
            )
        except ImportedCSVFile.DoesNotExist:
            yield {"type": "error", "content": f"File {file_id} not found."}
            return

        csv_dir = data_service._get_csv_dir()
        filepath = os.path.join(csv_dir, os.path.basename(record.filename))

        if not os.path.isfile(filepath):
            yield {"type": "error", "content": "File not found on disk."}
            return

        try:
            spreadsheet_data = file_parser.parse_file_to_json(filepath, record.filename)
        except Exception as e:
            yield {"type": "error", "content": f"Failed to parse file: {e}"}
            return

        workflow_run = AgentWorkflowRun.objects.create(
            session=self.session,
            status='analyzing',
        )

        try:
            analysis = yield from _iter_analysis(
                spreadsheet_data,
                user_id=self.user.id,
                agent_session=self.session,
            )
        except RuntimeError as e:
            workflow_run.status = 'failed'
            workflow_run.error_message = str(e)
            workflow_run.save()
            yield {"type": "error", "content": str(e)}
            return

        workflow_run.analysis_result = analysis
        workflow_run.status = 'awaiting_confirmation'
        workflow_run.save()

        anomalies = analysis.get("anomalies", [])
        summary_parts = [f"Found {len(anomalies)} anomalies:"]
        for a in anomalies:
            summary_parts.append(f"- {a.get('description', str(a))}")

        yield {
            "type": "analysis",
            "content": "\n".join(summary_parts),
            "data": analysis,
        }

    def analyze_spreadsheet(self, spreadsheet_id):
        """Read spreadsheet data via the provider facade, send to LLM for analysis."""
        yield {"type": "text", "content": "Analyzing spreadsheet data..."}

        try:
            spreadsheet_data = self.spreadsheet_provider.get_analysis_payload(
                spreadsheet_id
            )
        except SpreadsheetAccessError:
            yield {"type": "error", "content": "Spreadsheet not found."}
            return
        except AiAnalysisDisabled as exc:
            yield {
                "type": "error",
                "content": exc.message,
                "data": {"code": exc.code, "spreadsheet_id": exc.spreadsheet_id},
            }
            return

        self._audit_spreadsheet_analysis(
            'agent.spreadsheet.analyzed', spreadsheet_data
        )

        workflow_run = AgentWorkflowRun.objects.create(
            session=self.session,
            spreadsheet_id=spreadsheet_data["id"],
            status='analyzing',
        )

        _notice = _truncation_notice(spreadsheet_data)
        if _notice:
            yield {"type": "text", "content": _notice}

        try:
            analysis = yield from _iter_analysis(
                spreadsheet_data,
                user_id=self.user.id,
                agent_session=self.session,
            )
        except RuntimeError as e:
            workflow_run.status = 'failed'
            workflow_run.error_message = str(e)
            workflow_run.save()
            yield {"type": "error", "content": str(e)}
            return

        workflow_run.analysis_result = analysis
        workflow_run.status = 'awaiting_confirmation'
        workflow_run.save()

        anomalies = analysis.get("anomalies", [])
        summary_parts = [f"Found {len(anomalies)} anomalies:"]
        for a in anomalies:
            summary_parts.append(f"- {a['description']}")

        yield {
            "type": "analysis",
            "content": "\n".join(summary_parts),
            "data": analysis,
        }

    def analyze_csv(self, csv_filename):
        """Read an uploaded CSV file from disk, send to LLM for analysis."""
        yield {"type": "text", "content": "Analyzing CSV data..."}

        safe_name = os.path.basename(csv_filename)

        # Verify file belongs to this project
        record = ImportedCSVFile.objects.filter(
            filename=safe_name, project=self.project, is_deleted=False
        ).first()
        if not record:
            yield {"type": "error", "content": f"CSV file not found: {safe_name}"}
            return

        csv_dir = data_service._get_csv_dir()
        filepath = os.path.join(csv_dir, safe_name)

        if not os.path.isfile(filepath):
            yield {"type": "error", "content": f"CSV file not found on disk: {safe_name}"}
            return

        columns, rows = data_service._read_csv_file(filepath)
        if not rows:
            yield {"type": "error", "content": "CSV file is empty or could not be parsed."}
            return

        workflow_run = AgentWorkflowRun.objects.create(
            session=self.session,
            status='analyzing',
        )

        # Build spreadsheet-like data structure for the analysis pipeline
        spreadsheet_data = {
            "name": safe_name,
            "sheets": [{
                "name": "Sheet1",
                "columns": columns,
                "rows": rows[:100],  # limit rows sent to LLM
            }],
        }

        try:
            analysis = yield from _iter_analysis(
                spreadsheet_data,
                user_id=self.user.id,
                agent_session=self.session,
            )
        except RuntimeError as e:
            workflow_run.status = 'failed'
            workflow_run.error_message = str(e)
            workflow_run.save()
            yield {"type": "error", "content": str(e)}
            return

        workflow_run.analysis_result = analysis
        workflow_run.status = 'awaiting_confirmation'
        workflow_run.save()

        anomalies = analysis.get("anomalies", [])
        summary_parts = [f"Found {len(anomalies)} anomalies:"]
        for a in anomalies:
            summary_parts.append(f"- {a.get('description', str(a))}")

        yield {
            "type": "analysis",
            "content": "\n".join(summary_parts),
            "data": analysis,
        }
