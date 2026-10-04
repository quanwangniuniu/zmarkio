"""Routing rule CRUD, validation and live-intake routing."""

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.models import Project
from csm.models import RoutingRule, SupportChannel, SupportChannelExperienceGroup
from customer.models import CustomerOrganisation
from experience_group.models import ExperienceGroup
from csm.services.routing_engine import (
    CHANNEL_STATUSES,
    CHANNEL_TYPES,
    FIELD_LABELS,
    OPERATOR_LABELS,
    VALUE_CHANNEL_IDS,
    VALUE_CHANNEL_STATUS,
    VALUE_CHANNEL_TYPES,
    VALUE_INTEGER,
    VALUE_KEYWORDS,
    VALUE_NONE,
    VALUE_ORGANISATION_IDS,
    VALUE_TEXT,
    VOCABULARY,
    evaluate_rules,
    routing_context,
)

logger = logging.getLogger(__name__)

MAX_CONDITIONS = 10
MAX_KEYWORDS = 20
MAX_KEYWORD_CHARS = 100
MAX_TEXT_CHARS = 200
MAX_TAGS = 10
MAX_TAG_CHARS = 50
MAX_MESSAGE_COUNT = 1000


def vocabulary_payload():
    """Field/operator table for the rule builder UI."""
    return {
        'fields': [
            {
                'field': field_name,
                'label': FIELD_LABELS[field_name],
                'operators': [
                    {'operator': op, 'label': OPERATOR_LABELS[op], 'value_kind': kind}
                    for op, kind in operators.items()
                ],
            }
            for field_name, operators in VOCABULARY.items()
        ],
        'channel_types': list(CHANNEL_TYPES),
        'channel_statuses': list(CHANNEL_STATUSES),
        'limits': {
            'conditions': MAX_CONDITIONS,
            'keywords': MAX_KEYWORDS,
            'tags': MAX_TAGS,
        },
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _clean_string_list(value, *, max_items, max_chars, label):
    if not isinstance(value, list) or not value:
        raise ValueError(f'Provide at least one {label}.')
    if len(value) > max_items:
        raise ValueError(f'At most {max_items} {label}s are allowed.')
    cleaned = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f'Each {label} must be non-empty text.')
        item = item.strip()
        if len(item) > max_chars:
            raise ValueError(f'Each {label} must be {max_chars} characters or fewer.')
        if item.casefold() not in {c.casefold() for c in cleaned}:
            cleaned.append(item)
    return cleaned


def _clean_id_list(value, *, allowed_ids, label):
    if not isinstance(value, list) or not value:
        raise ValueError(f'Select at least one {label}.')
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        raise ValueError(f'{label.capitalize()} ids must be integers.')
    unknown = sorted(set(value) - set(allowed_ids))
    if unknown:
        raise ValueError(f'Unknown {label} ids for this workspace: {unknown}')
    return sorted(set(value))


def _clean_value(kind, value, *, channel_ids, organisation_ids):
    if kind == VALUE_KEYWORDS:
        return _clean_string_list(
            value, max_items=MAX_KEYWORDS, max_chars=MAX_KEYWORD_CHARS, label='keyword',
        )
    if kind == VALUE_TEXT:
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Enter a value.')
        if len(value.strip()) > MAX_TEXT_CHARS:
            raise ValueError(f'Value must be {MAX_TEXT_CHARS} characters or fewer.')
        return value.strip()
    if kind == VALUE_NONE:
        return None
    if kind == VALUE_CHANNEL_IDS:
        return _clean_id_list(value, allowed_ids=channel_ids, label='channel')
    if kind == VALUE_ORGANISATION_IDS:
        return _clean_id_list(value, allowed_ids=organisation_ids, label='organisation')
    if kind == VALUE_CHANNEL_TYPES:
        if not isinstance(value, list) or not value or any(v not in CHANNEL_TYPES for v in value):
            raise ValueError(f'Choose channel types from: {", ".join(CHANNEL_TYPES)}.')
        return sorted(set(value))
    if kind == VALUE_CHANNEL_STATUS:
        if value not in CHANNEL_STATUSES:
            raise ValueError(f'Choose one of: {", ".join(CHANNEL_STATUSES)}.')
        return value
    if kind == VALUE_INTEGER:
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_MESSAGE_COUNT:
            raise ValueError(f'Enter a whole number between 0 and {MAX_MESSAGE_COUNT}.')
        return value
    raise ValueError('Unsupported value.')


def _project_channel_ids(project_id):
    return set(SupportChannel.objects.filter(project_id=project_id).values_list('id', flat=True))


def project_organization_id(project_id):
    """
    The workspace Organization of `project_id`. Project ids repeat across
    organisation schemas; under the request's search_path this resolves the
    caller's own project, so it pins a public row to the right organisation.
    Call it only inside a request (or an explicit tenant schema context).
    """
    organization_id = Project.objects.values_list('organization_id', flat=True).get(pk=project_id)
    if organization_id is None:
        raise ValidationError({'project': 'This project does not belong to an organisation.'})
    return organization_id


def _project_organisation_ids(project_id):
    return set(
        CustomerOrganisation.objects.filter(
            organization_id=project_organization_id(project_id),
        ).values_list('id', flat=True),
    )


def validate_conditions(project_id, conditions):
    """
    Validate and normalise a rule's condition list (a list of dicts; the
    serializer checks that shape).

    Raises ValidationError keyed by 'conditions'; per-row messages are prefixed
    with the 1-based condition number so the rule builder can point at the row.
    """
    if len(conditions) > MAX_CONDITIONS:
        raise ValidationError({'conditions': f'At most {MAX_CONDITIONS} conditions are allowed.'})

    channel_ids = None
    organisation_ids = None
    cleaned = []
    errors = []
    for index, condition in enumerate(conditions):
        field_name = condition.get('field')
        operator = condition.get('operator')
        if field_name not in VOCABULARY:
            errors.append(f"Condition {index + 1}: unknown field '{field_name}'.")
            continue
        kind = VOCABULARY[field_name].get(operator)
        if kind is None:
            errors.append(
                f"Condition {index + 1}: operator '{operator}' is not valid for {FIELD_LABELS[field_name]}."
            )
            continue
        if kind == VALUE_CHANNEL_IDS and channel_ids is None:
            channel_ids = _project_channel_ids(project_id)
        if kind == VALUE_ORGANISATION_IDS and organisation_ids is None:
            organisation_ids = _project_organisation_ids(project_id)
        try:
            value = _clean_value(
                kind, condition.get('value'),
                channel_ids=channel_ids or set(),
                organisation_ids=organisation_ids or set(),
            )
        except ValueError as exc:
            errors.append(f'Condition {index + 1}: {exc}')
            continue
        cleaned.append({'field': field_name, 'operator': operator, 'value': value})

    if errors:
        raise ValidationError({'conditions': errors})
    return cleaned


def _validate_name(name):
    name = (name or '').strip()
    if not name:
        raise ValidationError({'name': 'Name is required.'})
    if len(name) > 200:
        raise ValidationError({'name': 'Name must be 200 characters or fewer.'})
    return name


def _assert_unique_name(organization_id, experience_group_id, name, exclude_id=None):
    qs = RoutingRule.objects.filter(
        organization_id=organization_id, experience_group_id=experience_group_id, name__iexact=name,
    )
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    if qs.exists():
        raise ValidationError({'name': 'A rule with this name already exists for this experience group.'})


def _validate_target_queue(organization_id, project_id, queue):
    # project_id alone is ambiguous across organisations; the queue's customer
    # organisation pins it to one.
    queue_organization_id = queue.organisation.organization_id if queue.organisation_id else None
    if queue.project_id != project_id or queue_organization_id != organization_id or not queue.is_active:
        raise ValidationError({
            'target_queue': 'Queue must belong to this workspace and be active.',
        })
    return queue


def _validate_tags(tags):
    if tags in (None, []):
        return []
    try:
        return _clean_string_list(tags, max_items=MAX_TAGS, max_chars=MAX_TAG_CHARS, label='tag')
    except ValueError as exc:
        raise ValidationError({'add_tags': str(exc)})


def _validate_experience_group(project_id, experience_group):
    if experience_group is None or experience_group.project_id != project_id:
        raise ValidationError({
            'experience_group': 'Experience group must belong to this workspace.',
        })
    return experience_group


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def list_rules(project_id, experience_group_id=None):
    qs = RoutingRule.objects.filter(
        organization_id=project_organization_id(project_id), project_id=project_id,
    ).select_related('target_queue')
    if experience_group_id is not None:
        qs = qs.filter(experience_group_id=experience_group_id)
    return qs.order_by('experience_group_id', 'position', 'id')


def _lock_experience_group(experience_group_id):
    # Serialises everything that assigns positions in a group (create and
    # reorder), so they can't interleave and hand out the same position.
    # NO KEY: still conflicts with itself, but not with FK checks from other
    # tables that reference the group.
    ExperienceGroup.objects.select_for_update(no_key=True).filter(pk=experience_group_id).first()


def _next_position(organization_id, experience_group_id):
    current = RoutingRule.objects.filter(
        organization_id=organization_id, experience_group_id=experience_group_id,
    ).aggregate(m=Max('position'))['m']
    return 0 if current is None else current + 1


@transaction.atomic
def create_rule(project_id, *, user, experience_group, name, target_queue,
                conditions=None, match_mode=RoutingRule.MatchMode.ALL,
                is_enabled=True, add_tags=None):
    organization_id = project_organization_id(project_id)
    _validate_experience_group(project_id, experience_group)
    name = _validate_name(name)
    _assert_unique_name(organization_id, experience_group.id, name)
    _validate_target_queue(organization_id, project_id, target_queue)
    conditions = validate_conditions(project_id, conditions or [])
    add_tags = _validate_tags(add_tags)

    _lock_experience_group(experience_group.id)
    return RoutingRule.objects.create(
        organization_id=organization_id,
        project_id=project_id,
        experience_group=experience_group,
        name=name,
        position=_next_position(organization_id, experience_group.id),
        is_enabled=is_enabled,
        match_mode=match_mode,
        conditions=conditions,
        target_queue=target_queue,
        add_tags=add_tags,
        created_by=user,
    )


@transaction.atomic
def update_rule(rule, *, name=None, target_queue=None, conditions=None,
                match_mode=None, is_enabled=None, add_tags=None):
    changed = []
    if name is not None:
        name = _validate_name(name)
        _assert_unique_name(rule.organization_id, rule.experience_group_id, name, exclude_id=rule.pk)
        rule.name = name
        changed.append('name')
    if target_queue is not None:
        rule.target_queue = _validate_target_queue(rule.organization_id, rule.project_id, target_queue)
        changed.append('target_queue')
    if conditions is not None:
        rule.conditions = validate_conditions(rule.project_id, conditions)
        changed.append('conditions')
    if match_mode is not None:
        rule.match_mode = match_mode
        changed.append('match_mode')
    if is_enabled is not None:
        rule.is_enabled = is_enabled
        changed.append('is_enabled')
    if add_tags is not None:
        rule.add_tags = _validate_tags(add_tags)
        changed.append('add_tags')
    # Only the edited fields: `rule` may hold a position that a concurrent
    # reorder has since changed, and only create/reorder may write position.
    rule.save(update_fields=[*changed, 'updated_at'])
    return rule


@transaction.atomic
def reorder_rules(project_id, experience_group_id, ordered_ids):
    """Set positions from `ordered_ids`, which must list every rule in the group once."""
    _lock_experience_group(experience_group_id)
    existing = list(
        RoutingRule.objects.select_for_update().filter(
            organization_id=project_organization_id(project_id),
            project_id=project_id, experience_group_id=experience_group_id,
        ),
    )
    existing_ids = {rule.pk for rule in existing}
    if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != existing_ids:
        raise ValidationError({
            'ids': 'Provide every rule id for this experience group exactly once.',
        })

    by_id = {rule.pk: rule for rule in existing}
    for index, pk in enumerate(ordered_ids):
        by_id[pk].position = index
    RoutingRule.objects.bulk_update(by_id.values(), ['position', 'updated_at'])
    return list_rules(project_id, experience_group_id)


# ---------------------------------------------------------------------------
# Live intake
# ---------------------------------------------------------------------------

def route_new_conversation(customer, *, support_channel, availability, message, subject,
                           fallback_queue):
    """
    Queue for a conversation the customer is starting, from their experience
    group's rules (the same engine and fallback as the sandbox). With no group
    or no match it is fallback_queue. The rule's tags are not applied.

    Scoped by the customer organisation's workspace Organization (a public,
    unambiguous id), so it does not depend on the request's schema.
    """
    organisation = customer.organisation
    if customer.experience_group_id is None or organisation is None or organisation.organization_id is None:
        return fallback_queue
    # As in the sandbox, only the group's own channels carry its rules.
    if support_channel is not None and not SupportChannelExperienceGroup.objects.filter(
        channel=support_channel, experience_group_id=customer.experience_group_id,
    ).exists():
        return fallback_queue

    rules = list(
        RoutingRule.objects.filter(
            organization_id=organisation.organization_id,
            experience_group_id=customer.experience_group_id,
        ).select_related('target_queue'),
    )
    ctx = routing_context(
        [message], subject=subject, channel=support_channel, availability=availability,
        customer_organisation_id=organisation.id,
    )
    trace = evaluate_rules(rules, ctx, fallback_queue, evaluated_at=timezone.now())
    outcome = trace['outcome']
    if outcome['decided_by'] != 'rule':
        return fallback_queue
    winner = next(rule for rule in rules if rule.id == outcome['rule_id'])
    logger.info(
        'Routing rule %s (%r) routed a new conversation for customer %s to queue %s',
        winner.id, winner.name, customer.id, winner.target_queue_id,
    )
    return winner.target_queue
