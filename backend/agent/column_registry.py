"""
Column schema registry for uploaded spreadsheets.

Detection pipeline:
  1. DB-template match against DataSchemaTemplate records (instant, no LLM cost).
     Falls back to the hard-coded SCHEMA_REGISTRY if the DB is unavailable.
  2. Rule-based match against the hard-coded SCHEMA_REGISTRY (instant fallback).
  3. LLM fallback (Dify / Gemini) for files that do not match any known schema.
     On success the result is optionally saved as a new learned DataSchemaTemplate.
  4. Keyword-based auto-categorisation for columns that remain 'unknown' after
     all detection paths, so results are never silently lost.

Public API:
  detect_columns(headers)         -> ColumnDetectionResult
  normalize_spreadsheet(data, column_mapping) -> normalized data dict
  auto_categorize_by_name(canonical_name)     -> category string
  save_learned_template(schema_name, source_platform, columns, project) -> DataSchemaTemplate | None
  register_schema(schema_key, schema)         -> registered schema
  register_column(schema_key, canonical_name, spec) -> registered column
"""

import json
import logging
import os
import re
from copy import deepcopy

logger = logging.getLogger(__name__)

COLUMN_REGISTRY_TEST_MODE_ENV = "AGENT_COLUMN_REGISTRY_TEST_MODE"
_COLUMN_REGISTRY_TEST_MODE_ALIASES = ("COLUMN_REGISTRY_TEST_MODE",)
_TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


class ColumnRegistryCollisionError(ValueError):
    """Raised when two registered schemas claim the same lookup name."""

    code = "COLUMN_REGISTRY_COLLISION"

    def __init__(self, collisions):
        self.collisions = tuple(collisions)
        details = "; ".join(
            f"{collision['name']!r} ({', '.join(collision['owners'])})"
            for collision in self.collisions
        )
        super().__init__(f"Column registry name collision: {details}")

# ---------------------------------------------------------------------------
# Semantic category constants
# ---------------------------------------------------------------------------
CAT_IDENTIFIER = "identifier"
CAT_FINANCIAL = "financial"
CAT_ENGAGEMENT = "engagement"
CAT_CONVERSION = "conversion"
CAT_PERFORMANCE_RATIO = "performance_ratio"
CAT_TEMPORAL = "temporal"
CAT_UNKNOWN = "unknown"

# ---------------------------------------------------------------------------
# Keyword-based auto-categorisation
# When AI detection returns 'unknown', we apply these rules as a last-resort
# fallback so the user sees a best-guess category rather than a blank.
# Rules are checked in order; the first match wins.
# ---------------------------------------------------------------------------
_AUTO_CATEGORY_RULES = [
    (CAT_TEMPORAL, [
        'date', 'week', 'month', 'year', 'quarter', 'period', 'time', 'day',
        'start', 'end', 'reporting',
    ]),
    (CAT_FINANCIAL, [
        'spend', 'cost', 'budget', 'revenue', 'roas', 'cpm', 'cpc', 'cpp',
        'cpa', 'amount', 'price', 'fee', 'profit', 'margin', 'value',
    ]),
    (CAT_CONVERSION, [
        'purchase', 'conversion', 'lead', 'signup', 'sign_up', 'checkout',
        'add_to_cart', 'cart', 'install', 'registration', 'subscribe',
    ]),
    (CAT_ENGAGEMENT, [
        'impression', 'click', 'reach', 'view', 'watch', 'engagement',
        'like', 'share', 'comment', 'video', 'thruplay', 'frequency',
        'interaction',
    ]),
    (CAT_PERFORMANCE_RATIO, [
        'ctr', 'cvr', 'rate', 'ratio', 'score', 'index', 'efficiency',
        'percentage', 'pct', 'percent',
    ]),
    (CAT_IDENTIFIER, [
        'name', 'id', 'campaign', 'ad_set', 'adset', 'ad', 'creative',
        'label', 'tag', 'group', 'account', 'brand', 'product', 'sku',
        'category', 'channel', 'platform', 'source', 'medium',
    ]),
]


def auto_categorize_by_name(canonical_name: str) -> str:
    """
    Infer a semantic category from a canonical column name using keyword rules.

    This is a best-effort fallback called when AI detection returns 'unknown'.
    Returns the inferred category string, or CAT_UNKNOWN if no rule matches.

    Args:
        canonical_name: snake_case column name, e.g. 'total_ad_spend'

    Returns:
        One of the CAT_* constants.
    """
    if not canonical_name or canonical_name == CAT_UNKNOWN:
        return CAT_UNKNOWN

    normalized_name = re.sub(r'[\s\-]+', '_', canonical_name.lower()).strip('_')
    padded_name = f'_{normalized_name}_'

    for category, keywords in _AUTO_CATEGORY_RULES:
        for kw in keywords:
            normalized_keyword = re.sub(r'[\s\-]+', '_', kw.lower()).strip('_')
            if f'_{normalized_keyword}_' in padded_name:
                return category

    return CAT_UNKNOWN


# ---------------------------------------------------------------------------
# DB-template detection
# ---------------------------------------------------------------------------

def _try_db_template_match(headers: list) -> "ColumnDetectionResult | None":
    """
    Attempt to match headers against DataSchemaTemplate records in the DB.

    Returns the best-matching result if confidence >= template.match_threshold,
    else None.  Silently skips if the DB or model is unavailable (e.g. during
    tests or first-run before migrations).
    """
    try:
        from .models import DataSchemaTemplate
        templates = DataSchemaTemplate.objects.filter(
            is_deleted=False,
        ).order_by('-usage_count', '-is_system')
    except Exception:
        return None

    normalised = [_normalise(h) for h in headers]
    best_result = None
    best_confidence = 0.0

    for template in templates:
        col_defs = template.column_definitions or []
        if not col_defs:
            continue

        # Build alias index for this template
        alias_index = {}
        cat_map = {}
        for col in col_defs:
            canonical = col.get('canonical_name', '')
            category = col.get('category', CAT_UNKNOWN)
            for alias in col.get('aliases', []):
                alias_index[_normalise(alias)] = canonical
            alias_index[_normalise(canonical)] = canonical
            cat_map[canonical] = category

        mappings = {}
        categories = {}
        unrecognized = []
        matched = 0

        for original, norm in zip(headers, normalised):
            canonical = alias_index.get(norm)
            if canonical:
                mappings[original] = canonical
                categories[canonical] = cat_map.get(canonical, CAT_UNKNOWN)
                matched += 1
            else:
                mappings[original] = CAT_UNKNOWN
                unrecognized.append(original)

        total = len(headers)
        confidence = matched / total if total else 0.0

        if confidence >= template.match_threshold and confidence > best_confidence:
            best_confidence = confidence
            best_result = ColumnDetectionResult(
                schema_key=str(template.id),
                schema_name=template.name,
                source="db_template",
                confidence=round(confidence, 3),
                mappings=mappings,
                categories=categories,
                unrecognized=unrecognized,
            )
            best_result._matched_template_id = str(template.id)

    return best_result


# ---------------------------------------------------------------------------
# Save a learned template back to the DB
# ---------------------------------------------------------------------------

def save_learned_template(schema_name: str, source_platform: str,
                          columns: list, project=None) -> "DataSchemaTemplate | None":
    """
    Persist a new DataSchemaTemplate discovered by the LLM.

    Args:
        schema_name:     Human-readable name returned by the LLM.
        source_platform: Platform hint (defaults to 'custom').
        columns:         List of {canonical_name, aliases, category, value_type, description}.
        project:         Optional Project instance to scope the template.

    Returns:
        The created DataSchemaTemplate, or None if saving failed.
    """
    if not columns:
        return None
    try:
        from .models import DataSchemaTemplate
        template, created = DataSchemaTemplate.objects.get_or_create(
            name=schema_name,
            project=project,
            defaults={
                'source_platform': source_platform or 'custom',
                'is_system': False,
                'is_learned': True,
                'column_definitions': columns,
            },
        )
        if not created:
            # Update column definitions if the template already exists
            template.column_definitions = columns
            template.save(update_fields=['column_definitions', 'updated_at'])
        return template
    except Exception:
        logger.exception("Failed to save learned DataSchemaTemplate '%s'", schema_name)
        return None

# ---------------------------------------------------------------------------
# Schema registry
# Each schema entry:
#   name        — human-readable schema name
#   source_hint — optional origin label shown to users
#   columns     — {canonical_name: {aliases, category, description}}
# ---------------------------------------------------------------------------

SCHEMA_REGISTRY = {
    "meta_ads": {
        "name": "Meta Ads Performance",
        "source_hint": "Meta Ads Manager export",
        "columns": {
            # --- Identifiers ---
            "campaign_name": {
                "aliases": ["campaign name", "campaign", "campaign_name"],
                "category": CAT_IDENTIFIER,
                "description": "Campaign name",
            },
            "ad_set_name": {
                "aliases": [
                    "ad set name", "adset name", "adset_name", "ad_set_name",
                ],
                "category": CAT_IDENTIFIER,
                "description": "Ad set name",
            },
            "ad_name": {
                "aliases": [
                    "ad name", "ad_name", "creative name", "creative_name",
                ],
                "category": CAT_IDENTIFIER,
                "description": "Ad name / creative",
            },
            "campaign_id": {
                "aliases": ["campaign id", "campaign_id"],
                "category": CAT_IDENTIFIER,
                "description": "Campaign ID",
            },
            "ad_set_id": {
                "aliases": [
                    "ad set id", "adset id", "adset_id", "ad_set_id",
                ],
                "category": CAT_IDENTIFIER,
                "description": "Ad set ID",
            },
            "ad_id": {
                "aliases": ["ad id", "ad_id"],
                "category": CAT_IDENTIFIER,
                "description": "Ad ID",
            },
            # --- Financial ---
            "amount_spent": {
                "aliases": [
                    "amount spent", "amount_spent", "spend", "ad spend",
                    "ad_spend", "cost", "total spend", "total_spend",
                ],
                "category": CAT_FINANCIAL,
                "description": "Total amount spent (currency)",
            },
            "cpm": {
                "aliases": [
                    "cpm", "cost per 1000 impressions",
                    "cost per thousand impressions",
                ],
                "category": CAT_FINANCIAL,
                "description": "Cost per 1,000 impressions",
            },
            "cpc": {
                "aliases": ["cpc", "cost per click", "cost per link click"],
                "category": CAT_FINANCIAL,
                "description": "Cost per click",
            },
            "cpp": {
                "aliases": ["cpp", "cost per purchase", "cost per result"],
                "category": CAT_FINANCIAL,
                "description": "Cost per purchase / result",
            },
            # --- Engagement ---
            "impressions": {
                "aliases": ["impressions", "impression"],
                "category": CAT_ENGAGEMENT,
                "description": "Total impressions",
            },
            "reach": {
                "aliases": ["reach", "unique reach"],
                "category": CAT_ENGAGEMENT,
                "description": "Unique accounts reached",
            },
            "clicks": {
                "aliases": ["clicks", "all clicks", "total clicks"],
                "category": CAT_ENGAGEMENT,
                "description": "Total clicks (all)",
            },
            "link_clicks": {
                "aliases": ["link clicks", "link_clicks", "unique link clicks"],
                "category": CAT_ENGAGEMENT,
                "description": "Link clicks",
            },
            "frequency": {
                "aliases": ["frequency"],
                "category": CAT_ENGAGEMENT,
                "description": "Average times each person saw the ad",
            },
            "video_views": {
                "aliases": [
                    "video views", "video_views", "3-second video views",
                    "thruplays", "thruplay", "2-second continuous video views",
                ],
                "category": CAT_ENGAGEMENT,
                "description": "Video views",
            },
            # --- Conversion ---
            "purchases": {
                "aliases": [
                    "purchases", "purchase", "conversions", "results",
                    "website purchases", "omni purchases",
                ],
                "category": CAT_CONVERSION,
                "description": "Purchase / conversion events",
            },
            "purchase_roas": {
                "aliases": [
                    "purchase roas", "purchase_roas", "roas",
                    "website purchase roas",
                ],
                "category": CAT_CONVERSION,
                "description": "Return on ad spend (purchases)",
            },
            "add_to_cart": {
                "aliases": [
                    "add to cart", "add_to_cart", "adds to cart",
                    "website adds to cart",
                ],
                "category": CAT_CONVERSION,
                "description": "Add-to-cart events",
            },
            "leads": {
                "aliases": [
                    "leads", "lead", "on-facebook leads", "website leads",
                ],
                "category": CAT_CONVERSION,
                "description": "Lead generation events",
            },
            # --- Performance ratios ---
            "ctr": {
                "aliases": [
                    "ctr", "click-through rate", "click through rate",
                    "ctr (all)", "all ctr",
                ],
                "category": CAT_PERFORMANCE_RATIO,
                "description": "Click-through rate (all clicks / impressions)",
            },
            "link_ctr": {
                "aliases": [
                    "link ctr", "link_ctr",
                    "ctr (link click-through rate)",
                ],
                "category": CAT_PERFORMANCE_RATIO,
                "description": "Link click-through rate",
            },
        },
    },
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lowercase and collapse whitespace/underscores for fuzzy matching."""
    return re.sub(r"[\s_]+", " ", text.strip().lower())


def column_registry_test_mode_enabled() -> bool:
    """Return whether duplicate registration is explicitly allowed for tests."""
    env_names = (COLUMN_REGISTRY_TEST_MODE_ENV,) + _COLUMN_REGISTRY_TEST_MODE_ALIASES
    return any(
        os.environ.get(name, "").strip().lower() in _TRUE_ENV_VALUES
        for name in env_names
    )


def _find_registry_collisions(registry: dict) -> list[dict]:
    """Find duplicate canonical names and aliases across all registered schemas."""
    owners_by_lookup_name = {}

    for schema_key, schema in registry.items():
        for canonical_name, spec in (schema.get("columns") or {}).items():
            owner = f"{schema_key}.{canonical_name}"
            lookup_names = [canonical_name, *(spec.get("aliases") or [])]
            for lookup_name in lookup_names:
                normalised_name = _normalise(str(lookup_name))
                if not normalised_name:
                    continue
                owners_by_lookup_name.setdefault(normalised_name, set()).add(owner)

    return [
        {"name": lookup_name, "owners": sorted(owners)}
        for lookup_name, owners in sorted(owners_by_lookup_name.items())
        if len(owners) > 1
    ]


def validate_registry(registry: dict | None = None, *, test_mode: bool | None = None) -> list[dict]:
    """Validate a schema registry and return any collisions found.

    In normal application mode collisions are fatal. Tests can opt out by
    setting ``AGENT_COLUMN_REGISTRY_TEST_MODE=1`` (or by passing
    ``test_mode=True`` explicitly) so they can construct intentionally
    overlapping fixtures.
    """
    registry = SCHEMA_REGISTRY if registry is None else registry
    collisions = _find_registry_collisions(registry)
    allow_collisions = (
        column_registry_test_mode_enabled() if test_mode is None else test_mode
    )
    if collisions and not allow_collisions:
        raise ColumnRegistryCollisionError(collisions)
    if collisions:
        logger.warning(
            "Column registry contains %d collision(s); allowed in test mode: %s",
            len(collisions),
            ", ".join(c["name"] for c in collisions),
        )
    return collisions


def _build_alias_index(schema: dict) -> dict:
    """Return {normalised_alias: canonical_name} for a single schema."""
    index = {}
    for canonical, spec in schema["columns"].items():
        for alias in [canonical, *(spec.get("aliases") or [])]:
            index[_normalise(alias)] = canonical
    return index


# Pre-compute alias indexes for every schema at import time.
_SCHEMA_INDEXES = {
    schema_key: _build_alias_index(schema)
    for schema_key, schema in SCHEMA_REGISTRY.items()
}


def _rebuild_schema_indexes() -> None:
    """Rebuild lookup indexes after a plugin registers a schema or column."""
    global _SCHEMA_INDEXES
    _SCHEMA_INDEXES = {
        schema_key: _build_alias_index(schema)
        for schema_key, schema in SCHEMA_REGISTRY.items()
    }


def _commit_registry(candidate: dict) -> None:
    """Validate and atomically replace the live registry contents."""
    validate_registry(candidate)
    SCHEMA_REGISTRY.clear()
    SCHEMA_REGISTRY.update(candidate)
    _rebuild_schema_indexes()


def register_schema(schema_key: str, schema: dict) -> dict:
    """Register a schema without silently replacing an existing schema.

    Plugin startup code should call this function instead of mutating
    ``SCHEMA_REGISTRY`` directly. The candidate registry is validated before
    the live registry or its indexes are changed.
    """
    if not schema_key or not isinstance(schema, dict):
        raise ValueError("schema_key must be non-empty and schema must be a dict")

    if schema_key in SCHEMA_REGISTRY and not column_registry_test_mode_enabled():
        raise ColumnRegistryCollisionError([
            {
                "name": schema_key,
                "owners": [schema_key, schema_key],
            }
        ])

    candidate = deepcopy(SCHEMA_REGISTRY)
    candidate[schema_key] = deepcopy(schema)
    _commit_registry(candidate)
    return SCHEMA_REGISTRY[schema_key]


def register_column(schema_key: str, canonical_name: str, spec: dict) -> dict:
    """Register one column in an existing schema with collision protection."""
    if schema_key not in SCHEMA_REGISTRY:
        raise KeyError(f"Unknown column schema: {schema_key}")
    if not canonical_name or not isinstance(spec, dict):
        raise ValueError("canonical_name must be non-empty and spec must be a dict")

    existing_columns = SCHEMA_REGISTRY[schema_key].get("columns") or {}
    if canonical_name in existing_columns and not column_registry_test_mode_enabled():
        raise ColumnRegistryCollisionError([
            {
                "name": canonical_name,
                "owners": [
                    f"{schema_key}.{canonical_name}",
                    f"{schema_key}.{canonical_name} (new registration)",
                ],
            }
        ])

    candidate = deepcopy(SCHEMA_REGISTRY)
    columns = candidate[schema_key].setdefault("columns", {})
    columns[canonical_name] = deepcopy(spec)
    _commit_registry(candidate)
    return SCHEMA_REGISTRY[schema_key]["columns"][canonical_name]


# Validate the built-in registry as soon as the module is loaded. AgentConfig
# repeats this after Django has loaded plugin apps, catching hot-reload changes.
validate_registry()

# ---------------------------------------------------------------------------
# Detection result
# ---------------------------------------------------------------------------

class ColumnDetectionResult:
    """
    Outcome of column detection.

    Attributes:
        schema_key         — matched schema key, or None
        schema_name        — human-readable schema name, or "Unknown format"
        source             — "rule" | "llm" | "none"
        confidence         — 0.0 – 1.0  (overall schema confidence)
        mappings           — {original_header: canonical_name or "unknown"}
        categories         — {canonical_name: category_string}
        unrecognized       — original headers that could not be mapped
        column_confidences — {original_header: 0.0–1.0}
                             Per-column confidence scores.
                             Rule-based matches are always 1.0; LLM-detected
                             columns carry the score returned by the model;
                             unrecognized columns are 0.0.
    """

    def __init__(self, *, schema_key, schema_name, source, confidence,
                 mappings, categories, unrecognized, column_confidences=None):
        self.schema_key = schema_key
        self.schema_name = schema_name
        self.source = source
        self.confidence = confidence
        self.mappings = mappings
        self.categories = categories
        self.unrecognized = unrecognized
        # Default: 1.0 for every mapped column, 0.0 for unrecognized
        if column_confidences is not None:
            self.column_confidences = column_confidences
        else:
            unrecognized_set = set(unrecognized)
            self.column_confidences = {
                h: (0.0 if h in unrecognized_set else 1.0)
                for h in mappings
            }

    def to_dict(self):
        return {
            "schema_key": self.schema_key,
            "schema_name": self.schema_name,
            "source": self.source,
            "confidence": self.confidence,
            "mappings": self.mappings,
            "categories": self.categories,
            "unrecognized": self.unrecognized,
            "column_confidences": self.column_confidences,
        }


# ---------------------------------------------------------------------------
# Rule-based detection
# ---------------------------------------------------------------------------

def _try_rule_match(headers: list) -> "ColumnDetectionResult | None":
    """
    Attempt to match headers against each registered schema.
    Returns the best-matching result if confidence >= 0.5, else None.
    """
    normalised = [_normalise(h) for h in headers]
    best_result = None
    best_confidence = 0.0

    for schema_key, alias_index in _SCHEMA_INDEXES.items():
        schema = SCHEMA_REGISTRY[schema_key]
        mappings = {}
        categories = {}
        unrecognized = []
        matched = 0

        for original, norm in zip(headers, normalised):
            canonical = alias_index.get(norm)
            if canonical:
                mappings[original] = canonical
                categories[canonical] = schema["columns"][canonical]["category"]
                matched += 1
            else:
                mappings[original] = CAT_UNKNOWN
                unrecognized.append(original)

        total = len(headers)
        confidence = matched / total if total else 0.0

        if confidence > best_confidence:
            best_confidence = confidence
            best_result = ColumnDetectionResult(
                schema_key=schema_key,
                schema_name=schema["name"],
                source="rule",
                confidence=round(confidence, 3),
                mappings=mappings,
                categories=categories,
                unrecognized=unrecognized,
            )

    if best_result and best_confidence >= 0.5:
        return best_result
    return None


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------

_LLM_SYSTEM_PROMPT = """You are a data schema expert. Given a list of spreadsheet column headers and sample rows, identify what each column represents.

Respond with a JSON object in exactly this format:
{
  "schema_name": "Brief name describing this data format",
  "confidence": 0.85,
  "columns": [
    {
      "original": "original column header",
      "canonical": "snake_case semantic name or 'unknown'",
      "category": "one of: identifier, financial, engagement, conversion, performance_ratio, unknown",
      "confidence": 0.95
    }
  ]
}

Rules:
- Use 'unknown' for canonical/category when you cannot reliably identify a column.
- Never guess at financial or conversion columns — only label them if you are confident.
- confidence at the top level is a float 0.0-1.0 for overall schema certainty.
- confidence inside each column entry is a float 0.0-1.0 for that specific column.
- canonical must be snake_case or 'unknown'.
"""

# Maximum number of sample rows to include in the LLM prompt.
_LLM_SAMPLE_ROW_LIMIT = 3


_COLUMN_DETECTION_SYSTEM_PROMPT = """\
You are a spreadsheet schema expert. Given a list of column headers and optional sample rows, \
identify what each column represents.

Return ONLY valid JSON (no markdown, no explanation) with this structure:
{
  "schema_name": "brief name for this dataset type (e.g. Meta Ads Performance)",
  "confidence": 0.85,
  "columns": [
    {
      "original": "exact column name from input",
      "canonical": "canonical field name (snake_case) or 'unknown'",
      "category": "one of: metric, dimension, date, identifier, text, unknown",
      "confidence": 0.9
    }
  ]
}

Rules:
- Include every column from the input in the output.
- Use 'unknown' for canonical/category when the column cannot be identified.
- confidence values must be between 0.0 and 1.0.\
"""


def _try_llm_fallback(headers: list, sample_rows: list = None, agent_session=None) -> ColumnDetectionResult:
    """
    Use Gemini to identify unknown columns.

    Args:
        headers:       list of column header strings.
        sample_rows:   optional list of row dicts to help the LLM identify column types
                       from actual values. At most _LLM_SAMPLE_ROW_LIMIT rows are sent.
        agent_session: forwarded to call_llm for quota billing.

    Falls back to a full-unknown result if the LLM call fails (QuotaError propagates).
    """
    from stripe_meta.exceptions import QuotaError
    from agent.llm_client import call_llm as _call_llm_unified
    from core.services.gemini_client import _get_api_key as _gemini_key

    if not _gemini_key():
        logger.warning("GEMINI_API_KEY not set; skipping LLM column detection")
        return _unknown_result(headers)

    try:
        rows_to_send = (sample_rows or [])[:_LLM_SAMPLE_ROW_LIMIT]
        result = _call_llm_unified(
            agent_session=agent_session,
            provider='gemini',
            model='gemini-2.5-flash-lite',
            system_prompt=_COLUMN_DETECTION_SYSTEM_PROMPT,
            user_prompt=(
                f"Column headers: {', '.join(headers)}\n"
                f"Sample rows:\n{json.dumps(rows_to_send, default=str)}"
            ),
            temperature=0.2,
            max_output_tokens=2048,
            response_mime_type='application/json',
            call_purpose='column_detection',
        )
        parsed = json.loads(result['text'])
        return _parse_llm_response(headers, parsed)

    except QuotaError:
        raise
    except Exception:
        logger.exception("LLM column detection failed; falling back to unknown")
        return _unknown_result(headers)


def _parse_llm_response(headers: list, parsed: dict) -> ColumnDetectionResult:
    """Convert the LLM JSON response into a ColumnDetectionResult."""
    schema_name = parsed.get("schema_name", "Unknown format")
    confidence = float(parsed.get("confidence", 0.0))
    columns_list = parsed.get("columns", [])

    # Normalise keys for case-insensitive / whitespace-underscore-insensitive matching
    # e.g. "Campaign Name" matches "campaign_name"
    columns_by_original = {_normalise(c["original"]): c for c in columns_list}

    mappings = {}
    categories = {}
    column_confidences = {}
    unrecognized = []

    for header in headers:
        col_info = columns_by_original.get(_normalise(header), {})
        canonical = col_info.get("canonical", "unknown") or "unknown"
        category = col_info.get("category", CAT_UNKNOWN) or CAT_UNKNOWN
        col_confidence = float(col_info.get("confidence", 0.0))

        if canonical == "unknown":
            mappings[header] = CAT_UNKNOWN
            column_confidences[header] = 0.0
            unrecognized.append(header)
        else:
            mappings[header] = canonical
            categories[canonical] = category
            column_confidences[header] = round(col_confidence, 3)

    return ColumnDetectionResult(
        schema_key=None,
        schema_name=schema_name,
        source="llm",
        confidence=round(confidence, 3),
        mappings=mappings,
        categories=categories,
        unrecognized=unrecognized,
        column_confidences=column_confidences,
    )


def _unknown_result(headers: list) -> ColumnDetectionResult:
    """Fallback when all detection paths fail."""
    return ColumnDetectionResult(
        schema_key=None,
        schema_name="Unknown format",
        source="none",
        confidence=0.0,
        mappings={h: CAT_UNKNOWN for h in headers},
        categories={},
        unrecognized=list(headers),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_columns(headers: list, sample_rows: list = None,
                   project=None, agent_session=None) -> ColumnDetectionResult:
    """
    Detect what each column header represents.

    Detection pipeline (stops at the first successful match):
      1. DB-template match against DataSchemaTemplate records.
      2. Rule-based match against the hard-coded SCHEMA_REGISTRY.
      3. LLM fallback (Dify / Gemini) for unrecognised formats.
         On success with confidence >= 0.6 the result is saved as a learned
         DataSchemaTemplate so future uploads of the same format skip the LLM.
      4. Keyword-based auto-categorisation applied to any remaining 'unknown'
         columns in the final result.

    Args:
        headers:     list of column header strings from the uploaded file.
        sample_rows: optional list of row dicts sent to the LLM fallback.
        project:     optional Project instance used when saving learned templates.

    Returns:
        ColumnDetectionResult — mappings, categories, confidence, and per-column
        confidence scores.  Columns the AI could not classify receive the category
        inferred by auto_categorize_by_name(), or 'unknown' if inference also fails.
    """
    if not headers:
        return _unknown_result([])

    # 1. DB template match
    result = _try_db_template_match(headers)
    if result is not None:
        logger.info(
            "Column detection: DB template match schema='%s' confidence=%.2f",
            result.schema_name,
            result.confidence,
        )
        result = _apply_auto_categorization(result)
        return result

    # 2. Hard-coded rule match
    result = _try_rule_match(headers)
    if result is not None:
        logger.info(
            "Column detection: rule match schema=%s confidence=%.2f",
            result.schema_key,
            result.confidence,
        )
        result = _apply_auto_categorization(result)
        return result

    # 3. LLM fallback
    logger.info("Column detection: no rule match; falling back to LLM")
    result = _try_llm_fallback(headers, sample_rows=sample_rows, agent_session=agent_session)

    # Persist as a learned template if the LLM was confident enough
    if result.source == "llm" and result.confidence >= 0.6 and result.schema_name != "Unknown format":
        learned_cols = [
            {
                "canonical_name": canonical,
                "aliases": [original],
                "category": result.categories.get(canonical, CAT_UNKNOWN),
                "value_type": "string",
                "description": "",
            }
            for original, canonical in result.mappings.items()
            if canonical != CAT_UNKNOWN
        ]
        if learned_cols:
            save_learned_template(
                schema_name=result.schema_name,
                source_platform="custom",
                columns=learned_cols,
                project=project,
            )
            logger.info(
                "Column detection: saved learned template '%s' (%d columns)",
                result.schema_name,
                len(learned_cols),
            )

    # 4. Auto-categorise remaining unknowns
    result = _apply_auto_categorization(result)
    return result


def _apply_auto_categorization(result: ColumnDetectionResult) -> ColumnDetectionResult:
    """
    For any column mapped to 'unknown', attempt keyword-based category inference.

    Updates the result in-place and returns it.  Columns where inference also
    fails remain 'unknown' so the user can assign them manually.
    """
    updated_mappings = dict(result.mappings)
    updated_categories = dict(result.categories)
    still_unrecognized = []

    for original in result.unrecognized:
        canonical = updated_mappings.get(original, CAT_UNKNOWN)
        # Use the original header for inference if the canonical name is 'unknown'
        name_for_inference = original if canonical == CAT_UNKNOWN else canonical
        inferred = auto_categorize_by_name(name_for_inference)

        if inferred != CAT_UNKNOWN:
            # Keep the canonical name as 'unknown' (user must confirm the name),
            # but update the category so the UI can show a useful badge.
            updated_categories[original] = inferred
            logger.debug(
                "Auto-categorised '%s' as '%s' via keyword rules", original, inferred
            )
        else:
            still_unrecognized.append(original)

    result.categories = updated_categories
    result.unrecognized = still_unrecognized
    return result


def normalize_spreadsheet(data: dict, column_mapping: dict) -> dict:
    """
    Rename spreadsheet columns according to an approved column mapping.

    Args:
        data: spreadsheet_data dict with structure:
              {"name": ..., "sheets": [{"columns": [...], "rows": [...]}]}
        column_mapping: {original_header: canonical_name} — from ColumnDetectionResult,
                        possibly edited by the user. "unknown" values are left as-is.

    Returns:
        New data dict with columns/rows renamed according to the mapping.
        Columns mapped to "unknown" retain their original names.
    """
    if not column_mapping:
        return data

    normalized = {"name": data.get("name", ""), "sheets": []}

    for sheet in data.get("sheets", []):
        original_cols = sheet.get("columns", [])
        col_rename_map = {}
        renamed_cols = []

        for col in original_cols:
            canonical = column_mapping.get(col, col)
            # Columns labeled "unknown" keep their original name
            new_name = col if canonical == CAT_UNKNOWN else canonical
            col_rename_map[col] = new_name
            renamed_cols.append(new_name)

        renamed_rows = []
        for row in sheet.get("rows", []):
            new_row = {}
            for orig_key, value in row.items():
                new_key = col_rename_map.get(orig_key, orig_key)
                new_row[new_key] = value
            renamed_rows.append(new_row)

        normalized["sheets"].append({
            "name": sheet.get("name", ""),
            "columns": renamed_cols,
            "rows": renamed_rows,
        })

    return normalized
