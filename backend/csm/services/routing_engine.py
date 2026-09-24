"""
Pure routing-rule evaluator for CSM-S03-05.

No ORM access here: callers build RuleSpec / RoutingContext / FallbackSpec
from the database and get back a RoutingTrace describing every rule and
condition that was checked. Keeping this pure means the sandbox cannot
create records or broadcast anything, and live intake can reuse it later.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Vocabulary (shared by validation, the vocabulary endpoint and evaluation)
# ---------------------------------------------------------------------------

KEYWORD_OPERATORS = ('contains_any', 'contains_all', 'not_contains_any')

VALUE_KEYWORDS = 'keywords'
VALUE_TEXT = 'text'
VALUE_NONE = 'none'
VALUE_CHANNEL_IDS = 'channel_ids'
VALUE_CHANNEL_TYPES = 'channel_types'
VALUE_CHANNEL_STATUS = 'channel_status'
VALUE_ORGANISATION_IDS = 'organisation_ids'
VALUE_INTEGER = 'integer'

CHANNEL_TYPES = ('live_chat', 'contact_form', 'email')
CHANNEL_STATUSES = ('online', 'offline')

# field -> {operator: value kind}
VOCABULARY = {
    'latest_message': {op: VALUE_KEYWORDS for op in KEYWORD_OPERATORS},
    'any_message': {op: VALUE_KEYWORDS for op in KEYWORD_OPERATORS},
    'subject': {
        'contains_any': VALUE_KEYWORDS,
        'equals': VALUE_TEXT,
        'is_empty': VALUE_NONE,
    },
    'support_channel': {'in': VALUE_CHANNEL_IDS, 'not_in': VALUE_CHANNEL_IDS},
    'channel_type': {'in': VALUE_CHANNEL_TYPES},
    'channel_status': {'equals': VALUE_CHANNEL_STATUS},
    'customer_organisation': {
        'in': VALUE_ORGANISATION_IDS,
        'not_in': VALUE_ORGANISATION_IDS,
    },
    'message_count': {'gte': VALUE_INTEGER, 'lte': VALUE_INTEGER, 'eq': VALUE_INTEGER},
}

FIELD_LABELS = {
    'latest_message': 'Latest customer message',
    'any_message': 'Any customer message',
    'subject': 'Conversation subject',
    'support_channel': 'Support channel',
    'channel_type': 'Channel type',
    'channel_status': 'Channel status',
    'customer_organisation': 'Customer organisation',
    'message_count': 'Customer message count',
}

OPERATOR_LABELS = {
    'contains_any': 'contains any of',
    'contains_all': 'contains all of',
    'not_contains_any': 'contains none of',
    'equals': 'equals',
    'is_empty': 'is empty',
    'in': 'is one of',
    'not_in': 'is not one of',
    'gte': 'is at least',
    'lte': 'is at most',
    'eq': 'is exactly',
}

ACTUAL_VALUE_MAX_CHARS = 200

# Rule step statuses
STATUS_MATCHED = 'matched'
STATUS_NOT_MATCHED = 'not_matched'
STATUS_DISABLED = 'disabled'
STATUS_INVALID_ACTION = 'invalid_action'
STATUS_NOT_REACHED = 'not_reached'

# Fallback sources (mirror portal/views.py PortalConversationViewSet.create)
FALLBACK_CHANNEL_DEFAULT = 'channel_default_queue'
FALLBACK_ORGANISATION_QUEUE = 'first_organisation_queue'
FALLBACK_NONE = 'none'


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QueueRef:
    id: int
    name: str
    is_active: bool = True


@dataclass(frozen=True)
class RuleSpec:
    id: int
    name: str
    position: int
    is_enabled: bool
    match_mode: str
    conditions: tuple
    target_queue: Optional[QueueRef]
    add_tags: tuple = ()


@dataclass(frozen=True)
class RoutingContext:
    messages: tuple  # customer messages, oldest first
    subject: str = ''
    support_channel_id: Optional[int] = None
    channel_type: Optional[str] = None
    channel_online: Optional[bool] = None
    channel_offline_reason: Optional[str] = None
    customer_organisation_id: Optional[int] = None


@dataclass(frozen=True)
class FallbackSpec:
    has_channel: bool
    channel_default_queue: Optional[QueueRef] = None
    organisation_queue: Optional[QueueRef] = None


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

@dataclass
class ConditionResult:
    index: int
    field: str
    operator: str
    expected: Any
    actual: Any
    passed: bool
    detail: str = ''


@dataclass
class RuleStep:
    rule_id: int
    rule_name: str
    position: int
    status: str
    match_mode: str
    conditions: list = field(default_factory=list)
    action: Optional[dict] = None
    note: str = ''


@dataclass
class FallbackStep:
    source: str
    queue_id: Optional[int]
    queue_name: Optional[str]
    reason: str


@dataclass
class RoutingOutcome:
    decided_by: str  # 'rule' | 'fallback' | 'none'
    rule_id: Optional[int]
    rule_name: Optional[str]
    queue_id: Optional[int]
    queue_name: Optional[str]
    tags: list = field(default_factory=list)


@dataclass
class RoutingTrace:
    evaluated_at: str
    message_count: int
    steps: list
    fallback: Optional[FallbackStep]
    outcome: RoutingOutcome
    warnings: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

_WHITESPACE = re.compile(r'\s+')


def normalize_text(text):
    return _WHITESPACE.sub(' ', (text or '').casefold()).strip()


def _truncate(text):
    if text is None or len(text) <= ACTUAL_VALUE_MAX_CHARS:
        return text
    return text[:ACTUAL_VALUE_MAX_CHARS - 1] + '…'


def _keyword_check(operator, keywords, haystack):
    normalized = normalize_text(haystack)
    hits = [kw for kw in keywords if normalize_text(kw) and normalize_text(kw) in normalized]
    if operator == 'contains_any':
        passed = bool(hits)
    elif operator == 'contains_all':
        passed = len(hits) == len(keywords)
    else:  # not_contains_any
        passed = not hits
    if hits:
        detail = 'Matched: ' + ', '.join(hits)
    else:
        detail = 'No keywords matched'
    if operator == 'contains_all' and not passed:
        missing = [kw for kw in keywords if kw not in hits]
        detail = 'Missing: ' + ', '.join(missing)
    return passed, detail


def _membership_check(operator, expected, actual, missing_label):
    if actual is None:
        passed = operator == 'not_in'
        return passed, missing_label
    inside = actual in expected
    passed = inside if operator == 'in' else not inside
    return passed, ''


def _integer_check(operator, expected, actual):
    if operator == 'gte':
        return actual >= expected
    if operator == 'lte':
        return actual <= expected
    return actual == expected


def evaluate_condition(index, condition, ctx):
    """Evaluate one {field, operator, value} condition against ctx."""
    field_name = condition.get('field')
    operator = condition.get('operator')
    expected = condition.get('value')

    def result(actual, passed, detail=''):
        return ConditionResult(
            index=index, field=field_name, operator=operator,
            expected=expected, actual=actual, passed=passed, detail=detail,
        )

    if operator not in VOCABULARY.get(field_name, {}):
        return result(None, False, 'Unsupported condition; edit this rule to fix it.')

    if field_name == 'latest_message':
        text = ctx.messages[-1] if ctx.messages else ''
        passed, detail = _keyword_check(operator, expected, text)
        return result(_truncate(text), passed, detail)

    if field_name == 'any_message':
        text = '\n'.join(ctx.messages)
        passed, detail = _keyword_check(operator, expected, text)
        return result(_truncate(text), passed, detail)

    if field_name == 'subject':
        subject = ctx.subject or ''
        if operator == 'is_empty':
            return result(subject, not subject.strip())
        if operator == 'equals':
            return result(subject, normalize_text(subject) == normalize_text(expected))
        passed, detail = _keyword_check(operator, expected, subject)
        return result(_truncate(subject), passed, detail)

    if field_name == 'support_channel':
        passed, detail = _membership_check(
            operator, expected, ctx.support_channel_id, 'No channel selected',
        )
        return result(ctx.support_channel_id, passed, detail)

    if field_name == 'channel_type':
        passed, detail = _membership_check(
            operator, expected, ctx.channel_type, 'No channel selected',
        )
        return result(ctx.channel_type, passed, detail)

    if field_name == 'channel_status':
        if ctx.channel_online is None:
            return result(None, False, 'No channel selected')
        actual = 'online' if ctx.channel_online else 'offline'
        detail = '' if ctx.channel_online else f'Offline: {ctx.channel_offline_reason}'
        return result(actual, actual == expected, detail)

    if field_name == 'customer_organisation':
        passed, detail = _membership_check(
            operator, expected, ctx.customer_organisation_id, 'No customer organisation selected',
        )
        return result(ctx.customer_organisation_id, passed, detail)

    # message_count
    count = len(ctx.messages)
    return result(count, _integer_check(operator, expected, count))


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------

def _rule_matches(rule, results):
    if not results:
        return True
    if rule.match_mode == 'any':
        return any(r.passed for r in results)
    return all(r.passed for r in results)


def _action_payload(rule):
    queue = rule.target_queue
    return {
        'type': 'route_to_queue',
        'queue_id': queue.id if queue else None,
        'queue_name': queue.name if queue else None,
        'add_tags': list(rule.add_tags),
    }


def _fallback_step(fallback):
    if fallback.has_channel:
        queue = fallback.channel_default_queue
        if queue is None:
            return FallbackStep(
                FALLBACK_NONE, None, None,
                'The channel has no default queue; live chat would reject this conversation.',
            )
        return FallbackStep(
            FALLBACK_CHANNEL_DEFAULT, queue.id, queue.name,
            "No rule matched, so the channel's default queue is used.",
        )
    queue = fallback.organisation_queue
    if queue is None:
        return FallbackStep(
            FALLBACK_NONE, None, None,
            'No channel and no queue for the customer organisation; the conversation would be unqueued.',
        )
    return FallbackStep(
        FALLBACK_ORGANISATION_QUEUE, queue.id, queue.name,
        "No rule matched and no channel was given, so the customer organisation's first queue is used.",
    )


def evaluate_rules(rules, ctx, fallback, *, evaluated_at):
    """
    Walk `rules` in position order and return a RoutingTrace.

    Every condition of an enabled rule is evaluated (no short-circuit) so the
    trace shows each result. The first matching rule with a usable queue wins;
    a match whose queue is missing or inactive is recorded as invalid_action
    and evaluation continues. With no winner, the live fallback applies.
    """
    ordered = sorted(rules, key=lambda r: (r.position, r.id))
    steps = []
    warnings = []
    winner = None

    for rule in ordered:
        if winner is not None:
            steps.append(RuleStep(
                rule.id, rule.name, rule.position, STATUS_NOT_REACHED, rule.match_mode,
                note=f"Not evaluated; '{winner.name}' already matched.",
            ))
            continue

        if not rule.is_enabled:
            steps.append(RuleStep(
                rule.id, rule.name, rule.position, STATUS_DISABLED, rule.match_mode,
                note='Rule is disabled.',
            ))
            continue

        results = [
            evaluate_condition(index, condition, ctx)
            for index, condition in enumerate(rule.conditions)
        ]
        if not _rule_matches(rule, results):
            steps.append(RuleStep(
                rule.id, rule.name, rule.position, STATUS_NOT_MATCHED, rule.match_mode,
                conditions=results,
            ))
            continue

        queue = rule.target_queue
        if queue is None or not queue.is_active:
            reason = 'has no target queue' if queue is None else f"targets inactive queue '{queue.name}'"
            warnings.append(f"Rule '{rule.name}' matched but {reason}; it was skipped.")
            steps.append(RuleStep(
                rule.id, rule.name, rule.position, STATUS_INVALID_ACTION, rule.match_mode,
                conditions=results, action=_action_payload(rule),
                note=f'Matched, but the rule {reason}.',
            ))
            continue

        winner = rule
        steps.append(RuleStep(
            rule.id, rule.name, rule.position, STATUS_MATCHED, rule.match_mode,
            conditions=results, action=_action_payload(rule),
            note='' if results else 'No conditions; always matches.',
        ))

    if winner is not None:
        fallback_step = None
        outcome = RoutingOutcome(
            'rule', winner.id, winner.name,
            winner.target_queue.id, winner.target_queue.name, list(winner.add_tags),
        )
    else:
        fallback_step = _fallback_step(fallback)
        decided_by = 'none' if fallback_step.source == FALLBACK_NONE else 'fallback'
        outcome = RoutingOutcome(
            decided_by, None, None, fallback_step.queue_id, fallback_step.queue_name,
        )
        fallback_queue = (
            fallback.channel_default_queue if fallback.has_channel else fallback.organisation_queue
        )
        if fallback_queue is not None and not fallback_queue.is_active:
            warnings.append(f"Fallback queue '{fallback_queue.name}' is inactive.")

    if isinstance(evaluated_at, datetime):
        evaluated_at = evaluated_at.isoformat()

    return RoutingTrace(
        evaluated_at=evaluated_at,
        message_count=len(ctx.messages),
        steps=steps,
        fallback=fallback_step,
        outcome=outcome,
        warnings=warnings,
    )
