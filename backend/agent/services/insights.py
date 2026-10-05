"""Spreadsheet insights (summary / recommendations / anomalies) for a single sheet."""
import json
import logging

from spreadsheet.providers import (
    SpreadsheetAccessError,
    AiAnalysisDisabled,
)
from ..models import AgentWorkflowRun
from .analysis import _ANALYSIS_VALIDATION_MAX_ATTEMPTS, _truncation_notice

logger = logging.getLogger(__name__)


_SPREADSHEET_INSIGHTS_SYSTEM_PROMPT = """\
You are a spreadsheet data analyst. Analyze the provided sheet data and return \
insights for the user.

You MUST return ONLY valid JSON (no markdown, no explanation, no code fences) \
with this exact structure:

{
  "summary": "Markdown summary of the data: column overview, key statistics, trends, and patterns",
  "recommendations": ["actionable recommendation strings"],
  "anomalies": [
    {
      "title": "short label for the anomaly",
      "severity": "one of: critical, warning, info",
      "description": "why this value or pattern is anomalous",
      "locations": [
        { "row": 0, "col": 2, "a1": "C1" }
      ]
    }
  ],
  "recommended_tasks": [
    {
      "type": "one of: optimization, alert, asset, execution, budget, report, scaling, communication, retrospective, experiment, platform_policy_update",
      "summary": "Short task title (max 255 chars)",
      "description": "2-4 sentence actionable description",
      "priority": "one of: HIGH, MEDIUM, LOW"
    }
  ]
}

Rules:
- Use 0-based row and col indices matching the data rows provided (first data row is row 0)
- Include A1 notation for each location when possible
- Only reference cells present in the provided data sample
- Look for outliers, missing values, inconsistent formats, impossible ratios, and unexpected patterns
- Suggest 0-5 recommended_tasks based on findings
- If no anomalies found, return an empty anomalies array
- Return ONLY the JSON object, nothing else\
"""


# Max data rows handed to the LLM for in-sheet insights. Anomaly locations are
# validated against this window (see _spreadsheet_insights_sample_bounds).
_SPREADSHEET_INSIGHTS_SAMPLE_ROWS = 50


def _preprocess_spreadsheet_insights(spreadsheet_data):
    """Return (column_summary, cleaned_data) for in-sheet insights analysis."""
    all_rows = []
    columns_info = []
    sheet_meta = []
    for sheet in spreadsheet_data.get('sheets', []):
        columns = sheet.get('columns', [])
        columns_info.extend(columns)
        sheet_meta.append(
            f"Sheet id={sheet.get('id')} name={sheet.get('name', 'Unknown')}"
        )
        for row_idx, row in enumerate(sheet.get('rows', [])):
            if isinstance(row, dict):
                row_with_idx = dict(row)
                row_with_idx['_row_index'] = row_idx
                all_rows.append(row_with_idx)

    unique_columns = list(dict.fromkeys(columns_info))
    limited = all_rows[:_SPREADSHEET_INSIGHTS_SAMPLE_ROWS]
    column_summary = (
        f"Spreadsheet: {spreadsheet_data.get('name', 'Unknown')}, "
        f"Sheets: {', '.join(sheet_meta) or 'none'}, "
        f"Total rows: {len(all_rows)}, Showing: {len(limited)}, "
        f"Columns: {unique_columns}, "
        f"Valid 0-based location indices: rows 0..{max(len(limited) - 1, 0)}, "
        f"cols 0..{max(len(unique_columns) - 1, 0)}"
    )
    return column_summary, json.dumps(limited, default=str)


def _spreadsheet_insights_sample_bounds(spreadsheet_data):
    """Row/column extent of the data sample handed to the LLM for insights.

    The system prompt tells the model to reference only cells inside the sample
    it was shown, using 0-based indices. A location outside that window points
    the sheet-highlight UI at a cell that does not exist, so callers use these
    bounds to reject such locations. Returns ``(row_count, col_count)``; a zero
    means the dimension is unknown and must not be enforced.
    """
    unique_columns: list = []
    total_rows = 0
    for sheet in spreadsheet_data.get('sheets', []):
        for name in sheet.get('columns', []) or []:
            if name not in unique_columns:
                unique_columns.append(name)
        total_rows += sum(
            1 for row in (sheet.get('rows', []) or []) if isinstance(row, dict)
        )
    return (
        min(total_rows, _SPREADSHEET_INSIGHTS_SAMPLE_ROWS),
        len(unique_columns),
    )


def _normalize_spreadsheet_insights_result(
    raw, sheet_id=None, row_count=None, col_count=None
):
    """Validate and normalize the model's spreadsheet insights JSON.

    ``row_count`` / ``col_count`` are the dimensions of the data sample shown to
    the LLM (see :func:`_spreadsheet_insights_sample_bounds`). When set, anomaly
    locations that fall outside that window are rejected so the sheet-highlight
    UI is never handed a non-existent cell.
    """
    if not isinstance(raw, dict):
        raise ValueError('Insights response must be a JSON object')

    summary_raw = raw.get('summary')
    if summary_raw is not None and not isinstance(summary_raw, str):
        raise ValueError('LLM insights summary must be a string.')
    summary = (summary_raw or '').strip()
    if not summary:
        raise ValueError('LLM insights summary is empty or missing.')
    recommendations = raw.get('recommendations') or []
    if not isinstance(recommendations, list):
        recommendations = []
    recommendations = [str(r).strip() for r in recommendations if str(r).strip()]

    anomalies_in = raw.get('anomalies') or []
    if not isinstance(anomalies_in, list):
        anomalies_in = []

    normalized_anomalies = []
    for i, anomaly in enumerate(anomalies_in):
        if not isinstance(anomaly, dict):
            continue
        severity = anomaly.get('severity', 'info')
        if severity not in ('critical', 'warning', 'info'):
            severity = 'info'

        anomaly_ref = anomaly.get('id') or anomaly.get('title') or f'#{i}'
        locations_in = anomaly.get('locations') or []
        norm_locations = []
        if isinstance(locations_in, list):
            for loc in locations_in:
                if not isinstance(loc, dict):
                    continue
                if 'row' not in loc or 'col' not in loc:
                    continue
                try:
                    row = int(loc['row'])
                    col = int(loc['col'])
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Anomaly {anomaly_ref} has a location with non-integer "
                        f"row/col indices: {loc!r}"
                    )
                if row < 0 or col < 0:
                    raise ValueError(
                        f"Anomaly {anomaly_ref} has a location with a negative "
                        f"row/col index: row={row}, col={col}"
                    )
                if row_count and row >= row_count:
                    raise ValueError(
                        f"Anomaly {anomaly_ref} references row {row}, which is "
                        f"outside the analysed data (valid rows are 0..{row_count - 1})."
                    )
                if col_count and col >= col_count:
                    raise ValueError(
                        f"Anomaly {anomaly_ref} references column {col}, which is "
                        f"outside the analysed data (valid columns are 0..{col_count - 1})."
                    )
                norm_loc = {'row': row, 'col': col}
                if loc.get('a1'):
                    norm_loc['a1'] = str(loc['a1'])
                if sheet_id is not None:
                    norm_loc['sheet_id'] = sheet_id
                norm_locations.append(norm_loc)

        title = str(anomaly.get('title') or '').strip()
        description = str(anomaly.get('description') or '').strip()
        if not title and description:
            title = description[:80]

        normalized_anomalies.append({
            'id': f'anom_{i}',
            'title': title or 'Anomaly',
            'severity': severity,
            'description': description or title,
            'locations': norm_locations,
            'metric': title or 'Data',
            'movement': 'UNEXPECTED_SPIKE',
            'current_value': '',
            'previous_value': '',
            'change_percent': 0,
        })

    recommended_tasks = raw.get('recommended_tasks') or []
    if not isinstance(recommended_tasks, list):
        recommended_tasks = []

    return {
        'summary': summary,
        'recommendations': recommendations,
        'anomalies': normalized_anomalies,
        'recommended_tasks': recommended_tasks,
        'anomalies_confirmed': True,
        # Marks the lightweight in-sheet flow so the task-creation gate knows
        # anomalies were auto-confirmed here rather than requiring a review pass.
        '_source': 'spreadsheet_insights',
    }


def _call_ollama_spreadsheet_insights(
    spreadsheet_data,
    user_id=None,
    sheet_id=None,
    validation_feedback=None,
    agent_session=None,
):
    """Call Ollama for in-sheet summarization and anomaly detection.

    When *agent_session* is set the call is routed through the unified
    ``llm_client.call_llm`` so it is quota-checked and written to ``LLMCallLog``
    (mirrors ``_call_ollama_analysis``); otherwise it falls back to a direct
    ``call_ollama_json``.
    """
    from core.services.ollama_client import call_ollama_json
    from ..llm_client import call_llm as _call_llm_unified

    column_summary, cleaned_data = _preprocess_spreadsheet_insights(spreadsheet_data)
    user_prompt = (
        f"Data summary: {column_summary}\n\n"
        f"Analyze the following spreadsheet data:\n\n{cleaned_data}"
    )
    if validation_feedback:
        user_prompt += (
            f"\n\nYour previous JSON response failed validation: {validation_feedback}\n"
            "Fix every issue and return ONLY the corrected JSON object."
        )

    logger.info(
        "Calling Ollama for spreadsheet insights user_id=%s sheet_id=%s attempt=%s",
        user_id,
        sheet_id,
        'retry' if validation_feedback else 'initial',
    )
    if agent_session is None:
        return call_ollama_json(
            system_prompt=_SPREADSHEET_INSIGHTS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.3,
        )
    result = _call_llm_unified(
        agent_session=agent_session,
        system_prompt=_SPREADSHEET_INSIGHTS_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.3,
        max_output_tokens=4096,
        response_mime_type='application/json',
        call_purpose='data_analysis',
    )
    return json.loads(result['text'])


def _run_spreadsheet_insights(
    spreadsheet_data, user_id=None, sheet_id=None, agent_session=None
):
    """Run in-sheet insights using Ollama.

    Raises RuntimeError if no provider is configured or the call fails.
    Raises GenerationValidationError if recommended_tasks fail validation after retries.
    Propagates QuotaError from the billed path untouched.
    """
    from core.services.ollama_client import _get_base_url as _ollama_base_url
    from ..generation_registry import GenerationValidationError, validate_recommended_tasks
    from stripe_meta.exceptions import QuotaError

    if not _ollama_base_url():
        raise RuntimeError("No analysis provider available.")

    sample_rows, sample_cols = _spreadsheet_insights_sample_bounds(spreadsheet_data)
    validation_feedback = None
    for attempt in range(1, _ANALYSIS_VALIDATION_MAX_ATTEMPTS + 1):
        try:
            raw = _call_ollama_spreadsheet_insights(
                spreadsheet_data,
                user_id=user_id,
                sheet_id=sheet_id,
                validation_feedback=validation_feedback,
                agent_session=agent_session,
            )
            result = _normalize_spreadsheet_insights_result(
                raw,
                sheet_id=sheet_id,
                row_count=sample_rows,
                col_count=sample_cols,
            )
            result['recommended_tasks'] = validate_recommended_tasks(
                result.get('recommended_tasks', [])
            )
            return result
        except QuotaError:
            raise
        except (GenerationValidationError, ValueError) as exc:
            if attempt >= _ANALYSIS_VALIDATION_MAX_ATTEMPTS:
                raise
            validation_feedback = str(exc)
            logger.warning(
                "LLM spreadsheet insights validation failed (attempt %s/%s): %s; retrying",
                attempt,
                _ANALYSIS_VALIDATION_MAX_ATTEMPTS,
                exc,
            )
        except Exception as e:
            logger.error("Ollama spreadsheet insights failed: %s", e)
            raise RuntimeError("Spreadsheet insights analysis failed.") from e

    raise RuntimeError("Spreadsheet insights analysis failed.")


class InsightsMixin:
    """Spreadsheet insights entry point for AgentOrchestrator."""

    def analyze_spreadsheet_insights(self, spreadsheet_id, sheet_id=None):
        """Analyze the active sheet in-place: summary + anomalies (lightweight path)."""
        from stripe_meta.exceptions import QuotaError

        yield {"type": "text", "content": "Analyzing spreadsheet data..."}

        if not spreadsheet_id:
            yield {"type": "error", "content": "spreadsheet_id is required."}
            return

        try:
            spreadsheet_data = self.spreadsheet_provider.get_analysis_payload(
                spreadsheet_id, sheet_id=sheet_id
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

        if not spreadsheet_data["sheets"]:
            yield {"type": "error", "content": "Sheet not found."}
            return

        self._audit_spreadsheet_analysis(
            'agent.spreadsheet.insights_analyzed', spreadsheet_data, sheet_id=sheet_id
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
            insights = _run_spreadsheet_insights(
                spreadsheet_data,
                user_id=self.user.id,
                sheet_id=sheet_id,
                agent_session=self.session,
            )
        except QuotaError:
            raise
        except Exception as e:
            from ..generation_registry import GenerationValidationError

            if isinstance(e, GenerationValidationError):
                message = f"Task suggestions failed validation: {e}"
            elif isinstance(e, RuntimeError):
                message = str(e)
            else:
                message = "Spreadsheet insights analysis failed."
            workflow_run.status = 'failed'
            workflow_run.error_message = message
            workflow_run.save()
            yield {"type": "error", "content": message}
            return

        workflow_run.analysis_result = insights
        workflow_run.status = 'awaiting_confirmation'
        workflow_run.save()

        summary_content = insights.get('summary') or 'Analysis complete.'
        recommendations = insights.get('recommendations') or []
        if recommendations:
            summary_content += '\n\n### Recommendations\n' + '\n'.join(
                f'- {rec}' for rec in recommendations
            )

        yield {
            "type": "spreadsheet_summary",
            "content": summary_content,
        }

        anomalies = insights.get('anomalies', [])
        count = len(anomalies)
        anomaly_label = 'anomaly' if count == 1 else 'anomalies'
        yield {
            "type": "spreadsheet_anomalies",
            "content": (
                f"Found {count} {anomaly_label} in the data."
                if count
                else "No anomalies detected in the data."
            ),
            "data": {
                "anomalies": anomalies,
                "anomalies_confirmed": True,
                "recommended_tasks": insights.get('recommended_tasks', []),
                "spreadsheet_id": spreadsheet_id,
                "sheet_id": sheet_id,
            },
        }
