import logging

from django.contrib.auth import get_user_model
from django.http import FileResponse
from django.db import transaction
from audit.utils import capture_snapshot, record_audit_entry
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status, viewsets
from core.slug_mixins import SlugLookupViewSetMixin, resolve_lookup_kwargs, resolve_pk_for, resolve_project_pk
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.admin_utils import (
    assign_org_admin,
    can_user_access_organization,
    get_user_organizations,
    is_org_admin,
    switch_organization,
)
from core.models import (
    DataExportRequest,
    Organization,
    OrganizationInvitation,
    OrganizationInvitationUse,
    OrganizationMembership,
    Project,
    ProjectInvitation,
    ProjectMember,
    Role,
)
from core.permissions import (
    CanManageProjectMembers,
    IsProjectMember,
    can_invite_project_members,
    IsProjectOwner,
    can_manage_project_members,
)
from core.serializers import (
    AcceptInvitationSerializer,
    AcceptOrganizationInvitationSerializer,
    OrganizationActivityEventSerializer,
    OrganizationInvitationSerializer,
    OrganizationMembershipSerializer,
    OrganizationSerializer,
    OrganizationSummarySerializer,
    ProjectInvitationSerializer,
    ProjectMemberInviteSerializer,
    ProjectMemberSerializer,
    ProjectOnboardingSerializer,
    ProjectSerializer,
    ProjectSummarySerializer,
    SwitchOrganizationSerializer,
)
from decision.models import Decision, DecisionEdge, DecisionTopicLabel
from decision.serializers import DecisionEdgeSerializer, DecisionGraphNodeSerializer
from decision.services import decision_topic_label as default_decision_topic_label, normalize_decision_topic
from core.services.project_initialization import ProjectInitializationService
from core.services.organization_activity import log_org_activity
from core.services.audit_events import safe_emit_audit_event
from core.services.privacy_export import build_download_url, mark_expired_exports, verify_download_token
from core.tasks import assemble_personal_data_export
from core.utils.invitations import accept_invitation, create_project_invitation, send_invitation_email
from core.utils.kpi_suggestions import get_kpi_suggestions
from core.utils.project_calendars import (
    ensure_project_calendar,
    soft_delete_project_calendars,
)
from notifications.action_urls import overview_action_url

logger = logging.getLogger(__name__)
User = get_user_model()


def _audit_project_event(event_type, request, project, *, target_type="project", target_id=None, before=None, after=None, context=None):
    safe_emit_audit_event(
        event_type=event_type,
        actor=request.user,
        organization=getattr(project, "organization", None),
        project=project,
        target_type=target_type,
        target_id=target_id if target_id is not None else getattr(project, "id", ""),
        before=before,
        after=after,
        context=context,
        request=request,
    )


def _serialize_data_export_request(export_request, request=None):
    download_url = build_download_url(export_request, request=request)
    return {
        "id": str(export_request.id),
        "status": export_request.status,
        "export_format": export_request.export_format,
        "created_at": export_request.created_at,
        "updated_at": export_request.updated_at,
        "completed_at": export_request.completed_at,
        "expires_at": export_request.expires_at,
        "download_url": download_url,
        "failure_reason": export_request.failure_reason,
        "metadata": export_request.metadata,
    }


class PersonalDataExportRequestListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        mark_expired_exports()
        exports = DataExportRequest.objects.filter(user=request.user)[:10]
        return Response([_serialize_data_export_request(item, request=request) for item in exports])

    def post(self, request):
        mark_expired_exports()
        export_format = str(request.data.get("export_format") or DataExportRequest.ExportFormat.JSON).lower()
        if export_format not in DataExportRequest.ExportFormat.values:
            return Response(
                {
                    "export_format": [
                        f"Unsupported export format. Choose one of: {', '.join(DataExportRequest.ExportFormat.values)}."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        active_export = (
            DataExportRequest.objects.filter(
                user=request.user,
                status__in=[
                    DataExportRequest.Status.PENDING,
                    DataExportRequest.Status.PROCESSING,
                    DataExportRequest.Status.READY,
                ],
            )
            .order_by("-created_at")
            .first()
        )

        if active_export and active_export.status == DataExportRequest.Status.READY:
            if (
                active_export.export_format == export_format
                and active_export.expires_at
                and active_export.expires_at > timezone.now()
            ):
                return Response(_serialize_data_export_request(active_export, request=request), status=status.HTTP_200_OK)

        if active_export and active_export.status in [
            DataExportRequest.Status.PENDING,
            DataExportRequest.Status.PROCESSING,
        ]:
            return Response(_serialize_data_export_request(active_export, request=request), status=status.HTTP_202_ACCEPTED)

        export_request = DataExportRequest.objects.create(user=request.user, export_format=export_format)
        safe_emit_audit_event(
            event_type="privacy.data_export.requested",
            actor=request.user,
            organization=getattr(request.user, "current_organization", None),
            project=getattr(request.user, "active_project", None),
            target_type="data_export_request",
            target_id=export_request.id,
            after={
                "status": export_request.status,
                "export_format": export_request.export_format,
                "expires_at": export_request.expires_at,
            },
            request=request,
        )
        assemble_personal_data_export.delay(str(export_request.id))
        return Response(_serialize_data_export_request(export_request, request=request), status=status.HTTP_202_ACCEPTED)


class PersonalDataExportRequestDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, export_id):
        mark_expired_exports()
        export_request = get_object_or_404(DataExportRequest, pk=export_id, user=request.user)
        return Response(_serialize_data_export_request(export_request, request=request))


class PersonalDataExportDownloadView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, export_id):
        mark_expired_exports()
        export_request = get_object_or_404(DataExportRequest, pk=export_id)
        token = request.query_params.get("token", "")
        if (
            export_request.status != DataExportRequest.Status.READY
            or not export_request.file
            or (export_request.expires_at and export_request.expires_at <= timezone.now())
            or not verify_download_token(export_request, token)
        ):
            return Response({"detail": "This export link is invalid or expired."}, status=status.HTTP_403_FORBIDDEN)

        safe_emit_audit_event(
            event_type="privacy.data_export.downloaded",
            actor=export_request.user,
            organization=getattr(export_request.user, "current_organization", None),
            project=getattr(export_request.user, "active_project", None),
            target_type="data_export_request",
            target_id=export_request.id,
            after={
                "status": export_request.status,
                "export_format": export_request.export_format,
                "expires_at": export_request.expires_at,
            },
            request=request,
        )
        response = FileResponse(export_request.file.open("rb"), as_attachment=True, filename=export_request.file.name.rsplit("/", 1)[-1])
        response["Cache-Control"] = "private, no-store"
        return response


class CheckProjectMembershipView(APIView):
    """Return project membership metadata for authenticated users."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        memberships = ProjectMember.objects.filter(user=user, is_active=True)
        project_count = memberships.count()
        # Use the raw FK column — never raises DoesNotExist even when the project is deleted.
        active_project_id = user.active_project_id

        return Response(
            {
                'has_project': project_count > 0,
                'active_project_id': active_project_id,
                'project_count': project_count,
            }
        )


class ProjectOnboardingView(APIView):
    """Handle the multi-step onboarding wizard for creating a project."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ProjectOnboardingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        # Use current_organization for multi-org support
        organization = getattr(user, 'current_organization', None)
        if not organization:
            # Fallback to legacy organization field
            organization = getattr(user, 'organization', None)
        if not organization:
            organization = self._ensure_organization_for_user(user)

        data = serializer.validated_data

        owner = self._resolve_owner(user, data.get('owner_id'))
        if isinstance(owner, Response):
            return owner

        advertising_platforms = data.get('advertising_platforms', [])
        if data.get('advertising_platforms_other') and 'other' not in advertising_platforms:
            advertising_platforms.append('other')

        # DEBUG: Log project creation
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[ProjectOnboarding] Creating project '{data['name']}' for org '{organization.name}' (ID: {organization.id})")

        project = Project.objects.create(
            name=data['name'],
            description=data.get('description'),
            organization=organization,
            owner=owner,
            project_type=data.get('project_type', []),
            work_model=data.get('work_model', []),
            advertising_platforms=advertising_platforms,
            objectives=data.get('objectives', []),
            kpis=data.get('kpis', {}),
            budget_management_type=data.get('budget_management_type'),
            total_monthly_budget=data.get('total_monthly_budget'),
            pacing_enabled=data.get('pacing_enabled', False),
            budget_config=data.get('budget_config', {}),
            primary_audience_type=data.get('primary_audience_type'),
            audience_targeting=data.get('audience_targeting', {}),
        )

        # Ensure creator membership
        ProjectMember.objects.update_or_create(
            user=user,
            project=project,
            defaults={'role': 'owner', 'is_active': True},
        )

        # Ensure owner membership
        ProjectMember.objects.update_or_create(
            user=owner,
            project=project,
            defaults={'role': 'owner', 'is_active': True},
        )

        ensure_project_calendar(project)

        # Set active project
        user.active_project = project
        user.save(update_fields=['active_project'])

        # DEBUG: Confirm project was saved
        logger.info(f"[ProjectOnboarding] Project {project.id} '{project.name}' created successfully for org {organization.name}")

        # Invite existing members (basic implementation)
        self._handle_member_invites(project, data.get('invite_members', []))

        # Initialize project services (best-effort)
        try:
            ProjectInitializationService.initialize_project(project)
        except Exception as exc:  # pragma: no cover - safeguard
            logger.warning("Project initialization failed for project %s: %s", project.id, exc)

        project_data = ProjectSerializer(project, context={'request': request}).data
        project_data['is_active'] = True

        return Response(project_data, status=status.HTTP_201_CREATED)

    def _resolve_owner(self, default_owner, owner_id):
        if not owner_id:
            return default_owner
        if owner_id == default_owner.id:
            return default_owner
        try:
            owner = User.objects.get(id=owner_id)
        except User.DoesNotExist:
            return Response({'error': 'Specified owner was not found.'}, status=status.HTTP_400_BAD_REQUEST)
        if owner.organization_id != default_owner.organization_id:
            return Response(
                {'error': 'Owner must belong to the same organization as the creator.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return owner

    def _handle_member_invites(self, project, invite_members):
        if not invite_members:
            return

        for invite in invite_members:
            email = invite.get('email')
            role = invite.get('role', 'member')
            if not email:
                continue

            try:
                invited_user = User.objects.get(email=email)
            except User.DoesNotExist:
                logger.info("Invite skipped for %s (user does not exist yet)", email)
                continue

            ProjectMember.objects.update_or_create(
                user=invited_user,
                project=project,
                defaults={'role': role, 'is_active': True},
            )
            actor = self.request.user
            if invited_user.id != actor.id:
                try:
                    from notifications.models import NotificationCategory, NotificationEventType  # noqa: PLC0415
                    from notifications.services import create_notification  # noqa: PLC0415
                    create_notification(
                        recipient_id=invited_user.id,
                        actor_id=actor.id,
                        category=NotificationCategory.COLLABORATION,
                        event_type=NotificationEventType.PROJECT_INVITE,
                        title=f"You've been added to project: {project.name}",
                        body=f"You were added to the project \"{project.name}\".",
                        related_object_type="project",
                        related_object_id=str(project.id),
                        action_url=overview_action_url(),
                        metadata={"project_name": project.name},
                    )
                except Exception:
                    logger.exception(
                        "Failed to send PROJECT_INVITE notification for user %s", invited_user.id
                    )

    def _ensure_organization_for_user(self, user):
        from customer.models import CustomerOrganisation
        from csm.models import CustomerUser

        base_name = f"{user.username}'s Organisation"
        name = base_name
        suffix = 1
        while Organization.objects.filter(name=name).exists():
            suffix += 1
            name = f"{base_name} {suffix}"
        email = getattr(user, 'email', '') or ''
        domain = email.split('@')[-1].lower() if '@' in email else None
        organization = Organization.objects.create(name=name, email_domain=domain)

        user.organization = organization
        user.save(update_fields=['organization'])

        # Create a CustomerOrganisation + admin CustomerUser so CSM features work
        cust_org, created = CustomerOrganisation.objects.get_or_create(
            organization=organization,
            defaults={'name': organization.name},
        )
        CustomerUser.objects.get_or_create(
            user=user,
            organisation=cust_org,
            defaults={
                'user_type': 'admin',
                'is_active': True,
                'is_creator': True,
            },
        )
        assign_org_admin(user, organization)

        return organization


class KPISuggestionsView(APIView):
    """Return merged KPI suggestions for provided objectives."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        objectives_param = request.query_params.get('objectives', '')
        objectives = [item.strip() for item in objectives_param.split(',') if item.strip()]

        if not objectives:
            return Response(
                {'error': 'objectives query parameter is required (comma-separated list).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        suggestions = get_kpi_suggestions(objectives)

        return Response(
            {
                'objectives': objectives,
                'suggested_kpis': suggestions,
                'count': len(suggestions),
            }
        )


class ProjectViewSet(SlugLookupViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for Project model with project membership filtering.
    
    Endpoints:
    - GET /api/core/projects/ - List user's projects (with filtering)
    - POST /api/core/projects/ - Create simple project
    - GET /api/core/projects/{id}/ - Get project details
    - PATCH /api/core/projects/{id}/ - Update project
    - POST /api/core/projects/{id}/set-active/ - Set as active project
    """

    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Filter projects by user's access level."""
        from core.admin_utils import get_org_admin_org_ids

        user = self.request.user

        # Base: projects where user has active membership
        member_ids = ProjectMember.objects.filter(
            user=user, is_active=True,
        ).values_list('project_id', flat=True)

        org_ids = get_org_admin_org_ids(user)
        if org_ids:
            queryset = Project.objects.filter(
                Q(id__in=member_ids) | Q(organization_id__in=org_ids)
            ).distinct()
        else:
            queryset = Project.objects.filter(id__in=member_ids)

        queryset = queryset.select_related('organization', 'owner')

        # Filter by active_only query parameter
        active_only = self.request.query_params.get('active_only', 'false').lower() == 'true'
        if active_only and user.active_project_id:
            queryset = queryset.filter(id=user.active_project_id)

        return queryset.order_by('-created_at')

    def get_permissions(self):
        """Set permissions based on action."""
        if self.action in ['update', 'partial_update', 'destroy']:
            return [IsAuthenticated(), IsProjectOwner()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        """Create project and add user as owner.

        If the user has no Organization yet, one is auto-created from their
        email prefix and they become its admin (via CustomerUser).
        """
        from django.db import connection
        from core.services.tenant import slug_to_schema_name
        from psycopg2 import sql

        user = self.request.user
        # Use current_organization for multi-org support
        organization = getattr(user, 'current_organization', None)
        if not organization:
            # Fallback to legacy organization field
            organization = getattr(user, 'organization', None)

        if not organization:
            organization = self._auto_create_organization(user)
            # provision_tenant_schema() resets search_path to 'public' in its
            # finally block. Switch back to the new org's schema so that Project
            # and ProjectMember are created in the correct tenant schema.
            schema_name = slug_to_schema_name(organization.slug)
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL('SET search_path TO {}, public').format(
                        sql.Identifier(schema_name)
                    )
                )

        project = serializer.save(
            organization=organization,
            owner=user,
        )

        # Create project membership
        ProjectMember.objects.create(
            user=user,
            project=project,
            role='owner',
            is_active=True
        )

        # Set as active project if user has no active project
        # CRITICAL: user.active_project is a FK to Project, but User is in public schema
        # and Project is in tenant schema. We must check and update in public schema
        # to avoid cross-schema FK violations.
        with connection.cursor() as cursor:
            cursor.execute('SHOW search_path')
            original_path = cursor.fetchone()[0]

        # Switch to public schema to access user data
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')

        try:
            # Re-fetch user from public schema to get fresh active_project value
            from core.models import CustomUser
            user = CustomUser.objects.get(pk=user.pk)

            if not user.active_project_id:  # Use _id to avoid FK lookup
                user.active_project_id = project.id
                user.save(update_fields=['active_project'])
        finally:
            # Restore tenant schema
            with connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO {original_path}')

        ensure_project_calendar(project)
        _audit_project_event(
            "project.created",
            self.request,
            project,
            after={
                "name": project.name,
                "slug": project.slug,
                "organization_id": project.organization_id,
                "owner_id": project.owner_id,
            },
        )

        return project

    @staticmethod
    def _auto_create_organization(user):
        """Auto-create an Organization for a user who doesn't have one yet."""
        from customer.models import CustomerOrganisation
        from csm.models import CustomerUser

        org_name = f"{user.username}'s Organisation"

        # Ensure uniqueness
        base_name = org_name
        counter = 1
        while Organization.objects.filter(name=org_name).exists():
            counter += 1
            org_name = f"{base_name} ({counter})"

        organization = Organization.objects.create(name=org_name)

        # Link user to the new organization
        user.organization = organization
        user.save(update_fields=['organization'])

        # Also create a CustomerOrganisation so CSM features work
        cust_org, _ = CustomerOrganisation.objects.get_or_create(
            organization=organization,
            defaults={'name': org_name},
        )

        # Make user the admin (and creator) of the CSM org
        CustomerUser.objects.get_or_create(
            user=user,
            organisation=cust_org,
            defaults={
                'user_type': 'admin',
                'is_active': True,
                'is_creator': True,
            },
        )
        assign_org_admin(user, organization)

        return organization

    def perform_update(self, serializer):
        before = capture_snapshot(serializer.instance)
        with transaction.atomic():
            instance = serializer.save()
            record_audit_entry(
                actor=self.request.user if self.request.user.is_authenticated else None,
                action='project.updated',
                target=instance,
                before=before,
                after=capture_snapshot(instance),
                organization=instance.organization,
            )

    def perform_destroy(self, instance):
        """Delete project using raw SQL to bypass Django's cross-schema cascade issues."""
        from django.db import connection
        before = capture_snapshot(instance)
        project_org = instance.organization

        # CRITICAL: Multi-tenant architecture has FK relationships across schemas:
        # - public schema: slack_integration_notificationpreference, google_calendar_connections, etc.
        # - tenant schema: core_project, calendars_calendar, core_projectmember, etc.
        #
        # Django's ORM delete() tries to handle CASCADE/SET_NULL in Python, causing it to
        # query tables in the wrong schema. Solution: use raw SQL and let PostgreSQL handle it.

        project_id = instance.id
        project_snapshot = {
            "id": instance.id,
            "name": instance.name,
            "slug": instance.slug,
            "organization_id": instance.organization_id,
            "owner_id": instance.owner_id,
        }

        # Step 1: Get original search_path and find calendar IDs in tenant schema first
        with connection.cursor() as cursor:
            cursor.execute('SHOW search_path')
            original_path = cursor.fetchone()[0]

        # Get calendar IDs associated with this project (in tenant schema)
        calendar_ids = []
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT id FROM calendars_calendar WHERE project_id = %s',
                    [project_id]
                )
                calendar_ids = [row[0] for row in cursor.fetchall()]
        except Exception:
            pass

        # Switch to public schema and delete cross-schema related records
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')

        try:
            # Use transaction.atomic() (savepoint) so that if either DELETE
            # fails with a SQL error the savepoint is rolled back cleanly and
            # the outer transaction stays healthy.  The bare except+pass
            # pattern without a savepoint leaves the connection in an aborted-
            # transaction state, causing every subsequent cursor.execute() in
            # the same request (including the finally SET search_path below)
            # to raise InFailedSqlTransaction.
            with transaction.atomic():
                with connection.cursor() as cursor:
                    # Delete NotificationPreference from slack_integration
                    cursor.execute(
                        'DELETE FROM slack_integration_notificationpreference WHERE project_id = %s',
                        [project_id]
                    )
                    # Delete GoogleCalendar connections that reference this project's calendars
                    if calendar_ids:
                        cursor.execute(
                            f'DELETE FROM google_calendar_integration_googlecalendarconnection WHERE import_calendar_id IN ({",".join(["%s"] * len(calendar_ids))})',
                            calendar_ids
                        )
        except Exception:
            pass
        finally:
            # Restore tenant schema – the outer transaction is clean because any
            # SQL error above was contained within a savepoint.
            with connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO {original_path}')

        # Step 2: Delete project and all related records in tenant schema using raw SQL
        # ON DELETE CASCADE may not be set on all tables, so manually delete in correct order
        with transaction.atomic():
            with connection.cursor() as cursor:
                # Delete child records first (bottom-up approach)

                # Soft-delete calendars and null out project_id so that the
                # subsequent hard-delete of core_project does not leave a
                # dangling FK reference (mirrors on_delete=SET_NULL that
                # Django ORM would normally apply but is bypassed here).
                cursor.execute(
                    'UPDATE calendars_calendar SET is_deleted = TRUE, project_id = NULL WHERE project_id = %s',
                    [project_id]
                )

                # Delete project members
                cursor.execute(
                    'DELETE FROM core_projectmember WHERE project_id = %s',
                    [project_id]
                )

                # Delete tasks
                cursor.execute(
                    'DELETE FROM task_task WHERE project_id = %s',
                    [project_id]
                )

                # Delete meetings
                cursor.execute(
                    'DELETE FROM meetings_meeting WHERE project_id = %s',
                    [project_id]
                )

                # Delete any other potential child records
                # ProjectInvitation, ProjectRole, etc.
                try:
                    cursor.execute(
                        'DELETE FROM core_projectinvitation WHERE project_id = %s',
                        [project_id]
                    )
                except Exception:
                    pass

                # Finally delete the project itself
                cursor.execute(
                    'DELETE FROM core_project WHERE id = %s',
                    [project_id]
                )
        safe_emit_audit_event(
            event_type="project.deleted",
            actor=self.request.user,
            organization=instance.organization,
            project=None,
            target_type="project",
            target_id=project_id,
            before=project_snapshot,
            request=self.request,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        before = {
            "name": instance.name,
            "description": instance.description,
            "organization_id": instance.organization_id,
            "owner_id": instance.owner_id,
        }
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        instance.refresh_from_db()
        _audit_project_event(
            "project.updated",
            request,
            instance,
            before=before,
            after={
                "name": instance.name,
                "description": instance.description,
                "organization_id": instance.organization_id,
                "owner_id": instance.owner_id,
            },
        )
        return Response(serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def set_active(self, request, pk=None):
        """Set project as user's active project."""
        project = self.get_object()
        user = request.user

        # Verify user is a member
        membership = ProjectMember.objects.filter(
            user=user,
            project=project,
            is_active=True
        ).first()

        if not membership:
            return Response(
                {'error': 'You are not a member of this project'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Set as active project
        user.active_project = project
        user.save(update_fields=['active_project'])

        serializer = ProjectSummarySerializer(project, context={'request': request})
        return Response({
            'message': 'Active project updated successfully',
            'active_project': serializer.data
        })

    @action(detail=True, methods=['get'], url_path='decisions/graph')
    def decisions_graph(self, request, pk=None):
        """Return decision graph (nodes + edges) for one project or all member projects."""
        project = self.get_object()
        scope = request.query_params.get('scope')
        if scope == 'all_projects':
            if request.user.is_superuser:
                project_ids = Project.objects.filter(
                    organization=project.organization,
                    is_deleted=False,
                ).values_list('id', flat=True)
            else:
                project_ids = ProjectMember.objects.filter(
                    user=request.user,
                    is_active=True,
                    project__organization=project.organization,
                    project__is_deleted=False,
                ).values_list('project_id', flat=True)
        else:
            project_ids = [project.id]

        nodes_qs = Decision.objects.filter(project_id__in=project_ids, is_deleted=False).select_related('project').order_by(
            'project_id',
            'project_seq',
            'id',
        )
        edges_qs = DecisionEdge.objects.filter(
            from_decision__project_id__in=project_ids,
            to_decision__project_id__in=project_ids,
        )
        topic_labels = {
            label.topic: label.title
            for label in DecisionTopicLabel.objects.filter(
                project=project,
                is_deleted=False,
            )
        }
        nodes = DecisionGraphNodeSerializer(
            nodes_qs,
            many=True,
            context={"request": request, "topic_labels": topic_labels},
        ).data
        edges = DecisionEdgeSerializer(edges_qs, many=True).data
        topics = [
            {
                "topic": topic,
                "title": title,
                "defaultTitle": default_decision_topic_label(topic),
            }
            for topic, title in sorted(topic_labels.items(), key=lambda item: item[1].lower())
        ]
        return Response({"nodes": nodes, "edges": edges, "topics": topics})

    @action(
        detail=True,
        methods=['patch', 'post', 'delete'],
        url_path=r'decision-topic-labels/(?P<topic>[^/.]+)',
    )
    def decision_topic_label(self, request, pk=None, topic=None):
        """Create, rename, or remove a topic column title for this project view."""
        project = self.get_object()
        normalized_topic = normalize_decision_topic(topic)

        if request.method == 'DELETE':
            if Decision.objects.filter(
                project=project,
                topic=normalized_topic,
                is_deleted=False,
            ).exists():
                return Response(
                    {'detail': 'Only empty topics can be deleted.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            DecisionTopicLabel.objects.filter(
                project=project,
                topic=normalized_topic,
                is_deleted=False,
            ).update(is_deleted=True)
            return Response(status=status.HTTP_204_NO_CONTENT)

        title = (request.data.get('title') or '').strip()
        if not title:
            return Response(
                {'title': 'Title is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(title) > 80:
            return Response(
                {'title': 'Title must be 80 characters or fewer.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            label, _ = DecisionTopicLabel.objects.update_or_create(
                project=project,
                topic=normalized_topic,
                defaults={'title': title, 'is_deleted': False},
            )
            record_audit_entry(
                actor=self.request.user if self.request.user.is_authenticated else None,
                action='project.labels_updated',
                target=project,
                before=None,
                after={'topic': normalized_topic, 'title': title},
                organization=project.organization,
                project=project,
            )
        return Response(
            {
                'topic': normalized_topic,
                'title': label.title,
                'defaultTitle': default_decision_topic_label(normalized_topic),
            }
        )


class ProjectMemberViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing project members.
    Nested under projects.
    
    Endpoints:
    - GET /api/core/projects/{project_id}/members/ - List project members
    - POST /api/core/projects/{project_id}/members/ - Invite user to project
    - PATCH /api/core/projects/{project_id}/members/{id}/ - Update member role
    - DELETE /api/core/projects/{project_id}/members/{id}/ - Remove member
    """

    serializer_class = ProjectMemberSerializer
    permission_classes = [IsAuthenticated]
    owner_transfer_default_role = 'Team Leader'

    def get_queryset(self):
        """Filter members by project."""
        from core.utils.bot_user import AGENT_BOT_EMAIL

        project_id = self.kwargs.get('project_id')
        project = get_object_or_404(Project, id=resolve_project_pk(project_id))

        # Verify user is a member
        if not ProjectMember.objects.filter(
            user=self.request.user,
            project=project,
            is_active=True
        ).exists():
            return ProjectMember.objects.none()

        # Exclude bot users from member list
        # Bot users are system-managed and automatically added to projects,
        # they should not appear in the regular member list UI
        return ProjectMember.objects.filter(
            project=project,
            is_active=True
        ).exclude(
            user__email=AGENT_BOT_EMAIL  # Exclude agent bot user
        ).select_related('user', 'project')

    def get_permissions(self):
        """Set permissions based on action."""
        if self.action in ['update', 'partial_update', 'destroy']:
            # Restrict role changes and removals to privileged roles.
            return [IsAuthenticated(), CanManageProjectMembers()]
        # For list/retrieve/create: object-level management permissions are handled
        # inside the endpoints (create is owner-only).
        return [IsAuthenticated()]

    def _transfer_project_owner(self, instance, actor):
        project = instance.project
        previous_owner = project.owner
        new_owner = instance.user

        with transaction.atomic():
            if previous_owner and previous_owner.id != instance.user_id:
                previous_owner_membership = ProjectMember.objects.filter(
                    project=project,
                    user=previous_owner,
                    is_active=True,
                ).first()
                if previous_owner_membership and previous_owner_membership.role == 'owner':
                    previous_owner_membership.role = self.owner_transfer_default_role
                    previous_owner_membership.save(update_fields=['role'])

            project.owner = instance.user
            project.save(update_fields=['owner'])

            if instance.role != 'owner':
                instance.role = 'owner'
                instance.save(update_fields=['role'])

            ensure_project_calendar(project)

        self._notify_new_project_owner(project, new_owner, actor, previous_owner)

    def _notify_new_project_owner(self, project, new_owner, actor, previous_owner):
        if not new_owner or not actor:
            return
        try:
            from notifications.models import NotificationCategory, NotificationEventType  # noqa: PLC0415
            from notifications.services import create_notification  # noqa: PLC0415

            actor_display = actor.get_full_name() or actor.username
            previous_owner_display = None
            if previous_owner and previous_owner.id != new_owner.id:
                previous_owner_display = previous_owner.get_full_name() or previous_owner.username

            create_notification(
                recipient_id=new_owner.id,
                actor_id=actor.id,
                category=NotificationCategory.COLLABORATION,
                event_type=NotificationEventType.ACCOUNT_PERMISSION,
                title=f"You are now the owner of project: {project.name}",
                body=(
                    f"{actor_display} transferred project ownership of "
                    f"\"{project.name}\" to you."
                ),
                related_object_type="project",
                related_object_id=str(project.id),
                action_url=overview_action_url(),
                metadata={
                    "project_name": project.name,
                    "project_id": project.id,
                    "action": "project_owner_transferred",
                    "previous_owner": previous_owner_display,
                },
            )
        except Exception:
            logger.exception(
                "Failed to send project owner transfer notification for user %s",
                getattr(new_owner, "id", None),
            )

    def update(self, request, *args, **kwargs):
        partial = kwargs.get('partial', False)
        instance = self.get_object()
        before = {
            "user_id": instance.user_id,
            "user_email": instance.user.email,
            "role": instance.role,
            "is_active": instance.is_active,
        }
        requested_role = request.data.get('role')
        if requested_role == 'owner':
            self._transfer_project_owner(instance, request.user)
            instance.refresh_from_db()
            _audit_project_event(
                "project_member.role_changed",
                request,
                instance.project,
                target_type="project_member",
                target_id=instance.id,
                before=before,
                after={
                    "user_id": instance.user_id,
                    "user_email": instance.user.email,
                    "role": instance.role,
                    "is_active": instance.is_active,
                    "ownership_transferred": True,
                },
            )
            return Response(self.get_serializer(instance).data)
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        desired_role = serializer.validated_data.get('role')
        if desired_role == 'owner':
            self._transfer_project_owner(instance, request.user)
            instance.refresh_from_db()
            _audit_project_event(
                "project_member.role_changed",
                request,
                instance.project,
                target_type="project_member",
                target_id=instance.id,
                before=before,
                after={
                    "user_id": instance.user_id,
                    "user_email": instance.user.email,
                    "role": instance.role,
                    "is_active": instance.is_active,
                    "ownership_transferred": True,
                },
            )
            return Response(self.get_serializer(instance).data)

        if (
            desired_role
            and instance.role == 'owner'
            and instance.project.owner_id == instance.user_id
            and desired_role != 'owner'
        ):
            raise ValidationError(
                {'role': 'Transfer project ownership before changing the owner role.'}
            )

        response = super().update(request, *args, **kwargs)
        instance.refresh_from_db()
        _audit_project_event(
            "project_member.role_changed",
            request,
            instance.project,
            target_type="project_member",
            target_id=instance.id,
            before=before,
            after={
                "user_id": instance.user_id,
                "user_email": instance.user.email,
                "role": instance.role,
                "is_active": instance.is_active,
            },
        )
        return response

    def check_object_permissions(self, request, obj):
        """Override to check project permissions for member objects."""
        # For destroy action, ensure user has a privileged project role.
        if self.action == 'destroy':
            project = obj.project
            if not can_manage_project_members(request.user, project):
                raise PermissionDenied('Only privileged project roles can remove members')
        super().check_object_permissions(request, obj)

    def create(self, request, *args, **kwargs):
        """Invite user to project."""
        project_id = self.kwargs.get('project_id')
        project = get_object_or_404(Project, id=resolve_project_pk(project_id))

        # Invite is owner-only: verify actor is the authoritative project owner
        user = request.user
        if not can_invite_project_members(user, project):
            raise PermissionDenied('Only project owner can invite members')

        # Use ProjectMemberInviteSerializer for validation
        invite_serializer = ProjectMemberInviteSerializer(
            data=request.data,
            context={"request": request},
        )
        invite_serializer.is_valid(raise_exception=True)

        email = invite_serializer.validated_data['email']
        role = invite_serializer.validated_data.get('role', 'member')

        # Check if user exists
        try:
            invited_user = User.objects.get(email=email)
        except User.DoesNotExist:
            invited_user = None

        if invited_user and ProjectMember.objects.filter(
            user=invited_user,
            project=project,
            is_active=True,
        ).exists():
            raise ValidationError({
                'email': 'User is already a member of this project'
            })

        # Seat cap check: enforce org's purchased seat limit before creating the invitation.
        org = project.organization
        if org:
            from stripe_meta.services import get_active_real_subscription  # noqa: PLC0415
            sub = get_active_real_subscription(org)
            if sub:
                current_count = User.objects.filter(organization=org).count()
                if sub.seat_count <= current_count:
                    is_free = sub.is_internal
                    return Response(
                        {
                            'error': (
                                'Free plan is limited to 1 seat. Upgrade to Team to add more members.'
                                if is_free
                                else 'Seat limit reached. Purchase more seats to add more members.'
                            ),
                            'code': 'SEAT_LIMIT_REACHED',
                            'seats_available': 0,
                            'seats_purchased': sub.seat_count,
                            'upgrade_required': is_free,
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )

        try:
            invitation = create_project_invitation(
                email=email,
                project=project,
                invited_by=user,
                role=role,
                auto_approve=False
            )

            # Immediately notify the invited user (if they already have an account)
            # so they see it in the notification panel without waiting for email acceptance.
            if invited_user and invited_user.id != user.id:
                try:
                    from notifications.models import NotificationCategory, NotificationEventType  # noqa: PLC0415
                    from notifications.services import create_notification  # noqa: PLC0415
                    create_notification(
                        recipient_id=invited_user.id,
                        actor_id=user.id,
                        category=NotificationCategory.COLLABORATION,
                        event_type=NotificationEventType.PROJECT_INVITE,
                        title=f"You've been invited to project: {project.name}",
                        body=f"{user.get_full_name() or user.username} invited you to join \"{project.name}\".",
                        related_object_type="project",
                        related_object_id=str(project.id),
                        action_url=overview_action_url(),
                        metadata={
                            "project_name": project.name,
                            "invitation_id": invitation.pk,
                        },
                    )
                except Exception:
                    logger.exception(
                        "Failed to send PROJECT_INVITE notification for user %s", invited_user.id
                    )

            invitation_serializer = ProjectInvitationSerializer(invitation, context={'request': request})
            _audit_project_event(
                "project_member.invited",
                request,
                project,
                target_type="project_invitation",
                target_id=invitation.id,
                after={
                    "email": invitation.email,
                    "role": invitation.role,
                    "approved": invitation.approved,
                    "accepted": invitation.accepted,
                    "user_exists": invited_user is not None,
                },
            )
            message = 'Invitation created and pending owner approval.'
            return Response(
                {
                    'message': message,
                    'invitation': invitation_serializer.data,
                    'user_exists': invited_user is not None
                },
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            logger.error(f"Failed to create invitation: {e}", exc_info=True)
            raise ValidationError({
                'email': f'Failed to send invitation: {str(e)}'
            })

    def perform_destroy(self, instance):
        """Remove member from project."""
        # Only the authoritative project owner (project.owner_id) cannot be removed.
        # Co-owners (role='owner' but not project.owner) can be removed normally.
        if instance.user_id == instance.project.owner_id:
            raise ValidationError({
                'error': 'Cannot remove the project owner. Transfer ownership first.'
            })

        removed_user = instance.user
        project = instance.project
        actor = self.request.user

        # Deactivate instead of delete
        instance.is_active = False
        instance.save()
        _audit_project_event(
            "project_member.removed",
            self.request,
            project,
            target_type="project_member",
            target_id=instance.id,
            before={
                "user_id": removed_user.id,
                "user_email": removed_user.email,
                "role": instance.role,
                "is_active": True,
            },
            after={
                "user_id": removed_user.id,
                "user_email": removed_user.email,
                "role": instance.role,
                "is_active": False,
            },
        )

        # Notify the removed user (skip self-removal)
        if removed_user.id != actor.id:
            try:
                from notifications.models import NotificationCategory, NotificationEventType  # noqa: PLC0415
                from notifications.services import create_notification, revoke_access_to_resource  # noqa: PLC0415
                create_notification(
                    recipient_id=removed_user.id,
                    actor_id=actor.id,
                    category=NotificationCategory.COLLABORATION,
                    event_type=NotificationEventType.ACCOUNT_PERMISSION,
                    title=f"Removed from project: {project.name}",
                    body=f"You have been removed from the project \"{project.name}\".",
                    related_object_type="project",
                    related_object_id=str(project.id),
                    action_url=overview_action_url(),
                    metadata={
                        "project_name": project.name,
                        "action": "removed_from_project",
                        "revoked_access": True,  # User no longer has access to this project
                    },
                )
                # Revoke access to all historical project notifications
                revoke_access_to_resource(
                    user_id=removed_user.id,
                    object_type="project",
                    object_id=str(project.id)
                )
            except Exception:
                logger.exception(
                    "Failed to send removal notification for user %s", removed_user.id
                )


class ListProjectAvailableRolesView(APIView):
    """
    Return available project roles for the current organization.

    Used by frontend role dropdowns:
    - Invitation role selection (owner is not included)
    - Member role editing (owner is also excluded; ownership transfer is separate)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, project_id: int):
        project = get_object_or_404(Project, id=resolve_project_pk(project_id))

        # Prevent leaking role definitions to non-members.
        if not ProjectMember.objects.filter(
            user=request.user,
            project=project,
            is_active=True,
        ).exists():
            return Response(
                {'error': 'You do not have access to this project'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Keep role names in sync with frontend `getRoleBadgeClasses()` switch cases.
        # `owner` is excluded from dropdown responses.
        base_roles = {
            "member",
            "viewer",
            "Approver",
            "Reviewer",
            "Super Administrator",
            "Organization Admin",
            "Team Leader",
            "Campaign Manager",
            "Budget Controller",
            "Data Analyst",
            "Senior Media Buyer",
            "Specialist Media Buyer",
            "Junior Media Buyer",
            "Designer",
            "Copywriter",
        }

        # Use current_organization for multi-org support
        user_org = getattr(request.user, "current_organization", None)
        if not user_org:
            # Fallback to legacy organization field
            user_org = getattr(request.user, "organization", None)

        role_qs = Role.objects.filter(is_deleted=False)
        if user_org:
            role_qs = role_qs.filter(Q(organization=user_org) | Q(organization__isnull=True))
        else:
            role_qs = role_qs.filter(organization__isnull=True)

        role_names = set(role_qs.values_list("name", flat=True))

        allowed_roles = (base_roles | role_names) - {"owner"}

        roles = [
            {"value": role_name, "label": role_name}
            for role_name in sorted(allowed_roles)
        ]
        return Response(
            {
                "roles": roles,
                "default_role": "member",
            },
            status=status.HTTP_200_OK,
        )


class AcceptInvitationView(APIView):
    """
    Accept a project invitation.
    If user doesn't exist, creates a new user account.
    """

    permission_classes = []  # Public endpoint

    def post(self, request):
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data['token']
        password = serializer.validated_data.get('password')
        username = serializer.validated_data.get('username')

        # Check if user is authenticated
        user = request.user if request.user.is_authenticated else None

        # If user is authenticated, verify email matches invitation
        if user:
            try:
                invitation = ProjectInvitation.objects.get(token=token, accepted=False)
                if invitation.email != user.email:
                    return Response(
                        {
                            'error': 'Invitation email does not match your account email',
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            except ProjectInvitation.DoesNotExist:
                return Response(
                    {'error': 'Invalid or already accepted invitation token'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            invitation, user, user_created = accept_invitation(
                token=token,
                user=user,
                password=password,
                username=username,
            )

            # Generate tokens for new users
            from core.services.auth_tokens import build_user_refresh_token

            refresh = build_user_refresh_token(user)
            from core.serializers import UserSummarySerializer

            user_data = UserSummarySerializer(user).data

            response_data = {
                'message': 'Invitation accepted successfully',
                'user': user_data,
                'project': ProjectSummarySerializer(invitation.project, context={'request': request}).data,
                'user_created': user_created,
            }

            if user_created:
                response_data['token'] = str(refresh.access_token)
                response_data['refresh'] = str(refresh)

            return Response(response_data, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error accepting invitation: {e}", exc_info=True)
            return Response(
                {'error': 'Failed to accept invitation. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ResendInvitationView(APIView):
    """Resend an invitation email."""

    permission_classes = [IsAuthenticated]

    def post(self, request, invitation_id):
        try:
            invitation = ProjectInvitation.objects.get(id=invitation_id)

            # Verify user has permission (must be project member)
            if not ProjectMember.objects.filter(
                user=request.user,
                project=invitation.project,
                is_active=True,
            ).exists():
                return Response(
                    {'error': 'You do not have permission to resend this invitation'},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # Check if already accepted
            if invitation.accepted:
                return Response(
                    {'error': 'Invitation has already been accepted'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not invitation.approved:
                return Response(
                    {'error': 'Invitation is pending owner approval'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Resend email
            send_invitation_email(invitation)

            return Response(
                {
                    'message': 'Invitation email resent successfully',
                    'invitation': ProjectInvitationSerializer(invitation).data,
                },
                status=status.HTTP_200_OK,
            )

        except ProjectInvitation.DoesNotExist:
            return Response(
                {'error': 'Invitation not found'},
                status=status.HTTP_404_NOT_FOUND,
            )


class ListProjectInvitationsView(APIView):
    """List pending invitations for a project."""

    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(Project, id=resolve_project_pk(project_id))

        # Verify user is a project member
        if not ProjectMember.objects.filter(
            user=request.user,
            project=project,
            is_active=True,
        ).exists():
            return Response(
                {'error': 'You do not have access to this project'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Get pending invitations
        invitations = ProjectInvitation.objects.filter(
            project=project,
            accepted=False,
            approved=True,
        ).select_related('invited_by', 'project').order_by('-created_at')

        serializer = ProjectInvitationSerializer(invitations, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class ListMyProjectInvitationsView(APIView):
    """List pending invitations for the authenticated user."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        project_id = request.query_params.get('project_id')
        invitations = ProjectInvitation.objects.filter(
            email=request.user.email,
            accepted=False,
            expires_at__gt=timezone.now(),
        ).select_related('invited_by', 'project').order_by('-created_at')

        if project_id:
            resolved_pid = resolve_project_pk(project_id)
            invitations = invitations.filter(project_id=resolved_pid) if resolved_pid else invitations.none()

        serializer = ProjectInvitationSerializer(invitations, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class ListPendingInvitationApprovalsView(APIView):
    """List invitations awaiting owner approval."""

    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = get_object_or_404(Project, id=resolve_project_pk(project_id))

        if not can_manage_project_members(request.user, project):
            return Response(
                {'error': 'Only privileged project roles can review invitations'},
                status=status.HTTP_403_FORBIDDEN,
            )

        invitations = ProjectInvitation.objects.filter(
            project=project,
            accepted=False,
            approved=False,
        ).select_related('invited_by', 'project').order_by('-created_at')

        serializer = ProjectInvitationSerializer(invitations, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class ApproveProjectInvitationView(APIView):
    """Approve a pending invitation and send the invite email."""

    permission_classes = [IsAuthenticated]

    def post(self, request, project_id, invitation_id):
        project = get_object_or_404(Project, id=resolve_project_pk(project_id))

        if not can_manage_project_members(request.user, project):
            return Response(
                {'error': 'Only privileged project roles can approve invitations'},
                status=status.HTTP_403_FORBIDDEN,
            )

        invitation = get_object_or_404(ProjectInvitation, id=invitation_id, project=project)

        if invitation.accepted:
            return Response(
                {'error': 'Invitation has already been accepted'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not invitation.approved:
            # Seat-cap check + approve must be atomic: select_for_update on the
            # subscription row serializes concurrent approvals, and approved-but-
            # unaccepted invitations count toward the cap (each one is a seat that
            # WILL be consumed at accept). Without both, two approvals at cap-1
            # both pass — the member count alone doesn't change at approval time.
            org = project.organization
            if org:
                from stripe_meta.models import Subscription  # noqa: PLC0415
                with transaction.atomic():
                    sub = (
                        Subscription.objects.select_for_update()
                        .filter(organization=org, is_active=True)
                        .order_by('is_internal')
                        .select_related('plan')
                        .first()
                    )
                    if sub:
                        member_count = User.objects.filter(organization=org).count()
                        # Approved invitations not yet accepted, excluding emails of
                        # existing org members (accepting those consumes no new seat).
                        pending_seats = (
                            ProjectInvitation.objects.filter(
                                project__organization=org,
                                approved=True,
                                accepted=False,
                            )
                            .exclude(
                                email__in=User.objects.filter(organization=org).values_list('email', flat=True)
                            )
                            .count()
                        )
                        if sub.seat_count <= member_count + pending_seats:
                            is_free = sub.is_internal
                            return Response(
                                {
                                    'error': (
                                        'Free plan is limited to 1 seat. Upgrade to Team to add more members.'
                                        if is_free
                                        else 'Seat limit reached. Purchase more seats to add more members.'
                                    ),
                                    'code': 'SEAT_LIMIT_REACHED',
                                    'seats_available': 0,
                                    'seats_purchased': sub.seat_count,
                                    'upgrade_required': is_free,
                                },
                                status=status.HTTP_403_FORBIDDEN,
                            )
                    invitation.approved = True
                    invitation.approved_by = request.user
                    invitation.approved_at = timezone.now()
                    invitation.save(update_fields=['approved', 'approved_by', 'approved_at'])
            else:
                invitation.approved = True
                invitation.approved_by = request.user
                invitation.approved_at = timezone.now()
                invitation.save(update_fields=['approved', 'approved_by', 'approved_at'])
            send_invitation_email(invitation)

        serializer = ProjectInvitationSerializer(invitation, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class RejectProjectInvitationView(APIView):
    """Reject a pending invitation."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, project_id, invitation_id):
        project = get_object_or_404(Project, id=resolve_project_pk(project_id))

        if not can_manage_project_members(request.user, project):
            return Response(
                {'error': 'Only privileged project roles can reject invitations'},
                status=status.HTTP_403_FORBIDDEN,
            )

        invitation = get_object_or_404(ProjectInvitation, id=invitation_id, project=project)

        if invitation.accepted:
            return Response(
                {'error': 'Invitation has already been accepted'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invitation.delete()
        return Response({'message': 'Invitation rejected'}, status=status.HTTP_200_OK)


# ============================================================================
# Multi-Organization Management Views
# ============================================================================


class UserOrganizationsView(APIView):
    """List all organizations the user belongs to."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        GET /api/core/organizations/
        Returns list of organizations with membership details and current org marker.
        """
        user = request.user
        organizations = get_user_organizations(user)

        serializer = OrganizationSerializer(
            organizations,
            many=True,
            context={'request': request}
        )

        return Response({
            'organizations': serializer.data,
            'current_organization_id': user.current_organization_id,
        })


class OrganizationDetailView(APIView):
    """Retrieve detailed info for a single organization (org info + subscription + usage)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, org_id):
        """
        GET /api/core/organizations/<org_id>/
        Returns org basic info, membership details, subscription plan, and current-month usage.
        """
        user = request.user
        resolved_id = resolve_pk_for(Organization, org_id)

        if not can_user_access_organization(user, resolved_id):
            return Response(
                {'error': 'You do not have access to this organization.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        org = get_object_or_404(Organization, id=resolved_id)
        org_data = OrganizationSerializer(org, context={'request': request}).data

        # ── Subscription & plan ──────────────────────────────────────────────
        from stripe_meta.models import Subscription, UsageMonthly  # noqa: PLC0415

        subscription_data = None
        # Prefer a real paid subscription (is_internal=False); fall back to the
        # Free sentinel (is_internal=True) so every org shows its plan & quota.
        # order_by('is_internal') puts False(0) before True(1).
        sub = (
            Subscription.objects.select_related('plan')
            .filter(organization=org, is_active=True)
            .order_by('is_internal')
            .first()
        )
        if sub:
            plan = sub.plan
            subscription_data = {
                'is_active': sub.is_active,
                'seat_count': sub.seat_count,
                'start_date': sub.start_date.isoformat() if sub.start_date else None,
                'end_date': sub.end_date.isoformat() if sub.end_date else None,
                'cancel_at_period_end': sub.cancel_at_period_end,
                'plan': {
                    'name': plan.name,
                    'desc': plan.desc,
                    'base_price_cents': plan.base_price_cents,
                    'included_seats': plan.included_seats,
                    'monthly_token_quota': plan.monthly_token_quota,
                    'currency': plan.currency,
                },
            }

        # ── Current-month usage ──────────────────────────────────────────────
        current_month = timezone.now().strftime('%Y-%m')
        usage_record = UsageMonthly.objects.filter(
            organization=org, year_month=current_month
        ).first()

        usage_data = None
        if usage_record:
            usage_data = {
                'tokens_used': usage_record.tokens_used,
                'tokens_reserved': usage_record.tokens_reserved,
                'overage_tokens': usage_record.overage_tokens,
                'updated_at': usage_record.updated_at.isoformat() if usage_record.updated_at else None,
            }

        # ── Usage breakdown by call_purpose ─────────────────────────────────
        from stripe_meta.services import get_usage_breakdown  # noqa: PLC0415
        usage_breakdown = get_usage_breakdown(org, current_month)

        # ── Recent org-level activity ────────────────────────────────────────
        from core.services.organization_activity import get_recent_org_activity  # noqa: PLC0415
        activity_qs = get_recent_org_activity(org_id=org.id, limit=15)
        activity_data = OrganizationActivityEventSerializer(activity_qs, many=True).data

        return Response({
            **org_data,
            'subscription': subscription_data,
            'usage': usage_data,
            'usage_breakdown': usage_breakdown,
            'recent_activity': activity_data,
        })

    def patch(self, request, org_id):
        """
        PATCH /api/core/organizations/<org_id>/
        Updates organization settings (admin only).
        Currently supports: max_concurrent_sessions
        """
        user = request.user
        resolved_id = resolve_pk_for(Organization, org_id)

        if not can_user_access_organization(user, resolved_id):
            return Response(
                {'error': 'You do not have access to this organization.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        org = get_object_or_404(Organization, id=resolved_id)

        original_org_id = user.current_organization_id
        user.current_organization_id = resolved_id
        if not is_org_admin(user):
            user.current_organization_id = original_org_id
            return Response({'error': 'Only organization admins can update settings.'}, status=status.HTTP_403_FORBIDDEN)
        user.current_organization_id = original_org_id

        cap = request.data.get('max_concurrent_sessions')
        if cap is None:
            return Response({'error': 'max_concurrent_sessions is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            cap = int(cap)
            if cap < 1:
                raise ValueError
        except (ValueError, TypeError):
            return Response({'error': 'max_concurrent_sessions must be a positive integer.'}, status=status.HTTP_400_BAD_REQUEST)

        org.max_concurrent_sessions = cap
        org.save(update_fields=['max_concurrent_sessions', 'updated_at'])
        return Response({'max_concurrent_sessions': org.max_concurrent_sessions})

    def delete(self, request, org_id):
        """
        DELETE /api/core/organizations/<org_id>/
        Soft-deletes an organization (admin only).
        Fails if this is the requesting user's last active organization,
        unless ?force=true is passed (used by onboarding undo flow).
        """
        user = request.user
        org_id = resolve_pk_for(Organization, org_id)
        force = request.query_params.get('force', '').lower() == 'true'

        if not can_user_access_organization(user, org_id):
            return Response(
                {'error': 'You do not have access to this organization.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        org = get_object_or_404(Organization, id=org_id, is_active=True)

        # Temporarily set current_organization to this org so is_org_admin works
        original_org_id = user.current_organization_id
        user.current_organization_id = org_id
        if not is_org_admin(user):
            user.current_organization_id = original_org_id
            raise PermissionDenied('Only organization admins can delete the organization.')
        user.current_organization_id = original_org_id

        # Ensure the user has at least one other active org (skip when force=true)
        if not force:
            other_orgs = get_user_organizations(user).filter(is_active=True).exclude(id=org_id)
            if not other_orgs.exists():
                return Response(
                    {'error': 'You must belong to at least one organization. Please join or create another organization before deleting this one.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        before = capture_snapshot(org)

        with transaction.atomic():
            # Soft-delete the organization
            org.is_active = False
            org.save(update_fields=['is_active', 'updated_at'])

            record_audit_entry(
                actor=user if user.is_authenticated else None,
                action='org.deleted',
                target=org,
                before=before,
                after=None,
                organization=org,
            )

        # Switch current_organization for every user who had this as their active org
        User = get_user_model()
        affected_users = User.objects.filter(current_organization_id=org_id)
        for affected_user in affected_users:
            alt = get_user_organizations(affected_user).filter(is_active=True).exclude(id=org_id).first()
            affected_user.current_organization_id = alt.id if alt else None
            affected_user.save(update_fields=['current_organization_id'])

        return Response({'message': f'Organization "{org.name}" has been deleted.'}, status=status.HTTP_200_OK)


class UpdateOrganizationSlugView(APIView):
    """
    PATCH /api/core/organizations/<org_id>/slug/
    Admin-only: rename an organization's slug and the underlying tenant schema.
    """

    permission_classes = [IsAuthenticated]

    def patch(self, request, org_id):
        from django.core.cache import cache  # noqa: PLC0415
        from django.db import transaction  # noqa: PLC0415
        from django.utils.text import slugify  # noqa: PLC0415
        from core.services.tenant import rename_tenant_schema  # noqa: PLC0415

        user = request.user
        org_id = resolve_pk_for(Organization, org_id)

        if not can_user_access_organization(user, org_id):
            return Response(
                {'error': 'You do not have access to this organization.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        org = get_object_or_404(Organization, id=org_id, is_active=True)

        # Temporarily set current_organization so is_org_admin works
        original_org_id = user.current_organization_id
        user.current_organization_id = org_id
        if not is_org_admin(user):
            user.current_organization_id = original_org_id
            raise PermissionDenied('Only organization admins can update the slug.')
        user.current_organization_id = original_org_id

        new_slug = request.data.get('slug', '').strip().lower()

        if not new_slug:
            return Response(
                {'error': 'Slug is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate slug format: lowercase letters, digits, hyphens only
        if slugify(new_slug) != new_slug:
            return Response(
                {'error': 'Slug may only contain lowercase letters, numbers, and hyphens.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_slug == org.slug:
            return Response({'slug': org.slug, 'message': 'No change.'})

        # Uniqueness check
        if Organization.objects.filter(slug=new_slug).exclude(pk=org.pk).exists():
            return Response(
                {'error': 'This slug is already taken. Please choose another.'},
                status=status.HTTP_409_CONFLICT,
            )

        old_slug = org.slug

        try:
            with transaction.atomic():
                # 1. Rename PostgreSQL schema atomically with the slug update
                rename_tenant_schema(old_slug, new_slug)

                # 2. Update the org record (is_new is False, so provision_tenant_schema
                #    is NOT called again — only save() triggers it for new orgs)
                org.slug = new_slug
                org.save(update_fields=['slug', 'updated_at'])

                record_audit_entry(
                    actor=user if user.is_authenticated else None,
                    action='org.slug_updated',
                    target=org,
                    before={'slug': old_slug},
                    after={'slug': new_slug},
                    organization=org,
                )
        except Exception as exc:
            return Response(
                {'error': f'Failed to rename slug: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # 3. Invalidate middleware caches so the next request uses the new schema
        cache.delete(f'tenant:slug:{org_id}')
        cache.delete(f'tenant:valid:{old_slug}')

        from core.services.organization_activity import log_org_activity  # noqa: PLC0415
        log_org_activity(
            org,
            'org_slug_changed',
            actor=user,
            metadata={'old_slug': old_slug, 'new_slug': new_slug},
        )

        return Response({'slug': new_slug, 'message': 'Slug updated successfully.'})


class SwitchOrganizationView(APIView):
    """Switch the user's current organization."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """
        POST /api/core/organizations/switch/
        Body: {"organization_id": 123}
        Switches current_organization and returns updated user info.
        """
        serializer = SwitchOrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        organization_id = serializer.validated_data['organization_id']
        user = request.user

        # Switch organization using admin_utils function
        success, error = switch_organization(user, organization_id)

        if not success:
            return Response(
                {'error': error},
                status=status.HTTP_403_FORBIDDEN
            )

        # Refresh user to get updated current_organization
        user.refresh_from_db()

        return Response({
            'message': 'Organization switched successfully',
            'current_organization_id': user.current_organization_id,
            'current_organization': {
                'id': user.current_organization.id,
                'name': user.current_organization.name,
                'slug': user.current_organization.slug,
            } if user.current_organization else None,
        })


class OrganizationMembersView(APIView):
    """View organization members (admin only)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, org_id):
        """
        GET /api/core/organizations/{org_id}/members/
        Returns list of members for the organization (any member can view).
        """
        user = request.user
        org_id = resolve_pk_for(Organization, org_id)
        organization = get_object_or_404(Organization, id=org_id)

        # Check if user has access to this organization (any member may view)
        if not can_user_access_organization(user, org_id):
            raise PermissionDenied("You do not have access to this organization")

        # Get all active memberships for this organization
        memberships = OrganizationMembership.objects.filter(
            organization=organization,
            is_active=True
        ).select_related('user', 'invited_by').order_by('-joined_at')

        serializer = OrganizationMembershipSerializer(
            memberships,
            many=True,
            context={'request': request}
        )

        return Response({
            'organization': {
                'id': organization.id,
                'name': organization.name,
                'slug': organization.slug,
            },
            'members': serializer.data,
            'member_count': memberships.count(),
        })


class RemoveOrganizationMemberView(APIView):
    """Remove a member from the organization (admin only)."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, org_id, user_id):
        """
        DELETE /api/core/organizations/{org_id}/members/{user_id}/
        Removes a member from the organization (admin only).
        Cannot remove yourself.
        """
        user = request.user
        org_id = resolve_pk_for(Organization, org_id)
        organization = get_object_or_404(Organization, id=org_id)
        target_user = get_object_or_404(User, id=user_id)

        # Check if requesting user has access to this organization
        if not can_user_access_organization(user, org_id):
            raise PermissionDenied("You do not have access to this organization")

        # Check if requesting user is admin
        original_org = user.current_organization_id
        user.current_organization_id = org_id

        if not is_org_admin(user):
            user.current_organization_id = original_org
            raise PermissionDenied("Only organization admins can remove members")

        user.current_organization_id = original_org

        # Cannot remove yourself
        if user.id == target_user.id:
            return Response(
                {'error': 'You cannot remove yourself from the organization. Use the leave endpoint instead.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get the membership
        membership = get_object_or_404(
            OrganizationMembership,
            user=target_user,
            organization=organization,
            is_active=True
        )

        # Deactivate the membership
        membership.is_active = False
        membership.save(update_fields=['is_active', 'updated_at'])

        # If the removed user's current_organization is this org, switch to another org
        if target_user.current_organization_id == org_id:
            other_orgs = get_user_organizations(target_user).exclude(id=org_id)
            if other_orgs.exists():
                target_user.current_organization_id = other_orgs.first().id
                target_user.save(update_fields=['current_organization_id'])
            else:
                target_user.current_organization_id = None
                target_user.save(update_fields=['current_organization_id'])

        log_org_activity(
            organization,
            'member_removed',
            actor=user,
            target_user=target_user,
            metadata={'role': membership.role},
        )

        return Response({
            'message': f'User {target_user.username} removed from organization',
        })


class LeaveOrganizationView(APIView):
    """Leave an organization."""

    permission_classes = [IsAuthenticated]

    def post(self, request, org_id):
        """
        POST /api/core/organizations/{org_id}/leave/
        Leave an organization. Fails if user owns projects in this organization.
        """
        user = request.user
        org_id = resolve_pk_for(Organization, org_id)
        organization = get_object_or_404(Organization, id=org_id)

        # Check if user has membership in this organization
        membership = OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
            is_active=True
        ).first()

        if not membership:
            return Response(
                {'error': 'You are not a member of this organization'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if user owns any projects in this organization
        owned_projects = Project.objects.filter(
            organization=organization,
            owner=user,
            is_deleted=False
        )

        if owned_projects.exists():
            project_names = list(owned_projects.values_list('name', flat=True))
            return Response(
                {
                    'error': 'You cannot leave this organization because you own projects in it',
                    'owned_projects': project_names,
                    'message': 'Please transfer ownership of these projects before leaving',
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # Deactivate membership
        membership.is_active = False
        membership.save(update_fields=['is_active', 'updated_at'])

        log_org_activity(
            organization,
            'member_left',
            actor=user,
            target_user=user,
            metadata={'role': membership.role},
        )

        # Remove all project memberships in this organization
        ProjectMember.objects.filter(
            user=user,
            project__organization=organization
        ).update(is_active=False)

        # If this was the current organization, switch to another
        if user.current_organization_id == org_id:
            other_orgs = get_user_organizations(user)
            if other_orgs.exists():
                success, error = switch_organization(user, other_orgs.first().id)
                if not success:
                    # Fallback: set to None if switch fails
                    user.current_organization_id = None
                    user.save(update_fields=['current_organization_id'])
            else:
                user.current_organization_id = None
                user.save(update_fields=['current_organization_id'])

        return Response({
            'message': f'You have left {organization.name}',
            'current_organization_id': user.current_organization_id,
        })


# ============================================================================
# Organization Invitation Management Views
# ============================================================================


class CreateOrganizationInvitationView(APIView):
    """Create organization invitation (admin only)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, org_id):
        """
        POST /api/core/organizations/{org_id}/invitations/
        Body: {
            "email": "user@example.com",  // optional - null for open invitation
            "role": "member",
            "max_uses": 1,  // 0 = unlimited
            "expires_days": 7
        }
        """
        from core.utils.org_invitations import create_org_invitation

        user = request.user
        org_id = resolve_pk_for(Organization, org_id)
        organization = get_object_or_404(Organization, id=org_id)

        # Check if user has access to this organization
        if not can_user_access_organization(user, org_id):
            raise PermissionDenied("You do not have access to this organization")

        # Check if user is admin
        original_org = user.current_organization_id
        user.current_organization_id = org_id

        if not is_org_admin(user):
            user.current_organization_id = original_org
            raise PermissionDenied("Only organization admins can create invitations")

        user.current_organization_id = original_org

        # Parse request data
        email = request.data.get('email')  # Can be None for open invitations
        role = request.data.get('role', 'member')
        max_uses = request.data.get('max_uses', 1)
        expires_days = request.data.get('expires_days', 7)

        # Validate role
        if role not in ['admin', 'member', 'viewer']:
            return Response(
                {'error': f'Invalid role: {role}. Must be admin, member, or viewer.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate max_uses
        if not isinstance(max_uses, int) or max_uses < 0:
            return Response(
                {'error': 'max_uses must be a non-negative integer'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create invitation
        invitation = create_org_invitation(
            organization=organization,
            invited_by=user,
            role=role,
            email=email,
            max_uses=max_uses,
            expires_days=expires_days,
        )

        serializer = OrganizationInvitationSerializer(
            invitation,
            context={'request': request}
        )

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ListOrganizationInvitationsView(APIView):
    """List organization invitations (admin only)."""

    permission_classes = [IsAuthenticated]

    def get(self, request, org_id):
        """
        GET /api/core/organizations/{org_id}/invitations/
        Returns list of all active invitations for the organization.
        """
        user = request.user
        org_id = resolve_pk_for(Organization, org_id)
        organization = get_object_or_404(Organization, id=org_id)

        # Check if user has access to this organization
        if not can_user_access_organization(user, org_id):
            raise PermissionDenied("You do not have access to this organization")

        # Check if user is admin
        original_org = user.current_organization_id
        user.current_organization_id = org_id

        if not is_org_admin(user):
            user.current_organization_id = original_org
            raise PermissionDenied("Only organization admins can view invitations")

        user.current_organization_id = original_org

        # Get all invitations
        invitations = OrganizationInvitation.objects.filter(
            organization=organization,
            is_deleted=False
        ).select_related('invited_by').order_by('-created_at')

        serializer = OrganizationInvitationSerializer(
            invitations,
            many=True,
            context={'request': request}
        )

        return Response({
            'organization': {
                'id': organization.id,
                'name': organization.name,
            },
            'invitations': serializer.data,
            'count': invitations.count(),
        })


class RevokeOrganizationInvitationView(APIView):
    """Revoke/deactivate an organization invitation (admin only)."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, org_id, invitation_id):
        """
        DELETE /api/core/organizations/{org_id}/invitations/{invitation_id}/
        Deactivates the invitation.
        """
        user = request.user
        org_id = resolve_pk_for(Organization, org_id)
        organization = get_object_or_404(Organization, id=org_id)

        # Check if user has access to this organization
        if not can_user_access_organization(user, org_id):
            raise PermissionDenied("You do not have access to this organization")

        # Check if user is admin
        original_org = user.current_organization_id
        user.current_organization_id = org_id

        if not is_org_admin(user):
            user.current_organization_id = original_org
            raise PermissionDenied("Only organization admins can revoke invitations")

        user.current_organization_id = original_org

        # Get the invitation
        invitation = get_object_or_404(
            OrganizationInvitation,
            id=invitation_id,
            organization=organization
        )

        # Deactivate it
        invitation.is_active = False
        invitation.save(update_fields=['is_active', 'updated_at'])

        return Response({
            'message': 'Invitation revoked successfully'
        })


class AcceptOrganizationInvitationView(APIView):
    """Accept an organization invitation (public endpoint)."""

    permission_classes = []  # Public endpoint

    def post(self, request):
        """
        POST /api/core/invitations/accept-organization/
        Body: {
            "token": "invitation-token",
            "password": "password123",  // required for new users
            "username": "johndoe"  // optional for new users
        }
        """
        serializer = AcceptOrganizationInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data['token']
        password = serializer.validated_data.get('password')
        username = serializer.validated_data.get('username')

        # Find invitation
        try:
            invitation = OrganizationInvitation.objects.get(token=token)
        except OrganizationInvitation.DoesNotExist:
            return Response(
                {'error': 'Invalid invitation token'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Check if invitation is valid
        if not invitation.is_valid():
            reasons = []
            if not invitation.is_active:
                reasons.append('Invitation has been revoked')
            if invitation.is_expired():
                reasons.append('Invitation has expired')
            if invitation.max_uses > 0 and invitation.use_count >= invitation.max_uses:
                reasons.append('Invitation has reached maximum uses')

            return Response(
                {
                    'error': 'Invitation is not valid',
                    'reasons': reasons
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # For email-specific invitations, check email match
        user_email = None
        if request.user.is_authenticated:
            user_email = request.user.email
        elif invitation.email:
            # For new users, email will be the invitation email
            user_email = invitation.email

        if invitation.email and user_email:
            if not invitation.can_be_used_by(user_email):
                return Response(
                    {'error': f'This invitation is for {invitation.email} only'},
                    status=status.HTTP_403_FORBIDDEN
                )

        # Check if user exists
        if request.user.is_authenticated:
            user = request.user
            user_created = False
        else:
            # Create new user or get existing
            if invitation.email:
                try:
                    user = User.objects.get(email=invitation.email)
                    user_created = False
                except User.DoesNotExist:
                    # Create new user
                    if not password:
                        return Response(
                            {'error': 'Password is required to create a new account'},
                            status=status.HTTP_400_BAD_REQUEST
                        )

                    username = username or invitation.email.split('@')[0]
                    # Ensure username is unique
                    base_username = username
                    counter = 1
                    while User.objects.filter(username=username).exists():
                        username = f"{base_username}{counter}"
                        counter += 1

                    user = User.objects.create_user(
                        username=username,
                        email=invitation.email,
                        password=password,
                        is_verified=True,  # Auto-verify invited users
                        organization=invitation.organization,  # Legacy field
                        current_organization=invitation.organization,  # New field
                    )
                    user_created = True
            else:
                return Response(
                    {'error': 'You must be logged in to accept an open invitation'},
                    status=status.HTTP_401_UNAUTHORIZED
                )

        # Create or update membership
        membership, created = OrganizationMembership.objects.update_or_create(
            user=user,
            organization=invitation.organization,
            defaults={
                'role': invitation.role,
                'is_active': True,
                'invited_by': invitation.invited_by,
            }
        )

        # If role is admin, also call assign_org_admin
        if invitation.role == 'admin':
            assign_org_admin(user, invitation.organization)

        # Record invitation use
        OrganizationInvitationUse.objects.create(
            invitation=invitation,
            user=user,
        )

        # Increment use count
        invitation.use_count += 1
        invitation.save(update_fields=['use_count', 'updated_at'])

        log_org_activity(
            invitation.organization,
            'member_joined',
            actor=invitation.invited_by,
            target_user=user,
            metadata={'role': invitation.role, 'via': 'invitation'},
        )

        # Set as current organization if user doesn't have one
        if not user.current_organization_id:
            user.current_organization_id = invitation.organization.id
            user.save(update_fields=['current_organization_id'])

        # Generate JWT tokens if user was just created
        response_data = {
            'message': f'Successfully joined {invitation.organization.name}',
            'user_created': user_created,
            'organization': {
                'id': invitation.organization.id,
                'name': invitation.organization.name,
                'slug': invitation.organization.slug,
            },
            'role': invitation.role,
        }

        if user_created:
            # Generate JWT tokens
            from core.services.auth_tokens import build_user_refresh_token

            refresh = build_user_refresh_token(user)
            response_data['tokens'] = {
                'access': str(refresh.access_token),
                'refresh': str(refresh),
            }

        return Response(response_data, status=status.HTTP_200_OK)


class CreateOrganizationView(APIView):
    """Create a new organization (onboarding step)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """
        POST /api/core/organizations/
        Body: {
            "name": "Acme Corp"
        }
        """
        from django.utils.text import slugify
        from django.db import connection, transaction
        from core.services.tenant import slug_to_schema_name
        from customer.models import CustomerOrganisation
        from csm.models import CustomerUser

        name = request.data.get('name', '').strip()
        if not name:
            return Response(
                {'error': 'Organization name is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user

        # Check if organization name already exists
        if Organization.objects.filter(name=name).exists():
            return Response({
                'error': f'Organization with name "{name}" already exists. Please choose a different name.'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Generate unique slug
        base_slug = slugify(name)
        slug = base_slug
        counter = 1
        while Organization.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1

        # Create organization
        with transaction.atomic():
            organization = Organization.objects.create(
                name=name,
                slug=slug,
                email_domain=user.email.split('@')[-1] if '@' in user.email else None
            )

            # Set user's current organization
            user.organization = organization  # Legacy field
            user.current_organization = organization  # New field
            user.save(update_fields=['organization', 'current_organization'])

            # Create OrganizationMembership with admin role
            OrganizationMembership.objects.create(
                user=user,
                organization=organization,
                role='admin',
                is_active=True,
                invited_by=None  # Self-created
            )

            # Create CustomerOrganisation + admin CustomerUser for CSM features
            cust_org, _ = CustomerOrganisation.objects.get_or_create(
                organization=organization,
                defaults={'name': organization.name},
            )
            CustomerUser.objects.get_or_create(
                user=user,
                organisation=cust_org,
                defaults={
                    'user_type': 'admin',
                    'is_active': True,
                    'is_creator': True,
                },
            )

            # Switch to tenant schema to create roles
            schema_name = slug_to_schema_name(organization.slug)

            with connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO {schema_name}, public')

            try:
                from access_control.models import Role as AccessRole, UserRole

                # Create Organization Admin role (level=2)
                admin_role, _ = AccessRole.objects.get_or_create(
                    organization=organization,
                    name='Organization Admin',
                    defaults={'level': 2},
                )
                UserRole.objects.get_or_create(user=user, role=admin_role)

                # Create default Media Buyer role (level=30)
                default_role, _ = AccessRole.objects.get_or_create(
                    organization=organization,
                    name="Media Buyer",
                    defaults={"level": 30}
                )
                UserRole.objects.get_or_create(user=user, role=default_role)
            finally:
                with connection.cursor() as cursor:
                    cursor.execute('SET search_path TO public')

        serializer = OrganizationSerializer(organization, context={'request': request})
        return Response({
            'organization': serializer.data,
            'message': f'Organization "{organization.name}" created successfully'
        }, status=status.HTTP_201_CREATED)


class ValidateOrganizationSlugView(APIView):
    """Validate if an organization slug exists."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        GET /api/core/organizations/validate-slug/?slug=acme-corp
        """
        slug = request.query_params.get('slug', '').strip()
        if not slug:
            return Response(
                {'error': 'Slug parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            organization = Organization.objects.get(slug=slug, is_active=True)
            serializer = OrganizationSummarySerializer(organization)
            return Response({
                'exists': True,
                'organization': serializer.data
            })
        except Organization.DoesNotExist:
            return Response({
                'exists': False,
                'organization': None
            })


class JoinOrganizationBySlugView(APIView):
    """Join an organization using its slug."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        """
        POST /api/core/organizations/join/
        Body: {
            "slug": "acme-corp"
        }
        """
        from django.db import connection, transaction
        from core.services.tenant import slug_to_schema_name

        slug = request.data.get('slug', '').strip()
        if not slug:
            return Response(
                {'error': 'Slug is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user = request.user

        # Find organization
        try:
            organization = Organization.objects.get(slug=slug, is_active=True)
        except Organization.DoesNotExist:
            return Response(
                {'error': 'Organization not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Check if user is already a member
        if OrganizationMembership.objects.filter(
            user=user,
            organization=organization,
            is_active=True
        ).exists():
            return Response(
                {'error': 'You are already a member of this organization'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create membership
        with transaction.atomic():
            membership = OrganizationMembership.objects.create(
                user=user,
                organization=organization,
                role='member',  # New members join as regular members
                is_active=True,
                invited_by=None  # Self-joined via slug
            )

            # Only switch current_organization if the user doesn't already have one.
            # Preserving the existing workspace prevents users who already have
            # projects in another org from being pushed into an empty-project state.
            if not user.current_organization_id:
                user.current_organization = organization
                user.save(update_fields=['current_organization'])

            # Create default Media Buyer role in tenant schema
            schema_name = slug_to_schema_name(organization.slug)

            with connection.cursor() as cursor:
                cursor.execute(f'SET search_path TO {schema_name}, public')

            try:
                from access_control.models import Role, UserRole
                default_role, _ = Role.objects.get_or_create(
                    organization=organization,
                    name="Media Buyer",
                    defaults={"level": 30}
                )
                UserRole.objects.get_or_create(user=user, role=default_role)
            finally:
                with connection.cursor() as cursor:
                    cursor.execute('SET search_path TO public')

        log_org_activity(
            organization,
            'member_joined',
            actor=user,
            target_user=user,
            metadata={'role': 'member', 'via': 'slug'},
        )

        org_serializer = OrganizationSerializer(organization, context={'request': request})

        return Response({
            'organization': org_serializer.data,
            'message': f'Successfully joined organization "{organization.name}"'
        }, status=status.HTTP_201_CREATED)


class OnboardingStatusView(APIView):
    """
    Return whether the authenticated user needs to go through onboarding.

    Rule: onboarding is only required when the user has NO active organization
    membership. A user who is in an org but has no projects yet should NOT be
    shown the onboarding wizard — they are simply waiting to be invited to a
    project, or can create one themselves.

    GET /api/core/onboarding-status/
    Response:
      {
        "needs_onboarding": bool,   // true only when user has no org at all
        "has_org": bool,            // user has at least one active org membership
        "has_project": bool,        // has project(s) in the current org's schema
      }
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        # Check org membership (public schema — no tenant scoping)
        has_org = OrganizationMembership.objects.filter(
            user=user, is_active=True
        ).exists()

        # Check project membership (tenant-scoped to current org's schema)
        has_project = ProjectMember.objects.filter(
            user=user, is_active=True
        ).exists()

        # Only force onboarding when the user truly has no organization at all.
        needs_onboarding = not has_org

        return Response({
            'needs_onboarding': needs_onboarding,
            'has_org': has_org,
            'has_project': has_project,
        })
