"""Pure tests for the routing rule evaluator (CSM-S03-05). No database."""

import pytest

from csm.services.routing_engine import (
    FALLBACK_CHANNEL_DEFAULT,
    FALLBACK_NONE,
    FALLBACK_ORGANISATION_QUEUE,
    STATUS_DISABLED,
    STATUS_INVALID_ACTION,
    STATUS_MATCHED,
    STATUS_NOT_MATCHED,
    STATUS_NOT_REACHED,
    FallbackSpec,
    QueueRef,
    RoutingContext,
    RuleSpec,
    evaluate_condition,
    evaluate_rules,
)

BILLING = QueueRef(id=10, name='Billing')
TECH = QueueRef(id=11, name='Tech')
ARCHIVED = QueueRef(id=12, name='Archived', is_active=False)
NO_FALLBACK = FallbackSpec(has_channel=False)


def ctx(*messages, **kwargs):
    return RoutingContext(messages=tuple(messages), **kwargs)


def cond(field, operator, value=None):
    return {'field': field, 'operator': operator, 'value': value}


def rule(rule_id, *conditions, position=None, match_mode='all', enabled=True,
         queue=BILLING, tags=()):
    return RuleSpec(
        id=rule_id, name=f'Rule {rule_id}',
        position=rule_id if position is None else position,
        is_enabled=enabled, match_mode=match_mode, conditions=tuple(conditions),
        target_queue=queue, add_tags=tuple(tags),
    )


def run(rules, context, fallback=NO_FALLBACK):
    return evaluate_rules(rules, context, fallback, evaluated_at='2026-09-25T00:00:00+00:00')


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

class TestKeywordConditions:
    def test_contains_any_is_case_and_whitespace_insensitive(self):
        result = evaluate_condition(
            0, cond('latest_message', 'contains_any', ['Refund  Please']), ctx('i want a REFUND please'),
        )
        assert result.passed
        assert result.detail == 'Matched: Refund  Please'

    def test_latest_message_only_looks_at_last_message(self):
        result = evaluate_condition(
            0, cond('latest_message', 'contains_any', ['refund']), ctx('refund', 'thanks'),
        )
        assert not result.passed
        assert result.actual == 'thanks'

    def test_any_message_searches_whole_conversation(self):
        result = evaluate_condition(
            0, cond('any_message', 'contains_any', ['refund']), ctx('refund', 'thanks'),
        )
        assert result.passed

    def test_contains_all_reports_missing_keywords(self):
        result = evaluate_condition(
            0, cond('latest_message', 'contains_all', ['refund', 'invoice']), ctx('refund me'),
        )
        assert not result.passed
        assert result.detail == 'Missing: invoice'

    def test_not_contains_any(self):
        condition = cond('latest_message', 'not_contains_any', ['angry'])
        assert evaluate_condition(0, condition, ctx('hello')).passed
        assert not evaluate_condition(0, condition, ctx('I am ANGRY')).passed

    def test_actual_value_is_truncated(self):
        result = evaluate_condition(
            0, cond('latest_message', 'contains_any', ['zzz']), ctx('a' * 500),
        )
        assert len(result.actual) == 200
        assert result.actual.endswith('…')


class TestSubjectConditions:
    def test_equals_is_normalised(self):
        assert evaluate_condition(
            0, cond('subject', 'equals', 'Billing issue'), ctx('x', subject='  billing   ISSUE '),
        ).passed

    def test_is_empty(self):
        condition = cond('subject', 'is_empty')
        assert evaluate_condition(0, condition, ctx('x', subject='   ')).passed
        assert not evaluate_condition(0, condition, ctx('x', subject='Help')).passed

    def test_contains_any(self):
        assert evaluate_condition(
            0, cond('subject', 'contains_any', ['bill']), ctx('x', subject='Billing'),
        ).passed


class TestChannelAndOrganisationConditions:
    def test_support_channel_in_and_not_in(self):
        context = ctx('x', support_channel_id=5)
        assert evaluate_condition(0, cond('support_channel', 'in', [5, 6]), context).passed
        assert not evaluate_condition(0, cond('support_channel', 'not_in', [5]), context).passed

    def test_missing_channel_fails_in_and_passes_not_in(self):
        context = ctx('x')
        in_result = evaluate_condition(0, cond('support_channel', 'in', [5]), context)
        assert not in_result.passed
        assert in_result.detail == 'No channel selected'
        assert evaluate_condition(0, cond('support_channel', 'not_in', [5]), context).passed

    def test_channel_type(self):
        context = ctx('x', channel_type='email')
        assert evaluate_condition(0, cond('channel_type', 'in', ['email']), context).passed
        assert not evaluate_condition(0, cond('channel_type', 'in', ['live_chat']), context).passed

    def test_channel_status(self):
        offline = ctx('x', channel_online=False, channel_offline_reason='outside_hours')
        result = evaluate_condition(0, cond('channel_status', 'equals', 'offline'), offline)
        assert result.passed
        assert result.actual == 'offline'
        assert result.detail == 'Offline: outside_hours'
        assert not evaluate_condition(
            0, cond('channel_status', 'equals', 'online'), offline,
        ).passed

    def test_channel_status_without_channel_fails(self):
        result = evaluate_condition(0, cond('channel_status', 'equals', 'offline'), ctx('x'))
        assert not result.passed
        assert result.actual is None

    def test_customer_organisation(self):
        context = ctx('x', customer_organisation_id=3)
        assert evaluate_condition(0, cond('customer_organisation', 'in', [3]), context).passed
        assert evaluate_condition(0, cond('customer_organisation', 'not_in', [4]), context).passed


class TestMessageCountAndUnsupported:
    @pytest.mark.parametrize('operator,value,expected', [
        ('gte', 2, True), ('gte', 3, False),
        ('lte', 2, True), ('lte', 1, False),
        ('eq', 2, True), ('eq', 1, False),
    ])
    def test_message_count(self, operator, value, expected):
        result = evaluate_condition(0, cond('message_count', operator, value), ctx('a', 'b'))
        assert result.passed is expected
        assert result.actual == 2

    def test_unsupported_condition_fails_without_raising(self):
        result = evaluate_condition(0, cond('priority', 'equals', 'high'), ctx('x'))
        assert not result.passed
        assert 'Unsupported' in result.detail


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

class TestRuleEvaluation:
    def test_first_match_wins_and_later_rules_are_not_reached(self):
        rules = [
            rule(1, cond('latest_message', 'contains_any', ['refund']), queue=BILLING, tags=['money']),
            rule(2, queue=TECH),
        ]
        trace = run(rules, ctx('refund please'))
        assert [s.status for s in trace.steps] == [STATUS_MATCHED, STATUS_NOT_REACHED]
        assert trace.outcome.decided_by == 'rule'
        assert trace.outcome.queue_id == BILLING.id
        assert trace.outcome.tags == ['money']
        assert trace.fallback is None

    def test_rules_are_sorted_by_position(self):
        rules = [rule(1, position=5, queue=BILLING), rule(2, position=0, queue=TECH)]
        trace = run(rules, ctx('x'))
        assert trace.steps[0].rule_id == 2
        assert trace.outcome.queue_id == TECH.id

    def test_every_condition_is_evaluated_even_after_a_failure(self):
        rules = [rule(
            1,
            cond('latest_message', 'contains_any', ['nope']),
            cond('message_count', 'gte', 1),
        )]
        step = run(rules, ctx('x')).steps[0]
        assert step.status == STATUS_NOT_MATCHED
        assert [c.passed for c in step.conditions] == [False, True]

    def test_any_mode(self):
        rules = [rule(
            1,
            cond('latest_message', 'contains_any', ['nope']),
            cond('message_count', 'gte', 1),
            match_mode='any',
        )]
        assert run(rules, ctx('x')).steps[0].status == STATUS_MATCHED

    def test_empty_conditions_always_match(self):
        step = run([rule(1)], ctx('x')).steps[0]
        assert step.status == STATUS_MATCHED
        assert step.note == 'No conditions; always matches.'

    def test_disabled_rule_is_skipped_without_evaluating(self):
        rules = [rule(1, enabled=False, queue=BILLING), rule(2, queue=TECH)]
        trace = run(rules, ctx('x'))
        assert trace.steps[0].status == STATUS_DISABLED
        assert trace.steps[0].conditions == []
        assert trace.outcome.queue_id == TECH.id

    def test_inactive_or_missing_queue_falls_through_with_warning(self):
        rules = [rule(1, queue=ARCHIVED), rule(2, queue=None), rule(3, queue=TECH)]
        trace = run(rules, ctx('x'))
        assert [s.status for s in trace.steps] == [
            STATUS_INVALID_ACTION, STATUS_INVALID_ACTION, STATUS_MATCHED,
        ]
        assert len(trace.warnings) == 2
        assert trace.outcome.queue_id == TECH.id


class TestFallback:
    def test_channel_default_queue(self):
        fallback = FallbackSpec(has_channel=True, channel_default_queue=TECH)
        trace = run([], ctx('x'), fallback)
        assert trace.fallback.source == FALLBACK_CHANNEL_DEFAULT
        assert trace.outcome.decided_by == 'fallback'
        assert trace.outcome.queue_id == TECH.id

    def test_channel_without_default_queue(self):
        fallback = FallbackSpec(has_channel=True, organisation_queue=BILLING)
        trace = run([], ctx('x'), fallback)
        assert trace.fallback.source == FALLBACK_NONE
        assert trace.outcome.decided_by == 'none'
        assert trace.outcome.queue_id is None

    def test_organisation_queue_when_no_channel(self):
        fallback = FallbackSpec(has_channel=False, organisation_queue=BILLING)
        trace = run([rule(1, cond('message_count', 'gte', 5))], ctx('x'), fallback)
        assert trace.fallback.source == FALLBACK_ORGANISATION_QUEUE
        assert trace.outcome.queue_id == BILLING.id

    def test_nothing_available(self):
        trace = run([], ctx('x'))
        assert trace.fallback.source == FALLBACK_NONE
        assert trace.outcome.decided_by == 'none'

    def test_inactive_fallback_queue_warns(self):
        fallback = FallbackSpec(has_channel=True, channel_default_queue=ARCHIVED)
        trace = run([], ctx('x'), fallback)
        assert trace.outcome.queue_id == ARCHIVED.id
        assert trace.warnings == ["Fallback queue 'Archived' is inactive."]
