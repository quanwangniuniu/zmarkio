import json
import math
import os
from pathlib import Path
from typing import Any

from django.conf import settings
from core.services.ollama_client import parse_json_text

from .agent_utils import json_input

import logging


MIRO_ALLOWED_ITEM_TYPES = {
    "text",
    "shape",
    "sticky_note",
    "frame",
    "line",
    "connector",
    "freehand",
}

MIRO_MAX_ITEM_COUNT = 50

MIRO_RULES_PATH = Path(__file__).with_name("miro_snapshot_rules.json")
MIRO_FRAME_PADDING_LEFT = 24
MIRO_FRAME_PADDING_RIGHT = 24
MIRO_FRAME_PADDING_TOP = 72
MIRO_FRAME_PADDING_BOTTOM = 24
MIRO_VERTICAL_GAP = 24
MIRO_MAX_OPTIONS = 10
MIRO_DECISION_TITLE_MAX_CHARS = 220
MIRO_DECISION_CONTEXT_SUMMARY_MAX_CHARS = 420
MIRO_DECISION_REASONING_MAX_CHARS = 640

logger = logging.getLogger(__name__)


def load_miro_snapshot_rules() -> dict[str, Any]:
    return json.loads(MIRO_RULES_PATH.read_text(encoding="utf-8"))


def _parse_number(value: Any, default: float = 0.0) -> float:
    """Convert a value to float, stripping CSS units like 'px', 'em', '%' if present."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip().rstrip("pxemremvwvh%").strip()
        try:
            return float(stripped)
        except (ValueError, TypeError):
            return default
    return default


def _sanitize_snapshot_numbers(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Strip CSS units from all numeric fields in an Ollama-generated snapshot."""
    numeric_item_fields = ("x", "y", "width", "height", "z_index")
    numeric_style_fields = ("fontSize", "strokeWidth", "borderRadius")

    items = snapshot.get("items", [])
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in numeric_item_fields:
            if field in item:
                item[field] = _parse_number(item[field], 0)
        style = item.get("style")
        if isinstance(style, dict):
            for field in numeric_style_fields:
                if field in style:
                    style[field] = _parse_number(style[field], 14 if field == "fontSize" else 0)
    return snapshot


def _truncate_text(value: str | None, limit: int) -> str:
    if not value:
        return ""
    value = str(value).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "..."


def _compact_anomalies(anomalies: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    compact = []
    for anomaly in anomalies or []:
        if not isinstance(anomaly, dict):
            continue
        compact.append(
            {
                "metric": anomaly.get("metric", ""),
                "movement": anomaly.get("movement", ""),
                "period": anomaly.get("period", ""),
                "scope_type": anomaly.get("scope_type", ""),
                "scope_value": anomaly.get("scope_value", ""),
                "description": anomaly.get("description", ""),
                "delta_value": anomaly.get("delta_value"),
                "delta_unit": anomaly.get("delta_unit", ""),
            }
        )
    return compact


def _compact_tasks(tasks: list[Any] | None) -> list[dict[str, Any]]:
    compact = []
    for task in tasks or []:
        if isinstance(task, dict):
            compact.append(
                {
                    "id": task.get("id"),
                    "summary": task.get("summary", ""),
                    "priority": task.get("priority", "MEDIUM"),
                    "type": task.get("type", "optimization"),
                }
            )
            continue
        compact.append(
            {
                "id": getattr(task, "id", None),
                "summary": getattr(task, "summary", ""),
                "priority": getattr(task, "priority", "MEDIUM"),
                "type": getattr(task, "type", "optimization"),
            }
        )
    return compact


def _compact_decision_payload(decision: Any | None) -> dict[str, Any] | None:
    if not decision:
        return None

    if isinstance(decision, dict):
        options = decision.get("options") or []
        return {
            "id": decision.get("id"),
            "title": _truncate_text(decision.get("title", ""), MIRO_DECISION_TITLE_MAX_CHARS),
            "context_summary": _truncate_text(decision.get("context_summary", ""), MIRO_DECISION_CONTEXT_SUMMARY_MAX_CHARS),
            "reasoning": _truncate_text(decision.get("reasoning", ""), MIRO_DECISION_REASONING_MAX_CHARS),
            "risk_level": decision.get("risk_level", ""),
            "confidence": decision.get("confidence"),
            "options": [
                {
                    "id": option.get("id"),
                    "text": _truncate_text(option.get("text", ""), 160),
                    "order": option.get("order"),
                    "is_selected": option.get("is_selected"),
                }
                for option in options[:MIRO_MAX_OPTIONS]
                if isinstance(option, dict)
            ],
        }

    option_manager = getattr(decision, "options", None)
    options = []
    if option_manager is not None and hasattr(option_manager, "all"):
        options = [
            {
                "id": option.id,
                "text": _truncate_text(option.text, 160),
                "order": option.order,
                "is_selected": option.is_selected,
            }
            for option in option_manager.all().order_by("order")[:MIRO_MAX_OPTIONS]
        ]
    return {
        "id": getattr(decision, "id", None),
        "title": _truncate_text(getattr(decision, "title", ""), MIRO_DECISION_TITLE_MAX_CHARS),
        "context_summary": _truncate_text(getattr(decision, "context_summary", ""), MIRO_DECISION_CONTEXT_SUMMARY_MAX_CHARS),
        "reasoning": _truncate_text(getattr(decision, "reasoning", ""), MIRO_DECISION_REASONING_MAX_CHARS),
        "risk_level": getattr(decision, "risk_level", ""),
        "confidence": getattr(decision, "confidence", None),
        "options": options,
    }


def build_miro_generation_context(
    *,
    analysis_result: dict[str, Any],
    session: Any | None = None,
    workflow_run: Any | None = None,
) -> dict[str, Any]:
    suggested_decision = None
    if analysis_result.get("suggested_decision"):
        suggested_decision = _compact_decision_payload(analysis_result.get("suggested_decision"))

    return {
        "session": {
            "id": str(getattr(session, "id", "")) if session else "",
            "title": getattr(session, "title", "") if session else "",
            "project_id": getattr(getattr(session, "project", None), "id", None)
            if session
            else None,
        },
        "workflow_run": {
            "id": str(getattr(workflow_run, "id", "")) if workflow_run else "",
            "status": getattr(workflow_run, "status", "") if workflow_run else "",
        },
        "analysis": {
            "anomalies": _compact_anomalies(analysis_result.get("anomalies", [])),
            "suggested_decision": suggested_decision,
            "recommended_tasks": _compact_tasks(analysis_result.get("recommended_tasks", [])),
        },
    }


def build_miro_generation_context_from_run(*, session: Any, workflow_run: Any) -> dict[str, Any]:
    return build_miro_generation_context(
        analysis_result=getattr(workflow_run, "analysis_result", {}) or {},
        session=session,
        workflow_run=workflow_run,
    )


def serialize_miro_generation_context(context: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe copy of the Miro generation context for Celery."""
    if not isinstance(context, dict):
        raise ValueError("Miro generation context must be a dictionary")
    return json.loads(json.dumps(context, default=str))


def deserialize_miro_generation_context(payload: Any) -> dict[str, Any] | None:
    """Rehydrate a Miro generation context from a Celery payload."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("Miro generation context payload must be a dictionary")
    return serialize_miro_generation_context(payload)


def _extract_snapshot_candidate(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        if "viewport" in payload or "items" in payload:
            return payload

        for key in ("snapshot", "result", "text", "output", "analysis"):
            value = payload.get(key)
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict) and ("viewport" in parsed or "items" in parsed):
                    return parsed
            elif isinstance(value, dict) and ("viewport" in value or "items" in value):
                return value

    if isinstance(payload, str):
        parsed = json.loads(payload)
        if isinstance(parsed, dict) and ("viewport" in parsed or "items" in parsed):
            return parsed

    raise ValueError("Dify returned unexpected Miro snapshot format")


def _count_wrapped_lines(content: str, width: float, font_size: float) -> int:
    lines = max(1, content.count("\n") + 1)
    estimated_chars_per_line = max(8, int(width / max(font_size * 0.58, 1)))
    wrapped_lines = 0
    for raw_line in content.splitlines() or [content]:
        line = raw_line.strip()
        if not line:
            wrapped_lines += 1
            continue
        wrapped_lines += max(1, math.ceil(len(line) / estimated_chars_per_line))
    return max(lines, wrapped_lines)


def _estimate_item_height(item: dict[str, Any]) -> float:
    item_type = item.get("type")
    style = item.get("style", {}) or {}
    width = max(float(item.get("width", 1)), 1.0)
    content = item.get("content", "") or ""
    font_size = float(style.get("fontSize", 14) or 14)
    is_bold = str(style.get("fontWeight", "")).lower() in {"bold", "600", "700", "800", "900"}
    line_height = font_size * (1.45 if item_type == "text" else 1.35)
    wrapped_lines = _count_wrapped_lines(content, width - 24, font_size)

    if item_type == "text":
        padding = 18 if is_bold else 14
    elif item_type == "shape":
        padding = 30 if is_bold else 24
    elif item_type == "sticky_note":
        padding = 28 if is_bold else 24
    else:
        return float(item.get("height", 1))

    if "\n" in content:
        padding += 6
    if len(content) > 120:
        padding += 8

    estimated_height = wrapped_lines * line_height + padding
    return max(float(item.get("height", 1)), math.ceil(estimated_height))


def _cluster_frame_columns(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not items:
        return []

    sorted_items = sorted(items, key=lambda item: (float(item.get("x", 0)), float(item.get("y", 0))))
    columns: list[list[dict[str, Any]]] = []
    threshold = 120

    for item in sorted_items:
        item_x = float(item.get("x", 0))
        placed = False
        for column in columns:
            avg_x = sum(float(existing.get("x", 0)) for existing in column) / len(column)
            if abs(item_x - avg_x) <= threshold:
                column.append(item)
                placed = True
                break
        if not placed:
            columns.append([item])

    for column in columns:
        column.sort(key=lambda item: (float(item.get("y", 0)), float(item.get("x", 0))))

    return columns


def normalize_miro_snapshot_layout(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return snapshot

    items = snapshot.get("items")
    if not isinstance(items, list):
        return snapshot

    frames = {
        str(item.get("id")): item
        for item in items
        if isinstance(item, dict) and item.get("type") == "frame"
    }
    child_items_by_frame: dict[str, list[dict[str, Any]]] = {frame_id: [] for frame_id in frames}

    for item in items:
        if not isinstance(item, dict):
            continue
        parent_id = item.get("parent_item_id")
        item_type = item.get("type")
        if parent_id is None or str(parent_id) not in frames:
            continue
        if item_type not in {"text", "shape", "sticky_note"}:
            continue
        child_items_by_frame[str(parent_id)].append(item)

    for frame_id, frame in frames.items():
        child_items = child_items_by_frame.get(frame_id) or []
        if not child_items:
            continue

        frame_x = float(frame.get("x", 0))
        frame_y = float(frame.get("y", 0))
        frame_width = max(float(frame.get("width", 1)), 1.0)
        frame_height = max(float(frame.get("height", 1)), 1.0)
        content_top = frame_y + MIRO_FRAME_PADDING_TOP
        max_content_bottom = content_top

        columns = _cluster_frame_columns(child_items)
        for column in columns:
            if not column:
                continue
            cursor_y = content_top
            column_left = max(
                frame_x + MIRO_FRAME_PADDING_LEFT,
                min(float(column[0].get("x", frame_x + MIRO_FRAME_PADDING_LEFT)),
                    frame_x + frame_width - MIRO_FRAME_PADDING_RIGHT - max(float(column[0].get("width", 1)), 1.0))
            )

            for item in column:
                item_width = max(float(item.get("width", 1)), 1.0)
                item_height = _estimate_item_height(item)

                max_x = frame_x + frame_width - MIRO_FRAME_PADDING_RIGHT - item_width
                item["x"] = max(frame_x + MIRO_FRAME_PADDING_LEFT, min(column_left, max_x))
                item["height"] = item_height
                item["y"] = max(cursor_y, frame_y + MIRO_FRAME_PADDING_TOP)

                cursor_y = float(item["y"]) + item_height + MIRO_VERTICAL_GAP
                max_content_bottom = max(max_content_bottom, float(item["y"]) + item_height)

        required_height = max_content_bottom - frame_y + MIRO_FRAME_PADDING_BOTTOM
        if required_height > frame_height:
            frame["height"] = math.ceil(required_height)

    return snapshot


def validate_miro_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("Snapshot must be a dictionary")

    viewport = snapshot.get("viewport", {})
    if viewport is None:
        viewport = {}
    if not isinstance(viewport, dict):
        raise ValueError("Snapshot viewport must be a dictionary")

    items = snapshot.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Snapshot items must be a list")
    if len(items) > MIRO_MAX_ITEM_COUNT:
        raise ValueError(f"Snapshot contains too many items ({len(items)})")

    seen_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each snapshot item must be a dictionary")

        item_id = str(item.get("id", "")).strip()
        if not item_id:
            raise ValueError("Each snapshot item must have a non-empty id")
        if item_id in seen_ids:
            raise ValueError(f"Duplicate snapshot item id: {item_id}")
        seen_ids.add(item_id)

        item_type = item.get("type")
        if item_type not in MIRO_ALLOWED_ITEM_TYPES:
            raise ValueError(f"Unsupported item type: {item_type}")

        for field in ("x", "y", "width", "height", "z_index"):
            value = item.get(field)
            if not isinstance(value, (int, float)):
                raise ValueError(f"Item {item_id} has non-numeric {field}")

        if item["width"] <= 0 or item["height"] <= 0:
            raise ValueError(f"Item {item_id} must have positive width and height")

        style = item.get("style", {})
        if not isinstance(style, dict):
            raise ValueError(f"Item {item_id} style must be a dictionary")

        content = item.get("content", "")
        if not isinstance(content, str):
            raise ValueError(f"Item {item_id} content must be a string")

        parent_item_id = item.get("parent_item_id")
        if parent_item_id is not None and str(parent_item_id) not in seen_ids and str(parent_item_id) not in {
            str(other.get("id", "")) for other in items
        }:
            raise ValueError(f"Item {item_id} references missing parent_item_id")

    return {
        "viewport": viewport,
        "items": items,
    }


_MIRO_SYSTEM_PROMPT = """\
You are generating a board snapshot for MediaJira's built-in Miro module.

Return exactly one valid board snapshot JSON object.

Requirements:
1. Output JSON only.
2. No markdown fences.
3. Follow miro_snapshot_rules.
4. Use only allowed item types.
5. Keep text short and readable.
6. Prefer human-readable summaries.
7. Raw campaign names may appear only as short secondary text.
8. Use a simple left-to-right layout with frames.
9. Use only sections supported by the input.
10. If decision options exist, show them clearly.
11. Every item must have positive width and height.
12. Keep the total item count within the schema limit.
13. Keep all child items fully inside their parent frame bounds.
14. When information is dense, compress content instead of creating too many separate items.
15. Prefer fewer, higher-signal items over exhaustive one-to-one rendering.

Focus only on:
- anomalies
- suggested_decision
- recommended_tasks

Compression rules:
- Do not try to render every input entry as its own shape or sticky note when that would make the board overcrowded.
- When anomalies are numerous, group related anomalies into concise summary notes.
- When recommended tasks are numerous, prioritize the highest-signal tasks and merge similar tasks into short grouped items.
- When decision reasoning or context is long, summarize it into one compact text or shape block.
- When decision options are numerous, always include the selected option and the most important alternatives first.
- If space is tight, reduce the number of sections or items before reducing readability.

Layout rules:
- Frames are containers and should be large enough for all of their child items.
- Child items must not overflow outside their frame.
- Keep a clean reading order from left to right and top to bottom.
- Use concise section titles and avoid oversized text blocks.
- Prefer one summary block plus a few focused detail items rather than many small fragmented blocks.

Create a practical draft board with concise labels and reasonable initial positions.\
"""


def call_ollama_miro_generator(
    context: dict[str, Any],
    *,
    user_id: str | int | None = None,
    agent_session=None,
) -> dict[str, Any]:
    from .llm_client import call_llm as _call_llm_unified

    context_json = json_input(context)
    rules_json = json_input(load_miro_snapshot_rules())
    logger.info(
        "Calling Ollama Miro generator user_id=%s context_chars=%s rules_chars=%s",
        user_id,
        len(context_json),
        len(rules_json),
    )
    result = _call_llm_unified(
        agent_session=agent_session,
        provider='ollama',
        model=settings.AGENT_LLM_MODEL,
        system_prompt=_MIRO_SYSTEM_PROMPT,
        user_prompt=(
            f"board_generation_context:\n{context_json}\n\n"
            f"miro_snapshot_rules:\n{rules_json}\n\n"
            f"Return the final board snapshot JSON now."
        ),
        temperature=0.5,
        max_output_tokens=4096,
        response_mime_type='application/json',
        call_purpose='miro_generation',
    )
    outputs = parse_json_text(result['text'])
    snapshot = _extract_snapshot_candidate(outputs)
    snapshot = _sanitize_snapshot_numbers(snapshot)
    snapshot = normalize_miro_snapshot_layout(snapshot)
    return validate_miro_snapshot(snapshot)
