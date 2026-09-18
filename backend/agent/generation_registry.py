"""Registry for upload-analyze generation outputs (prompts, validation, catalog)."""
from __future__ import annotations

from typing import Any

GENERATION_OUTPUT_KEYS = frozenset({
    'recommended_tasks',
    'recommended_decision_tree',
    'miro_board',
})

# Keys produced by the spreadsheet analysis Gemini call (not Miro/calendar dedicated calls).
ANALYSIS_JSON_KEYS = frozenset({'recommended_tasks', 'recommended_decision_tree'})

DEFAULT_GENERATION_OUTPUTS = [
    'recommended_tasks',
    'recommended_decision_tree',
    'miro_board',
]

CATALOG = [
    {
        'key': 'recommended_tasks',
        'label': 'Tasks',
        'description': 'Recommended tasks from spreadsheet analysis.',
    },
    {
        'key': 'recommended_decision_tree',
        'label': 'Decision tree',
        'description': 'Layered decision tree from spreadsheet analysis.',
    },
    {
        'key': 'miro_board',
        'label': 'Miro board',
        'description': 'Generate a Miro board snapshot from analysis context.',
    },
]

_TASK_TYPES = frozenset({
    'optimization', 'alert', 'asset', 'execution', 'budget', 'report',
    'scaling', 'communication', 'retrospective', 'experiment', 'platform_policy_update',
})
_TASK_PRIORITIES = frozenset({'HIGH', 'MEDIUM', 'LOW'})
_TASK_SUMMARY_MAX_LENGTH = 255

# Common Gemini aliases → canonical task types.
_TASK_TYPE_ALIASES: dict[str, str] = {
    'planning': 'execution',
    'plan': 'execution',
    'setup': 'execution',
    'launch': 'execution',
    'campaign': 'execution',
    'strategy': 'execution',
    'media': 'execution',
    'marketing': 'execution',
    'creative': 'asset',
    'content': 'asset',
    'design': 'asset',
    'reporting': 'report',
    'analysis': 'report',
    'analytics': 'report',
    'research': 'experiment',
    'test': 'experiment',
    'testing': 'experiment',
    'optimize': 'optimization',
    'optimisation': 'optimization',
    'scale': 'scaling',
    'monitoring': 'alert',
    'comms': 'communication',
    'policy': 'platform_policy_update',
}
_RISK_LEVELS = frozenset({'LOW', 'MEDIUM', 'HIGH'})
_DECISION_NODE_KEYS = frozenset({
    'ref', 'layer', 'title', 'parent_refs',
    'context_summary', 'reasoning', 'risk_level', 'confidence', 'topic',
})

_RECOMMENDED_TASKS_SCHEMA = """\
  "recommended_tasks": [
    {
      "type": "one of: optimization, alert, asset, execution, budget, report, scaling, communication, retrospective, experiment, platform_policy_update",
      "summary": "Short task title (max 255 chars)",
      "description": "2-4 sentence actionable description: why this task was created, what specifically needs to be done, and what success looks like",
      "priority": "one of: HIGH, MEDIUM, LOW"
    }
  ]"""

_RECOMMENDED_DECISION_TREE_SCHEMA = """\
  "recommended_decision_tree": {
    "nodes": [
      {
        "ref": "root_goal",
        "layer": 0,
        "title": "Decision title (max 255 chars)",
        "parent_refs": [],
        "context_summary": "optional string — 1-3 sentence context from the data",
        "reasoning": "optional string — reasoning for this decision",
        "risk_level": "LOW | MEDIUM | HIGH (optional)",
        "confidence": 3,
        "topic": "optional string slug e.g. meta_retargeting"
      },
      {
        "ref": "child_action",
        "layer": 1,
        "title": "Child decision title",
        "parent_refs": ["root_goal"],
        "context_summary": "optional string",
        "reasoning": "optional string",
        "risk_level": "MEDIUM",
        "confidence": 3,
        "topic": "optional string slug"
      }
    ]
  }"""

_ANALYSIS_PROMPT_HEADER = """\
You are a data analysis expert. Analyze the provided spreadsheet data.

{criteria_block}

You MUST return ONLY valid JSON (no markdown, no explanation, no code fences) with this exact structure:

{{
{schema_body}
}}

Rules:
{rules}
- Return ONLY the JSON object, nothing else"""


class GenerationValidationError(ValueError):
    """Raised when Gemini JSON does not match the requested generation contract."""


def get_catalog() -> list[dict[str, str]]:
    return [dict(entry) for entry in CATALOG]


def normalize_generation_outputs(raw: Any) -> list[str]:
    """Parse and validate generation_outputs; default to all keys when omitted."""
    if raw is None or raw == '':
        return list(DEFAULT_GENERATION_OUTPUTS)
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GenerationValidationError(
                'generation_outputs must be a JSON array of strings.'
            ) from exc
    if not isinstance(raw, list):
        raise GenerationValidationError('generation_outputs must be a JSON array.')
    keys = []
    for item in raw:
        if not isinstance(item, str):
            raise GenerationValidationError('Each generation_outputs entry must be a string.')
        if item not in GENERATION_OUTPUT_KEYS:
            raise GenerationValidationError(
                f'Unknown generation output: {item}. '
                f'Allowed: {", ".join(sorted(GENERATION_OUTPUT_KEYS))}.'
            )
        if item not in keys:
            keys.append(item)
    if not keys:
        raise GenerationValidationError('At least one generation output must be selected.')
    return keys


def analysis_keys_for_request(requested: frozenset[str]) -> frozenset[str]:
    return requested & ANALYSIS_JSON_KEYS


def build_analysis_prompt(requested: frozenset[str], criteria_block: str) -> str:
    """Build Gemini system prompt for spreadsheet analysis (subset of keys)."""
    analysis_keys = analysis_keys_for_request(requested)
    if not analysis_keys:
        return (
            "You are a data analysis expert. The user did not request structured analysis "
            "fields from this call.\n\n"
            f"{criteria_block}\n\n"
            "Return ONLY valid JSON: {}\n"
            "No markdown, no explanation, no code fences."
        )
    parts = []
    if 'recommended_tasks' in analysis_keys:
        parts.append(_RECOMMENDED_TASKS_SCHEMA)
    if 'recommended_decision_tree' in analysis_keys:
        parts.append(_RECOMMENDED_DECISION_TREE_SCHEMA)
    schema_body = ',\n'.join(parts)
    rules_parts = []
    if 'recommended_tasks' in analysis_keys:
        rules_parts.append(
            '- Suggest 1-5 tasks based on the data when recommended_tasks is requested'
        )
        rules_parts.append(
            '- If nothing actionable, return an empty recommended_tasks array'
        )
    if 'recommended_decision_tree' in analysis_keys:
        rules_parts.append(
            '- Suggest 2-8 decision nodes forming a layered DAG grounded in the data'
        )
        rules_parts.append(
            '- Every node MUST include these required fields with exact types:'
        )
        rules_parts.append(
            '  • ref: non-empty string, unique across all nodes'
        )
        rules_parts.append(
            '  • layer: non-negative integer (0 for roots; must be > every parent\'s layer)'
        )
        rules_parts.append(
            '  • title: non-empty string (max 255 chars)'
        )
        rules_parts.append(
            '  • parent_refs: JSON array of ref strings — use [] for layer-0 roots; '
            'use ["parent_ref"] for children. Never null, never omitted, never a single string'
        )
        rules_parts.append(
            '- Optional per node: context_summary (string), reasoning (string), '
            'risk_level (LOW|MEDIUM|HIGH), confidence (integer 1-5), topic (string)'
        )
        rules_parts.append(
            '- Parent-child edges flow parent to child; every child layer must be greater '
            'than all of its parents\' layers'
        )
        rules_parts.append(
            '- If nothing actionable, return recommended_decision_tree with nodes: []'
        )
    rules = '\n'.join(f'{line}\n' for line in rules_parts)
    return _ANALYSIS_PROMPT_HEADER.format(
        criteria_block=criteria_block,
        schema_body=schema_body,
        rules=rules,
    )


def _normalize_recommended_task_type(value: Any) -> str:
    """Map LLM task types to allowed _TASK_TYPES slugs."""
    if value is None or value == '':
        return 'execution'
    text = str(value).strip().lower()
    if text in _TASK_TYPES:
        return text
    if text in _TASK_TYPE_ALIASES:
        return _TASK_TYPE_ALIASES[text]
    if 'budget' in text:
        return 'budget'
    if 'asset' in text or 'creative' in text:
        return 'asset'
    if 'report' in text or 'readout' in text:
        return 'report'
    if 'experiment' in text or 'test' in text:
        return 'experiment'
    if 'optim' in text:
        return 'optimization'
    if 'scale' in text:
        return 'scaling'
    if 'retro' in text:
        return 'retrospective'
    return text


def _normalize_recommended_task_priority(value: Any) -> str:
    """Map LLM priorities to agent draft values HIGH|MEDIUM|LOW."""
    if value is None or value == '':
        return 'MEDIUM'
    text = str(value).strip().upper()
    if text == 'HIGHEST':
        return 'HIGH'
    if text == 'LOWEST':
        return 'LOW'
    if text in _TASK_PRIORITIES:
        return text
    return text


def _validate_recommended_tasks(value: Any) -> list:
    if not isinstance(value, list):
        raise GenerationValidationError('recommended_tasks must be a list.')
    normalized: list[dict[str, Any]] = []
    for idx, task in enumerate(value):
        if not isinstance(task, dict):
            raise GenerationValidationError(f'recommended_tasks[{idx}] must be an object.')

        raw_type = task.get('type')
        if raw_type is None or (isinstance(raw_type, str) and not raw_type.strip()):
            raise GenerationValidationError(f'recommended_tasks[{idx}].type is required.')
        task_type = _normalize_recommended_task_type(raw_type)
        if task_type not in _TASK_TYPES:
            raise GenerationValidationError(f'recommended_tasks[{idx}].type is invalid.')

        summary_raw = task.get('summary')
        if not isinstance(summary_raw, str) or not summary_raw.strip():
            raise GenerationValidationError(f'recommended_tasks[{idx}].summary is required.')
        summary = summary_raw.strip()
        if len(summary) > _TASK_SUMMARY_MAX_LENGTH:
            raise GenerationValidationError(
                f'recommended_tasks[{idx}].summary exceeds {_TASK_SUMMARY_MAX_LENGTH} characters.'
            )

        raw_priority = task.get('priority')
        if raw_priority is None or (isinstance(raw_priority, str) and not raw_priority.strip()):
            raise GenerationValidationError(f'recommended_tasks[{idx}].priority is required.')
        priority = _normalize_recommended_task_priority(raw_priority)
        if priority not in _TASK_PRIORITIES:
            raise GenerationValidationError(f'recommended_tasks[{idx}].priority is invalid.')

        normalized_task: dict[str, Any] = {
            'type': task_type,
            'summary': summary,
            'priority': priority,
        }
        if 'description' in task and task['description'] is not None:
            description_raw = task['description']
            if not isinstance(description_raw, str):
                raise GenerationValidationError(
                    f'recommended_tasks[{idx}].description must be a string.'
                )
            description = description_raw.strip()
            if description:
                normalized_task['description'] = description
        if 'index' in task:
            normalized_task['index'] = task['index']
        normalized.append(normalized_task)
    return normalized


def validate_recommended_tasks(value: Any) -> list:
    """Public entry point for recommended_tasks validation + normalization."""
    return _validate_recommended_tasks(value)


def _validate_recommended_decision_tree(value: Any) -> dict:
    if not isinstance(value, dict):
        raise GenerationValidationError('recommended_decision_tree must be an object.')
    nodes = value.get('nodes')
    if nodes is None:
        raise GenerationValidationError('recommended_decision_tree.nodes is required.')
    if not isinstance(nodes, list):
        raise GenerationValidationError('recommended_decision_tree.nodes must be a list.')
    if len(value.keys()) != 1:
        extra = set(value.keys()) - {'nodes'}
        if extra:
            raise GenerationValidationError(
                f'recommended_decision_tree has unexpected keys: {", ".join(sorted(extra))}.'
            )

    refs: dict[str, dict] = {}
    for idx, node in enumerate(nodes):
        prefix = f'recommended_decision_tree.nodes[{idx}]'
        if not isinstance(node, dict):
            raise GenerationValidationError(f'{prefix} must be an object.')
        unknown = set(node.keys()) - _DECISION_NODE_KEYS
        if unknown:
            raise GenerationValidationError(
                f'{prefix} has unexpected keys: {", ".join(sorted(unknown))}.'
            )
        ref = node.get('ref')
        if not isinstance(ref, str) or not ref.strip():
            raise GenerationValidationError(f'{prefix}.ref is required.')
        if ref in refs:
            raise GenerationValidationError(f'{prefix}.ref must be unique (duplicate: {ref}).')
        layer = node.get('layer')
        if not isinstance(layer, int) or isinstance(layer, bool) or layer < 0:
            raise GenerationValidationError(f'{prefix}.layer must be a non-negative integer.')
        title = node.get('title')
        if not isinstance(title, str) or not title.strip():
            raise GenerationValidationError(f'{prefix}.title is required.')
        parent_refs = node.get('parent_refs')
        if not isinstance(parent_refs, list):
            raise GenerationValidationError(f'{prefix}.parent_refs must be a list.')
        for pidx, parent_ref in enumerate(parent_refs):
            if not isinstance(parent_ref, str) or not parent_ref.strip():
                raise GenerationValidationError(
                    f'{prefix}.parent_refs[{pidx}] must be a non-empty string.'
                )
            if parent_ref == ref:
                raise GenerationValidationError(f'{prefix} cannot reference itself as parent.')
        risk_level = node.get('risk_level')
        if risk_level is not None:
            if not isinstance(risk_level, str) or risk_level not in _RISK_LEVELS:
                raise GenerationValidationError(f'{prefix}.risk_level is invalid.')
        confidence = node.get('confidence')
        if confidence is not None:
            if not isinstance(confidence, int) or isinstance(confidence, bool):
                raise GenerationValidationError(f'{prefix}.confidence must be an integer.')
            if confidence < 1 or confidence > 5:
                raise GenerationValidationError(f'{prefix}.confidence must be between 1 and 5.')
        for optional in ('context_summary', 'reasoning', 'topic'):
            if optional in node and node[optional] is not None and not isinstance(node[optional], str):
                raise GenerationValidationError(f'{prefix}.{optional} must be a string.')
        refs[ref] = node

    for ref, node in refs.items():
        prefix = f'recommended_decision_tree.nodes[{ref}]'
        parent_refs = node['parent_refs']
        layer = node['layer']
        if not parent_refs:
            if layer != 0:
                raise GenerationValidationError(
                    f'{prefix}: nodes without parents must be at layer 0.'
                )
            continue
        max_parent_layer = -1
        for parent_ref in parent_refs:
            if parent_ref not in refs:
                raise GenerationValidationError(
                    f'{prefix}.parent_refs references unknown ref: {parent_ref}.'
                )
            parent_layer = refs[parent_ref]['layer']
            if parent_layer >= layer:
                raise GenerationValidationError(
                    f'{prefix}: layer must be greater than all parent layers.'
                )
            max_parent_layer = max(max_parent_layer, parent_layer)
        if layer <= max_parent_layer:
            raise GenerationValidationError(
                f'{prefix}: layer must be greater than all parent layers.'
            )

    # Cycle detection via DFS on parent edges (child -> parents reversed for topo)
    visiting: set[str] = set()
    visited: set[str] = set()

    def _has_cycle(node_ref: str) -> bool:
        if node_ref in visiting:
            return True
        if node_ref in visited:
            return False
        visiting.add(node_ref)
        for parent_ref in refs[node_ref]['parent_refs']:
            if _has_cycle(parent_ref):
                return True
        visiting.remove(node_ref)
        visited.add(node_ref)
        return False

    for ref in refs:
        if _has_cycle(ref):
            raise GenerationValidationError(
                'recommended_decision_tree contains a cycle in parent_refs.'
            )

    return value


def _validate_calendar_events(value: Any) -> list:
    if not isinstance(value, list):
        raise GenerationValidationError('calendar_events must be a list.')
    for idx, evt in enumerate(value):
        if not isinstance(evt, dict):
            raise GenerationValidationError(f'calendar_events[{idx}] must be an object.')
        for field in ('title', 'start_datetime', 'end_datetime'):
            if not isinstance(evt.get(field), str) or not str(evt.get(field)).strip():
                raise GenerationValidationError(
                    f'calendar_events[{idx}].{field} is required.'
                )
        for optional in ('location', 'description'):
            if optional in evt and evt[optional] is not None and not isinstance(evt[optional], str):
                raise GenerationValidationError(
                    f'calendar_events[{idx}].{optional} must be a string.'
                )
    return value


_VALIDATORS = {
    'recommended_tasks': _validate_recommended_tasks,
    'recommended_decision_tree': _validate_recommended_decision_tree,
    'calendar_events': _validate_calendar_events,
}


def validate_analysis_response(data: dict, requested: frozenset[str]) -> dict:
    """Ensure analysis JSON has exactly the requested analysis keys."""
    if not isinstance(data, dict):
        raise GenerationValidationError('Analysis response must be a JSON object.')
    expected = analysis_keys_for_request(requested)
    actual = frozenset(data.keys())
    if actual != expected:
        missing = expected - actual
        extra = actual - expected
        parts = []
        if missing:
            parts.append(f'missing keys: {", ".join(sorted(missing))}')
        if extra:
            parts.append(f'unexpected keys: {", ".join(sorted(extra))}')
        raise GenerationValidationError(
            f'Analysis JSON key mismatch ({"; ".join(parts)}).'
        )
    result = {}
    for key in expected:
        result[key] = _VALIDATORS[key](data[key])
    return result


def validate_calendar_events_response(data: dict) -> dict:
    """Dedicated calendar generation call must return only calendar_events."""
    if not isinstance(data, dict):
        raise GenerationValidationError('Calendar response must be a JSON object.')
    if frozenset(data.keys()) != frozenset({'calendar_events'}):
        raise GenerationValidationError(
            'Calendar JSON must contain only the calendar_events key.'
        )
    return {'calendar_events': _validate_calendar_events(data['calendar_events'])}


def filter_sse_analysis_payload(analysis: dict, requested: frozenset[str]) -> dict:
    """Return only analysis keys the user requested for SSE."""
    return {
        k: v
        for k, v in analysis.items()
        if k in analysis_keys_for_request(requested)
    }


def should_skip_workflow_step(step_type: str, requested: frozenset[str]) -> bool:
    if step_type == 'create_tasks' and 'recommended_tasks' not in requested:
        return True
    if step_type == 'create_decision' and 'recommended_decision_tree' not in requested:
        return True
    if step_type in ('generate_miro_snapshot', 'create_miro_board') and 'miro_board' not in requested:
        return True
    return False


_CALENDAR_FROM_ANALYSIS_SYSTEM = """\
You are a calendar planning assistant. Based on spreadsheet analysis context, suggest \
follow-up calendar events the user may want to schedule (reviews, check-ins, deadlines).

Return ONLY valid JSON (no markdown, no code fences) with this exact structure:

{
  "calendar_events": [
    {
      "title": "event title",
      "start_datetime": "YYYY-MM-DDTHH:MM:SS",
      "end_datetime": "YYYY-MM-DDTHH:MM:SS",
      "location": "optional location",
      "description": "optional description"
    }
  ]
}

Rules:
- Suggest 0-5 events when appropriate; use an empty array if none are warranted.
- Datetimes must be ISO-like strings without timezone offset.
- Return ONLY the JSON object, nothing else."""


def build_calendar_from_analysis_user_prompt(
    column_summary: str,
    cleaned_data: str,
    analysis_result: dict | None,
) -> str:
    analysis_blob = ''
    if analysis_result:
        import json
        analysis_blob = json.dumps(analysis_result, default=str)
    return (
        f"Data summary: {column_summary}\n\n"
        f"Spreadsheet sample rows:\n{cleaned_data}\n\n"
        f"Analysis context (if any):\n{analysis_blob or '{}'}\n\n"
        "Suggest calendar events the user should consider scheduling."
    )


def calendar_from_analysis_system_prompt() -> str:
    return _CALENDAR_FROM_ANALYSIS_SYSTEM
