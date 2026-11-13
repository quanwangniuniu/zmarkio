import pytest
from django.urls import reverse
from freezegun import freeze_time
from rest_framework import status

from csm.models import Conversation, SupportChannel, SupportChannelExperienceGroup


pytestmark = pytest.mark.django_db


def _list_url(project_id, **params):
    query = f'?project={project_id}'
    for key, value in params.items():
        query += f'&{key}={value}'
    return reverse('support-channel-list') + query


def _detail_url(pk):
    return reverse('support-channel-detail', kwargs={'pk': pk})


def _experience_groups_url(pk):
    return reverse('support-channel-experience-groups', kwargs={'pk': pk})


def _embed_snippet_url(pk):
    return reverse('support-channel-embed-snippet', kwargs={'pk': pk})


def _portal_channel_config_url(lookup):
    return reverse('portal-channel-config', kwargs={'lookup': lookup})


def _portal_eg_channels_url(eg_id):
    return reverse('portal-eg-channels', kwargs={'eg_id': eg_id})


def _portal_conversation_list_url():
    return reverse('portal-conversation-list')


def _always_open_hours():
    from csm.services.support_channels import WEEKDAYS
    return {
        day: {'enabled': True, 'start': '00:00', 'end': '23:59'}
        for day in WEEKDAYS
    }


def _response_rows(response):
    data = response.data
    if isinstance(data, dict) and 'results' in data:
        return data['results']
    return data


@pytest.fixture
def live_chat_channel(project, csm_queue):
    return SupportChannel.objects.create(
        project=project,
        channel_type=SupportChannel.ChannelType.LIVE_CHAT,
        display_name='Billing Chat',
        default_queue=csm_queue,
    )


@pytest.fixture
def inactive_live_chat_channel(project, csm_queue):
    return SupportChannel.objects.create(
        project=project,
        channel_type=SupportChannel.ChannelType.LIVE_CHAT,
        display_name='Archived Chat',
        default_queue=csm_queue,
        is_active=False,
    )


@pytest.fixture
def always_open_live_chat_channel(project, csm_queue):
    return SupportChannel.objects.create(
        project=project,
        channel_type=SupportChannel.ChannelType.LIVE_CHAT,
        display_name='Always Open Chat',
        default_queue=csm_queue,
        operating_hours=_always_open_hours(),
    )


class TestAdminCRUD:
    def test_create_live_chat_success(self, member_client, project, csm_queue):
        response = member_client.post(
            _list_url(project.id),
            {
                'channel_type': 'live_chat',
                'display_name': 'Billing Chat',
                'default_queue': csm_queue.id,
            },
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['channel_type'] == 'live_chat'
        assert response.data['default_queue'] == csm_queue.id
        assert response.data['embed_key']

    def test_create_live_chat_missing_queue(self, member_client, project):
        response = member_client.post(
            _list_url(project.id),
            {
                'channel_type': 'live_chat',
                'display_name': 'Billing Chat',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'default_queue' in response.data

    def test_create_contact_form_missing_ticket_form(self, member_client, project):
        response = member_client.post(
            _list_url(project.id),
            {
                'channel_type': 'contact_form',
                'display_name': 'Support Request',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'ticket_form' in response.data

    def test_create_contact_form_success(self, member_client, project, default_form):
        response = member_client.post(
            _list_url(project.id),
            {
                'channel_type': 'contact_form',
                'display_name': 'Support Request',
                'ticket_form': default_form.id,
            },
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data['ticket_form'] == default_form.id

    def test_create_email_missing_address(self, member_client, project):
        response = member_client.post(
            _list_url(project.id),
            {
                'channel_type': 'email',
                'display_name': 'Email Support',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'email_address' in response.data

    def test_list_excludes_inactive_by_default(
        self, member_client, project, live_chat_channel, inactive_live_chat_channel,
    ):
        response = member_client.get(_list_url(project.id))
        names = [row['display_name'] for row in _response_rows(response)]
        assert 'Billing Chat' in names
        assert 'Archived Chat' not in names

    def test_list_include_inactive(
        self, member_client, project, live_chat_channel, inactive_live_chat_channel,
    ):
        response = member_client.get(_list_url(project.id, include_inactive=1))
        names = [row['display_name'] for row in _response_rows(response)]
        assert 'Billing Chat' in names
        assert 'Archived Chat' in names

    def test_patch_updates_display_name_only(
        self, member_client, project, live_chat_channel, csm_queue,
    ):
        other = SupportChannel.objects.create(
            project=project,
            channel_type=SupportChannel.ChannelType.LIVE_CHAT,
            display_name='Other Chat',
            default_queue=csm_queue,
        )
        response = member_client.patch(
            _detail_url(live_chat_channel.id),
            {'display_name': 'Renamed Chat'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        other.refresh_from_db()
        assert other.display_name == 'Other Chat'

    def test_delete_soft_deactivates(self, member_client, live_chat_channel):
        response = member_client.delete(_detail_url(live_chat_channel.id))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        live_chat_channel.refresh_from_db()
        assert live_chat_channel.is_active is False
        assert SupportChannel.objects.filter(pk=live_chat_channel.pk).exists()

    def test_retrieve_includes_experience_groups(
        self, member_client, live_chat_channel, experience_group,
    ):
        SupportChannelExperienceGroup.objects.create(
            channel=live_chat_channel,
            experience_group=experience_group,
        )
        response = member_client.get(_detail_url(live_chat_channel.id))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['experience_groups']) == 1
        assert response.data['experience_groups'][0]['name'] == 'VIP Support'

    def test_non_member_forbidden(self, outsider_client, project, csm_queue):
        response = outsider_client.post(
            _list_url(project.id),
            {
                'channel_type': 'live_chat',
                'display_name': 'Blocked',
                'default_queue': csm_queue.id,
            },
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestExperienceGroupAssignments:
    def test_replace_assignments_success(
        self, member_client, live_chat_channel, experience_group, project,
    ):
        from experience_group.models import ExperienceGroup
        second = ExperienceGroup.objects.create(project=project, name='SMB')
        response = member_client.put(
            _experience_groups_url(live_chat_channel.id),
            {'experience_group_ids': [experience_group.id, second.id]},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        names = {row['name'] for row in response.data}
        assert names == {'VIP Support', 'SMB'}

    def test_replace_assignments_empty(self, member_client, live_chat_channel, experience_group):
        SupportChannelExperienceGroup.objects.create(
            channel=live_chat_channel,
            experience_group=experience_group,
        )
        response = member_client.put(
            _experience_groups_url(live_chat_channel.id),
            {'experience_group_ids': []},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []
        assert not live_chat_channel.experience_group_links.exists()

    def test_replace_rejects_cross_project_eg(
        self, member_client, live_chat_channel, organization,
    ):
        from core.models import Project
        from experience_group.models import ExperienceGroup
        other_project = Project.objects.create(
            name='Other',
            organization=organization,
        )
        foreign_eg = ExperienceGroup.objects.create(project=other_project, name='Foreign')
        response = member_client.put(
            _experience_groups_url(live_chat_channel.id),
            {'experience_group_ids': [foreign_eg.id]},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'experience_group_ids' in response.data


class TestEmbedSnippet:
    def test_embed_snippet_live_chat(self, member_client, live_chat_channel):
        response = member_client.get(_embed_snippet_url(live_chat_channel.id))
        assert response.status_code == status.HTTP_200_OK
        assert str(live_chat_channel.embed_key) in response.data['snippet']
        assert response.data['embed_key'] == str(live_chat_channel.embed_key)

    def test_embed_snippet_non_live_chat(self, member_client, project, default_form):
        channel = SupportChannel.objects.create(
            project=project,
            channel_type=SupportChannel.ChannelType.CONTACT_FORM,
            display_name='Form Channel',
            ticket_form=default_form,
        )
        response = member_client.get(_embed_snippet_url(channel.id))
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestOperatingHoursService:
    def test_online_during_weekday_hours(self, live_chat_channel):
        from datetime import datetime

        from csm.services.support_channels import evaluate_channel_availability
        from django.utils import timezone

        at = timezone.make_aware(datetime(2026, 6, 15, 10, 0, 0), timezone.utc)
        result = evaluate_channel_availability(live_chat_channel, at=at)
        assert result['is_online'] is True

    def test_offline_on_disabled_day(self, live_chat_channel):
        from datetime import datetime

        from csm.services.support_channels import evaluate_channel_availability
        from django.utils import timezone

        at = timezone.make_aware(datetime(2026, 6, 20, 10, 0, 0), timezone.utc)
        result = evaluate_channel_availability(live_chat_channel, at=at)
        assert result['is_online'] is False
        assert result['reason'] == 'closed_day'

    def test_inactive_channel_always_offline(self, inactive_live_chat_channel):
        from csm.services.support_channels import evaluate_channel_availability

        result = evaluate_channel_availability(inactive_live_chat_channel)
        assert result['is_online'] is False
        assert result['reason'] == 'inactive'

    def test_offline_after_end_time(self, live_chat_channel):
        from datetime import datetime

        from csm.services.support_channels import evaluate_channel_availability
        from django.utils import timezone

        at = timezone.make_aware(datetime(2026, 6, 16, 17, 1, 0), timezone.utc)
        result = evaluate_channel_availability(live_chat_channel, at=at)
        assert result['is_online'] is False
        assert result['reason'] == 'outside_hours'

    def test_timezone_boundary(self, project, csm_queue):
        from datetime import datetime

        from csm.services.support_channels import evaluate_channel_availability
        from django.utils import timezone

        channel = SupportChannel.objects.create(
            project=project,
            channel_type=SupportChannel.ChannelType.LIVE_CHAT,
            display_name='NY Chat',
            default_queue=csm_queue,
            timezone='America/New_York',
        )
        # 14:00 UTC = 10:00 EDT on a Monday in June 2026
        at = timezone.make_aware(datetime(2026, 6, 15, 14, 0, 0), timezone.utc)
        result = evaluate_channel_availability(channel, at=at)
        assert result['is_online'] is True

    def test_malformed_hours_safe_offline(self, project, csm_queue):
        from datetime import datetime

        from csm.services.support_channels import evaluate_channel_availability
        from django.utils import timezone

        channel = SupportChannel.objects.create(
            project=project,
            channel_type=SupportChannel.ChannelType.LIVE_CHAT,
            display_name='Bad Hours Chat',
            default_queue=csm_queue,
            operating_hours={'monday': {'enabled': True, 'start': 'bad', 'end': '17:00'}},
        )
        at = timezone.make_aware(datetime(2026, 6, 15, 10, 0, 0), timezone.utc)
        result = evaluate_channel_availability(channel, at=at)
        assert result['is_online'] is False
        assert result['reason'] == 'malformed_hours'


class TestRuntimeAPI:
    # Keep online checks within business hours; the 23:59 end time is exclusive.
    @freeze_time("2026-01-05 12:00:00")
    def test_config_by_embed_key_online(self, api_client, always_open_live_chat_channel):
        url = _portal_channel_config_url(always_open_live_chat_channel.embed_key)
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['is_online'] is True
        assert response.data['embed_key'] == str(always_open_live_chat_channel.embed_key)
        assert response.data['offline'] is None

    def test_config_by_numeric_id(self, api_client, always_open_live_chat_channel):
        response = api_client.get(_portal_channel_config_url(always_open_live_chat_channel.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['channel_id'] == always_open_live_chat_channel.id

    def test_config_inactive_404(self, api_client, inactive_live_chat_channel):
        response = api_client.get(_portal_channel_config_url(inactive_live_chat_channel.embed_key))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_config_offline_closed_day(self, api_client, live_chat_channel):
        from unittest.mock import patch

        with patch(
            'csm.services.support_channels.evaluate_channel_availability',
            return_value={'is_online': False, 'reason': 'closed_day'},
        ):
            response = api_client.get(_portal_channel_config_url(live_chat_channel.embed_key))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['is_online'] is False
        assert response.data['offline'] is not None
        assert 'message' in response.data['offline']

    def test_eg_channels_list(
        self,
        api_client,
        always_open_live_chat_channel,
        published_experience_group,
    ):
        SupportChannelExperienceGroup.objects.create(
            channel=always_open_live_chat_channel,
            experience_group=published_experience_group,
        )
        response = api_client.get(_portal_eg_channels_url(published_experience_group.id))
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]['channel_id'] == always_open_live_chat_channel.id

    def test_eg_channels_excludes_inactive(
        self,
        api_client,
        inactive_live_chat_channel,
        published_experience_group,
    ):
        SupportChannelExperienceGroup.objects.create(
            channel=inactive_live_chat_channel,
            experience_group=published_experience_group,
        )
        response = api_client.get(_portal_eg_channels_url(published_experience_group.id))
        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_eg_channels_draft_eg_404(self, api_client, experience_group, always_open_live_chat_channel):
        SupportChannelExperienceGroup.objects.create(
            channel=always_open_live_chat_channel,
            experience_group=experience_group,
        )
        response = api_client.get(_portal_eg_channels_url(experience_group.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestPortalConversationGuard:
    def test_create_without_channel_unchanged(
        self, portal_customer_client, customer, csm_queue,
    ):
        response = portal_customer_client.post(
            _portal_conversation_list_url(),
            {'message': 'Hello'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        conversation = Conversation.objects.get(pk=response.data['id'])
        assert conversation.support_channel_id is None
        assert conversation.queue_id == csm_queue.id

    @freeze_time("2026-01-05 12:00:00")
    def test_create_live_chat_when_online(
        self, portal_customer_client, customer, always_open_live_chat_channel,
    ):
        response = portal_customer_client.post(
            _portal_conversation_list_url(),
            {
                'message': 'Hello',
                'support_channel_id': always_open_live_chat_channel.id,
            },
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED
        conversation = Conversation.objects.get(pk=response.data['id'])
        assert conversation.support_channel_id == always_open_live_chat_channel.id
        assert conversation.queue_id == always_open_live_chat_channel.default_queue_id

    def test_create_live_chat_when_offline(
        self, portal_customer_client, customer, always_open_live_chat_channel,
    ):
        from unittest.mock import patch

        with patch(
            'portal.views.evaluate_channel_availability',
            return_value={'is_online': False, 'reason': 'outside_hours'},
        ):
            response = portal_customer_client.post(
                _portal_conversation_list_url(),
                {
                    'message': 'Hello',
                    'support_channel_id': always_open_live_chat_channel.id,
                },
                format='json',
            )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data['detail'] == 'Channel is currently offline.'
        assert 'offline' in response.data

    def test_create_inactive_channel_404(
        self, portal_customer_client, customer, inactive_live_chat_channel,
    ):
        response = portal_customer_client.post(
            _portal_conversation_list_url(),
            {
                'message': 'Hello',
                'support_channel_id': inactive_live_chat_channel.id,
            },
            format='json',
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND
