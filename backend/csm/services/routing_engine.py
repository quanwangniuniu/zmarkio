"""
Routing-rule evaluator.

Takes RoutingRule instances (with target_queue loaded) and a context dict,
and returns a JSON-ready trace of every rule and condition that was checked.
It makes no queries and writes nothing.

Context keys: messages (customer messages, oldest first), subject,
support_channel_id, channel_type, channel_online, channel_offline_reason,
customer_organisation_id.
"""

import re
from datetime import datetime

from csm.models import SupportChannel

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

CHANNEL_TYPES = tuple(SupportChannel.ChannelType.values)
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


def routing_context(messages, *, subject='', channel=None, availability=None,
                    customer_organisation_id=None):
    """The context dict for evaluate_rules; shared by the sandbox and live intake."""
    return {
        'messages': list(messages),
        'subject': subject or '',
        'support_channel_id': channel.id if channel else None,
        'channel_type': channel.channel_type if channel else None,
        'channel_online': availability['is_online'] if availability else None,
        'channel_offline_reason': availability['reason'] if availability else None,
        'customer_organisation_id': customer_organisation_id,
    }


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
    messages = ctx['messages']

    def result(actual, passed, detail=''):
        return {
            'index': index, 'field': field_name, 'operator': operator,
            'expected': expected, 'actual': actual, 'passed': passed, 'detail': detail,
        }

    if operator not in VOCABULARY.get(field_name, {}):
        return result(None, False, 'Unsupported condition; edit this rule to fix it.')

    if field_name == 'latest_message':
        text = messages[-1] if messages else ''
        passed, detail = _keyword_check(operator, expected, text)
        return result(_truncate(text), passed, detail)

    if field_name == 'any_message':
        text = '\n'.join(messages)
        passed, detail = _keyword_check(operator, expected, text)
        return result(_truncate(text), passed, detail)

    if field_name == 'subject':
        subject = ctx['subject'] or ''
        if operator == 'is_empty':
            return result(subject, not subject.strip())
        if operator == 'equals':
            return result(subject, normalize_text(subject) == normalize_text(expected))
        passed, detail = _keyword_check(operator, expected, subject)
        return result(_truncate(subject), passed, detail)

    if field_name == 'support_channel':
        passed, detail = _membership_check(
            operator, expected, ctx['support_channel_id'], 'No channel selected',
        )
        return result(ctx['support_channel_id'], passed, detail)

    if field_name == 'channel_type':
        passed, detail = _membership_check(
            operator, expected, ctx['channel_type'], 'No channel selected',
        )
        return result(ctx['channel_type'], passed, detail)

    if field_name == 'channel_status':
        online = ctx['channel_online']
        if online is None:
            return result(None, False, 'No channel selected')
        actual = 'online' if online else 'offline'
        detail = '' if online else f"Offline: {ctx['channel_offline_reason']}"
        return result(actual, actual == expected, detail)

    if field_name == 'customer_organisation':
        passed, detail = _membership_check(
            operator, expected, ctx['customer_organisation_id'], 'No customer organisation selected',
        )
        return result(ctx['customer_organisation_id'], passed, detail)

    # message_count
    count = len(messages)
    return result(count, _integer_check(operator, expected, count))


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------

def _rule_matches(rule, results):
    if not results:
        return True
    if rule.match_mode == 'any':
        return any(r['passed'] for r in results)
    return all(r['passed'] for r in results)


def _action_payload(rule):
    queue = rule.target_queue
    return {
        'type': 'route_to_queue',
        'queue_id': queue.id if queue else None,
        'queue_name': queue.name if queue else None,
        'add_tags': list(rule.add_tags),
    }


def _step(rule, status, *, conditions=(), action=None, note=''):
    return {
        'rule_id': rule.id, 'rule_name': rule.name, 'position': rule.position,
        'status': status, 'match_mode': rule.match_mode,
        'conditions': list(conditions), 'action': action, 'note': note,
    }


def _fallback(source, queue, reason):
    return {
        'source': source,
        'queue_id': queue.id if queue else None,
        'queue_name': queue.name if queue else None,
        'reason': reason,
    }


def _fallback_step(has_channel, queue):
    """Mirrors PortalConversationViewSet.create when no rule decides."""
    if has_channel:
        if queue is None:
            return _fallback(
                FALLBACK_NONE, None,
                'The channel has no default queue; live chat would reject this conversation.',
            )
        return _fallback(
            FALLBACK_CHANNEL_DEFAULT, queue,
            "No rule matched, so the channel's default queue is used.",
        )
    if queue is None:
        return _fallback(
            FALLBACK_NONE, None,
            'No channel and no queue for the customer organisation; the conversation would be unqueued.',
        )
    return _fallback(
        FALLBACK_ORGANISATION_QUEUE, queue,
        "No rule matched and no channel was given, so the customer organisation's first queue is used.",
    )


def evaluate_rules(rules, ctx, fallback_queue, *, evaluated_at):
    """
    Walk `rules` in position order and return the trace as a dict.

    `fallback_queue` is the channel's default queue when ctx has a channel,
    otherwise the customer organisation's first queue (either may be None).

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
            steps.append(_step(
                rule, STATUS_NOT_REACHED, note=f"Not evaluated; '{winner.name}' already matched.",
            ))
            continue

        if not rule.is_enabled:
            steps.append(_step(rule, STATUS_DISABLED, note='Rule is disabled.'))
            continue

        results = [
            evaluate_condition(index, condition, ctx)
            for index, condition in enumerate(rule.conditions)
        ]
        if not _rule_matches(rule, results):
            steps.append(_step(rule, STATUS_NOT_MATCHED, conditions=results))
            continue

        queue = rule.target_queue
        if not rule.can_route:
            reason = 'has no target queue' if queue is None else f"targets inactive queue '{queue.name}'"
            warnings.append(f"Rule '{rule.name}' matched but {reason}; it was skipped.")
            steps.append(_step(
                rule, STATUS_INVALID_ACTION, conditions=results, action=_action_payload(rule),
                note=f'Matched, but the rule {reason}.',
            ))
            continue

        winner = rule
        steps.append(_step(
            rule, STATUS_MATCHED, conditions=results, action=_action_payload(rule),
            note='' if results else 'No conditions; always matches.',
        ))

    if winner is not None:
        fallback_step = None
        outcome = {
            'decided_by': 'rule', 'rule_id': winner.id, 'rule_name': winner.name,
            'queue_id': winner.target_queue.id, 'queue_name': winner.target_queue.name,
            'tags': list(winner.add_tags),
        }
    else:
        fallback_step = _fallback_step(ctx['support_channel_id'] is not None, fallback_queue)
        outcome = {
            'decided_by': 'none' if fallback_step['source'] == FALLBACK_NONE else 'fallback',
            'rule_id': None, 'rule_name': None,
            'queue_id': fallback_step['queue_id'], 'queue_name': fallback_step['queue_name'],
            'tags': [],
        }
        if fallback_queue is not None and not fallback_queue.is_active:
            warnings.append(f"Fallback queue '{fallback_queue.name}' is inactive.")

    if isinstance(evaluated_at, datetime):
        evaluated_at = evaluated_at.isoformat()

    return {
        'evaluated_at': evaluated_at,
        'message_count': len(ctx['messages']),
        'steps': steps,
        'fallback': fallback_step,
        'outcome': outcome,
        'warnings': warnings,
    }
