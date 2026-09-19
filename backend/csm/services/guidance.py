"""Guidance entry configuration and workspace lookup for CSM-S03-02."""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Prefetch

from csm.models import GuidanceEntry, GuidanceEntryExperienceGroup
from experience_group.models import ExperienceGroup

logger = logging.getLogger(__name__)

TRIGGER_DESCRIPTION_MAX_LENGTH = 2000
RECOMMENDED_RESPONSE_MAX_LENGTH = 10000


def guidance_group_name(experience_group_id):
    """Channel-layer group that agents viewing this EG's conversations join."""
    return f'csm_guidance_eg_{experience_group_id}'


def guidance_queryset():
    return GuidanceEntry.objects.prefetch_related(
        Prefetch(
            'experience_group_links',
            queryset=GuidanceEntryExperienceGroup.objects.select_related(
                'experience_group',
            ).order_by('experience_group__name', 'experience_group_id'),
        ),
    )


def _clean_text(field, value, max_length):
    value = (value or '').strip()
    if not value:
        raise ValidationError({field: 'This field is required.'})
    if len(value) > max_length:
        raise ValidationError({field: f'Must be {max_length} characters or fewer.'})
    return value


def _validate_guidance_type(guidance_type):
    if guidance_type not in GuidanceEntry.GuidanceType.values:
        raise ValidationError({'guidance_type': f'Unknown guidance type: {guidance_type}'})


def _lock_experience_groups(project_id, eg_ids):
    """
    Validate that every id is an EG in this project and lock those rows so
    concurrent appends to the same group cannot pick the same display_order.
    """
    unique_ids = list(dict.fromkeys(eg_ids or []))
    if not unique_ids:
        raise ValidationError({
            'experience_group_ids': 'Select at least one experience group.',
        })

    groups = list(
        ExperienceGroup.objects.select_for_update()
        .filter(pk__in=unique_ids)
        .order_by('pk')
        .only('id', 'project_id'),
    )
    found_ids = {group.id for group in groups}
    missing = [eg_id for eg_id in unique_ids if eg_id not in found_ids]
    if missing:
        raise ValidationError({
            'experience_group_ids': f'Unknown experience group id(s): {missing}',
        })
    for group in groups:
        if group.project_id is None or group.project_id != project_id:
            raise ValidationError({
                'experience_group_ids': f'Experience group {group.id} is not in this project.',
            })
    return unique_ids


def _next_display_order(experience_group_id):
    current = GuidanceEntryExperienceGroup.objects.filter(
        experience_group_id=experience_group_id,
    ).aggregate(m=Max('display_order'))['m']
    return 0 if current is None else current + 1


def _sync_experience_groups(entry, eg_ids):
    """
    Diff the entry's group links: retained groups keep their position,
    new groups are appended to the end of that group's list.
    Returns the union of old and new group ids (all groups whose panel changed).
    """
    eg_ids = _lock_experience_groups(entry.project_id, eg_ids)
    existing = set(entry.experience_group_links.values_list('experience_group_id', flat=True))
    wanted = set(eg_ids)

    removed = existing - wanted
    if removed:
        entry.experience_group_links.filter(experience_group_id__in=removed).delete()

    GuidanceEntryExperienceGroup.objects.bulk_create([
        GuidanceEntryExperienceGroup(
            entry=entry,
            experience_group_id=eg_id,
            display_order=_next_display_order(eg_id),
        )
        for eg_id in eg_ids
        if eg_id not in existing
    ])
    return existing | wanted


def broadcast_guidance_updated(experience_group_ids):
    """Tell open agent workspaces for these EGs to refetch, once the write commits."""
    eg_ids = sorted({int(eg_id) for eg_id in experience_group_ids if eg_id is not None})
    if not eg_ids:
        return

    def _send():
        try:
            channel_layer = get_channel_layer()
            if channel_layer is None:
                return
            for eg_id in eg_ids:
                async_to_sync(channel_layer.group_send)(
                    guidance_group_name(eg_id),
                    {'type': 'guidance.updated', 'experience_group_ids': eg_ids},
                )
        except Exception:
            logger.exception('[CSM guidance] broadcast failed for EGs %s', eg_ids)

    transaction.on_commit(_send)


def list_guidance(project_id, *, experience_group_id=None):
    """
    All entries for a project, or one EG's entries in display order.
    """
    if experience_group_id is None:
        return list(guidance_queryset().filter(project_id=project_id))

    if not ExperienceGroup.objects.filter(
        pk=experience_group_id, project_id=project_id,
    ).exists():
        raise ValidationError({
            'experience_group': 'Experience group is not in this project.',
        })

    ordered_ids = list(
        GuidanceEntryExperienceGroup.objects.filter(
            experience_group_id=experience_group_id,
            entry__project_id=project_id,
        ).order_by('display_order', 'id').values_list('entry_id', flat=True),
    )
    by_id = {entry.pk: entry for entry in guidance_queryset().filter(pk__in=ordered_ids)}
    return [by_id[pk] for pk in ordered_ids if pk in by_id]


@transaction.atomic
def create_guidance(
    project_id, *, user, guidance_type, trigger_description,
    recommended_response, experience_group_ids,
):
    _validate_guidance_type(guidance_type)
    entry = GuidanceEntry(
        project_id=project_id,
        guidance_type=guidance_type,
        trigger_description=_clean_text(
            'trigger_description', trigger_description, TRIGGER_DESCRIPTION_MAX_LENGTH,
        ),
        recommended_response=_clean_text(
            'recommended_response', recommended_response, RECOMMENDED_RESPONSE_MAX_LENGTH,
        ),
        created_by=user if getattr(user, 'is_authenticated', False) else None,
    )
    entry.save()
    affected = _sync_experience_groups(entry, experience_group_ids)
    broadcast_guidance_updated(affected)
    return guidance_queryset().get(pk=entry.pk)


@transaction.atomic
def update_guidance(
    entry, *, guidance_type=None, trigger_description=None,
    recommended_response=None, experience_group_ids=None,
):
    if guidance_type is not None:
        _validate_guidance_type(guidance_type)
        entry.guidance_type = guidance_type
    if trigger_description is not None:
        entry.trigger_description = _clean_text(
            'trigger_description', trigger_description, TRIGGER_DESCRIPTION_MAX_LENGTH,
        )
    if recommended_response is not None:
        entry.recommended_response = _clean_text(
            'recommended_response', recommended_response, RECOMMENDED_RESPONSE_MAX_LENGTH,
        )
    entry.save()

    if experience_group_ids is not None:
        affected = _sync_experience_groups(entry, experience_group_ids)
    else:
        affected = set(entry.experience_group_links.values_list('experience_group_id', flat=True))
    broadcast_guidance_updated(affected)
    return guidance_queryset().get(pk=entry.pk)


@transaction.atomic
def delete_guidance(entry):
    affected = list(entry.experience_group_links.values_list('experience_group_id', flat=True))
    entry.delete()
    broadcast_guidance_updated(affected)
    return affected


@transaction.atomic
def reorder_guidance(project_id, experience_group_id, ordered_ids):
    """
    Set the display order of one EG's entries. ``ordered_ids`` must list every
    entry currently linked to the group exactly once, so a stale admin view
    is rejected instead of silently dropping entries.
    """
    _lock_experience_groups(project_id, [experience_group_id])

    if len(ordered_ids) != len(set(ordered_ids)):
        raise ValidationError({'ids': 'Duplicate guidance ids are not allowed.'})

    links = list(
        GuidanceEntryExperienceGroup.objects.filter(
            experience_group_id=experience_group_id,
            entry__project_id=project_id,
        ),
    )
    by_entry = {link.entry_id: link for link in links}
    if set(ordered_ids) != set(by_entry):
        raise ValidationError({
            'ids': 'The list must contain every guidance entry in this experience group exactly once.',
        })

    for index, entry_id in enumerate(ordered_ids):
        by_entry[entry_id].display_order = index
    GuidanceEntryExperienceGroup.objects.bulk_update(links, ['display_order'])

    broadcast_guidance_updated([experience_group_id])
    return list_guidance(project_id, experience_group_id=experience_group_id)


def get_guidance_for_conversation(conversation):
    """
    Guidance for the conversation's matched Experience Group, which is the
    customer's EG. Returns ``(experience_group | None, [(entry, display_order)])``.
    """
    customer = conversation.customer
    group = customer.experience_group if customer is not None else None
    if group is None:
        return None, []

    links = (
        GuidanceEntryExperienceGroup.objects.filter(
            experience_group=group,
            entry__project_id=group.project_id,
        )
        .select_related('entry')
        .order_by('display_order', 'id')
    )
    return group, [(link.entry, link.display_order) for link in links]
