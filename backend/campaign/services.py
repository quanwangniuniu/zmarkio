"""
Campaign Management Module - Services
============================================================================

Keep business logic out of ViewSets (KISS).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError as DRFValidationError

from core.models import Project
from core.utils.project import has_project_access
from core.models import ProjectMember
from task.models import Task
from .models import Campaign, CampaignTemplate, CampaignTaskLink, AutomationTrigger, AutomationExecution
from .models import CampaignPlatformIntegration

User = get_user_model()


class CampaignPlatformIntegrationService:
    @staticmethod
    def classify_sync_error(error: Exception) -> str:
        """Use structured provider errors; never persist token-bearing messages."""
        from meta_ads.meta_client import MetaApiError

        codes = CampaignPlatformIntegration.SyncError
        status_code = None
        body = {}
        if isinstance(error, MetaApiError):
            status_code, body = error.status_code, error.body
        detail = body.get('error', {}) if isinstance(body, dict) else {}
        detail = detail if isinstance(detail, dict) else {}
        # Meta returns expired/revoked tokens as HTTP 400 with code 190 or 102.
        # The type OAuthException alone also covers throttling and is not enough.
        if str(detail.get('code')) in ('190', '102') or status_code == 401:
            return codes.AUTH
        if (status_code == 429 or (status_code is not None and status_code >= 500)
                or detail.get('is_transient') is True
                or str(detail.get('code')) in ('4', '17', '32', '613')
                or isinstance(error, (requests.Timeout, requests.ConnectionError))):
            return codes.TRANSIENT
        if status_code == 403:
            return codes.AUTH
        return codes.UNKNOWN

    @staticmethod
    def begin_sync(*, ad_account):
        attempted_at = timezone.now()
        if ad_account.project_id is None:
            return attempted_at
        campaigns = Campaign.objects.filter(project_id=ad_account.project_id, is_deleted=False)
        for campaign in campaigns:
            if Campaign.Platform.META not in (campaign.platforms or []):
                continue
            integration, created = CampaignPlatformIntegration.objects.get_or_create(
                campaign=campaign, ad_account=ad_account,
                defaults={'last_sync_attempted_at': attempted_at},
            )
            if not created:
                CampaignPlatformIntegration.objects.filter(pk=integration.pk).filter(
                    Q(last_sync_attempted_at__isnull=True) | Q(last_sync_attempted_at__lt=attempted_at),
                ).update(last_sync_attempted_at=attempted_at)
        return attempted_at

    @staticmethod
    @transaction.atomic
    def finish_sync(*, ad_account, attempted_at, error=None):
        from notifications.dispatch import notify_campaign_platform_auth_error

        codes = CampaignPlatformIntegration.SyncError
        error_code = CampaignPlatformIntegrationService.classify_sync_error(error) if error is not None else codes.NONE
        integrations = CampaignPlatformIntegration.objects.select_for_update(of=('self',)).filter(
            ad_account=ad_account,
            campaign__project_id=ad_account.project_id,
            campaign__is_deleted=False,
        ).select_related('campaign__project__organization', 'ad_account__connection__user')
        for integration in integrations:
            if Campaign.Platform.META not in (integration.campaign.platforms or []):
                continue
            # A successful newer attempt supersedes any late result from this one.
            if integration.last_synced_at and integration.last_synced_at >= attempted_at:
                continue
            was_auth_error = integration.last_sync_error == codes.AUTH
            is_latest_attempt = integration.last_sync_attempted_at == attempted_at
            if error is None:
                # Store the successful attempt's start, so completion order cannot
                # make an older success supersede a newer credential failure.
                integration.last_synced_at = attempted_at
                if not integration.last_sync_error_at or integration.last_sync_error_at <= attempted_at:
                    integration.last_sync_error = codes.NONE
                    integration.last_sync_error_at = None
            elif error_code == codes.AUTH:
                if was_auth_error and integration.last_sync_error_at and integration.last_sync_error_at >= attempted_at:
                    continue
                integration.last_sync_error = codes.AUTH
                integration.last_sync_error_at = attempted_at
            elif is_latest_attempt and not was_auth_error:
                integration.last_sync_error = error_code
                integration.last_sync_error_at = attempted_at
            else:
                continue
            integration.save(update_fields=['last_sync_error', 'last_sync_error_at', 'last_synced_at'])
            if error_code == codes.AUTH and not was_auth_error:
                notify_campaign_platform_auth_error(integration=integration)


class CampaignService:
    @staticmethod
    def assert_campaign_access(*, user, campaign: Campaign) -> None:
        if not has_project_access(user, campaign.project):
            raise PermissionDenied('You do not have access to this campaign.')

    @staticmethod
    @transaction.atomic
    def transition_status(*, campaign: Campaign, transition: str, user, status_note: Optional[str] = None) -> Campaign:
        """
        Run a django-fsm transition by name, persist, and return the updated campaign.
        """
        CampaignService.assert_campaign_access(user=user, campaign=campaign)

        if status_note is not None:
            campaign.status_note = status_note

        try:
            fn = getattr(campaign, transition)
        except AttributeError:
            raise DRFValidationError({'transition': f'Unknown transition: {transition}'})

        try:
            fn(user=user)
            campaign.save()
        except ValidationError as e:
            raise DRFValidationError(str(e))

        return campaign

    @staticmethod
    @transaction.atomic
    def soft_delete(*, campaign: Campaign) -> None:
        if campaign.status != Campaign.Status.PLANNING:
            raise DRFValidationError({
                'status': 'Only campaigns in PLANNING status can be deleted directly.'
            })
        campaign.is_deleted = True
        campaign.save(update_fields=['is_deleted'])


class TemplateService:
    @staticmethod
    def _resolve_template_project(*, user, sharing_scope: str, project_id: Optional[str]) -> Optional[Project]:
        if sharing_scope in (CampaignTemplate.SharingScope.TEAM, CampaignTemplate.SharingScope.ORGANIZATION):
            if not project_id:
                raise DRFValidationError({'project_id': 'project_id is required for TEAM/ORGANIZATION templates'})
            project = Project.objects.filter(id=project_id).first()
            if not project:
                raise DRFValidationError({'project_id': 'Project not found'})
            if not has_project_access(user, project):
                raise PermissionDenied('You do not have access to this project.')
            return project
        return None

    @staticmethod
    @transaction.atomic
    def save_campaign_as_template(
        *,
        campaign: Campaign,
        user,
        name: str,
        description: Optional[str] = None,
        sharing_scope: str = CampaignTemplate.SharingScope.PERSONAL,
        project_id: Optional[str] = None,
    ) -> CampaignTemplate:
        """
        Save campaign structure as a template.

        Auto-versioning policy:
        - If a non-archived template with same (scope+name) exists, archive it and create next version.
        """
        CampaignService.assert_campaign_access(user=user, campaign=campaign)

        name = (name or '').strip()
        if not name:
            raise DRFValidationError({'name': 'name is required'})

        template_project = TemplateService._resolve_template_project(
            user=user,
            sharing_scope=sharing_scope,
            project_id=project_id,
        )

        existing_qs = CampaignTemplate.objects.filter(
            name=name,
            sharing_scope=sharing_scope,
            is_archived=False,
        )
        if sharing_scope == CampaignTemplate.SharingScope.PERSONAL:
            existing_qs = existing_qs.filter(creator=user)
        else:
            existing_qs = existing_qs.filter(project=template_project)

        existing = existing_qs.order_by('-version_number', '-created_at').first()
        next_version = (existing.version_number + 1) if existing else 1
        if existing:
            existing.is_archived = True
            existing.save(update_fields=['is_archived', 'updated_at'])

        template = CampaignTemplate.objects.create(
            name=name,
            description=description,
            creator=user,
            version_number=next_version,
            sharing_scope=sharing_scope,
            project=template_project,
            # capture structure defaults from campaign
            objective=campaign.objective,
            platforms=campaign.platforms or [],
            hypothesis_framework=campaign.hypothesis,
            tag_suggestions=campaign.tags or [],
            # keep workflow fields minimal for MVP
            task_checklist=[],
            review_schedule_pattern={},
            decision_point_triggers=[],
            recommended_variation_count=3,
            variation_templates=[],
        )

        return template

    @staticmethod
    @transaction.atomic
    def create_campaign_from_template(
        *,
        template: CampaignTemplate,
        user,
        name: str,
        project_id: str,
        owner_id: str,
        start_date=None,
        end_date=None,
        assignee_id: Optional[str] = None,
        objective: Optional[str] = None,
        platforms=None,
        hypothesis: Optional[str] = None,
        tags=None,
        budget_estimate=None,
    ) -> Campaign:
        """
        Create a campaign from a template (apply template).
        """
        name = (name or '').strip()
        if not name:
            raise DRFValidationError({'name': 'name is required'})
        if not project_id:
            raise DRFValidationError({'project': 'project is required'})
        if not owner_id:
            raise DRFValidationError({'owner': 'owner is required'})

        project = Project.objects.filter(id=project_id).first()
        if not project:
            raise DRFValidationError({'project': 'Project not found'})
        if not has_project_access(user, project):
            raise PermissionDenied('You do not have access to this project.')

        owner = User.objects.filter(id=owner_id).first()
        if not owner:
            raise DRFValidationError({'owner': 'User not found'})
        if not has_project_access(owner, project):
            raise DRFValidationError({'owner': 'Owner must be a member of this project'})

        assignee = None
        if assignee_id:
            assignee = User.objects.filter(id=assignee_id).first()
            if not assignee:
                raise DRFValidationError({'assignee': 'User not found'})
            if not has_project_access(assignee, project):
                raise DRFValidationError({'assignee': 'Assignee must be a member of this project'})

        start_date_value = start_date or timezone.now().date()

        # Create campaign - FSMField will use default value (PLANNING) automatically
        # Don't set status explicitly as it's protected
        campaign = Campaign.objects.create(
            name=name,
            project=project,
            owner=owner,
            creator=user,
            assignee=assignee,
            objective=objective or template.objective,
            platforms=platforms or template.platforms or [],
            hypothesis=hypothesis if hypothesis is not None else template.hypothesis_framework,
            tags=tags or template.tag_suggestions or [],
            start_date=start_date_value,
            end_date=end_date,
            budget_estimate=budget_estimate,
            # status will be set to default (PLANNING) by FSMField
        )

        # Enforce model-level constraints (start_date rules, etc.)
        # Exclude 'status' field from validation to avoid FSM protection errors.
        # Note: clean() method is still called and can access self.status for business logic
        # validation (e.g., PLANNING status date checks, COMPLETED status end_date requirement).
        campaign.full_clean(exclude=['status'])

        return campaign


class CampaignTaskIntegrationService:
    """
    Integration service for Campaign ↔ Task.
    """

    BUDGET_TASK_SUMMARY = "Allocate budget for campaign"
    ASSET_TASK_SUMMARY = "Prepare creative assets"

    @staticmethod
    def _ensure_owner_membership(*, owner, project: Project) -> None:
        ProjectMember.objects.get_or_create(
            user=owner,
            project=project,
            defaults={'is_active': True},
        )

    @staticmethod
    def link_task(*, campaign: Campaign, task: Task, link_type: str | None = None) -> CampaignTaskLink:
        link, _ = CampaignTaskLink.objects.get_or_create(
            campaign=campaign,
            task=task,
            defaults={'link_type': link_type},
        )
        if link_type is not None and link.link_type != link_type:
            link.link_type = link_type
            link.save(update_fields=['link_type', 'updated_at'])
        return link

    @staticmethod
    def unlink_task(*, link_id) -> int:
        deleted, _ = CampaignTaskLink.objects.filter(id=link_id).delete()
        return deleted

    @staticmethod
    def _get_or_create_campaign_created_trigger(*, project: Project, task_type: str, summary: str) -> AutomationTrigger:
        trigger, _ = AutomationTrigger.objects.get_or_create(
            project=project,
            trigger_type=AutomationTrigger.TriggerType.TIME_BASED,
            trigger_config={'event': 'CAMPAIGN_CREATED', 'task_type': task_type},
            action_type=AutomationTrigger.ActionType.CREATE_TASK,
            action_config={'task_type': task_type, 'summary': summary},
            defaults={'is_active': True, 'prevent_duplicates': True},
        )
        return trigger

    @staticmethod
    def _already_executed(*, trigger: AutomationTrigger, campaign: Campaign) -> bool:
        if not trigger.prevent_duplicates:
            return False
        return AutomationExecution.objects.filter(
            trigger=trigger,
            campaign=campaign,
            executed_successfully=True,
        ).exists()

    @staticmethod
    def _log_execution(
        *,
        trigger: AutomationTrigger,
        campaign: Campaign,
        created_task: Task | None,
        ok: bool,
        error: str | None = None,
        context: dict | None = None,
    ) -> None:
        AutomationExecution.objects.create(
            trigger=trigger,
            campaign=campaign,
            executed_successfully=ok,
            error_message=error,
            created_task=created_task,
            trigger_context=context or {},
        )

    @staticmethod
    @transaction.atomic
    def on_campaign_created(*, campaign: Campaign, actor) -> None:
        """
        Hook: when a campaign is created, auto-create 2 tasks (budget + asset) owned by campaign.owner.
        """
        # Ensure actor has access (creator should, but keep safe)
        if not has_project_access(actor, campaign.project):
            raise PermissionDenied('You do not have access to this project.')

        CampaignTaskIntegrationService._ensure_owner_membership(owner=campaign.owner, project=campaign.project)

        task_specs = [
            ('budget', CampaignTaskIntegrationService.BUDGET_TASK_SUMMARY),
            ('asset', CampaignTaskIntegrationService.ASSET_TASK_SUMMARY),
        ]

        for task_type, summary in task_specs:
            trigger = CampaignTaskIntegrationService._get_or_create_campaign_created_trigger(
                project=campaign.project,
                task_type=task_type,
                summary=summary,
            )

            if CampaignTaskIntegrationService._already_executed(trigger=trigger, campaign=campaign):
                continue

            created_task = None
            try:
                created_task = Task.objects.create(
                    summary=summary,
                    type=task_type,
                    project=campaign.project,
                    created_by=campaign.creator or campaign.owner,
                    owner=campaign.owner,
                )
                # Auto-submit to SUBMITTED (per your requirement)
                created_task.submit()
                created_task.save()

                CampaignTaskIntegrationService.link_task(
                    campaign=campaign,
                    task=created_task,
                    link_type='auto_generated',
                )

                CampaignTaskIntegrationService._log_execution(
                    trigger=trigger,
                    campaign=campaign,
                    created_task=created_task,
                    ok=True,
                    context={'event': 'CAMPAIGN_CREATED', 'task_type': task_type},
                )
            except Exception as e:
                CampaignTaskIntegrationService._log_execution(
                    trigger=trigger,
                    campaign=campaign,
                    created_task=created_task,
                    ok=False,
                    error=str(e),
                    context={'event': 'CAMPAIGN_CREATED', 'task_type': task_type},
                )
