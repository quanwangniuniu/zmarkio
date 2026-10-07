from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied

from core.admin_permissions import IsCsmAccessAllowed
from core.models import Project
from core.viewset_mixins import ProjectScopedViewSetMixin

from .models import (
    Customer, Region, CustomerOrganisation, CustomerStatusLabel,
    CustomerInternalNote, CustomerInternalNoteAuditLog
)
from .serializers import (
    CustomerSerializer, RegionSerializer, CustomerOrganisationSerializer,
    CustomerStatusLabelSerializer, CustomerInternalNoteSerializer,
    CustomerInternalNoteAuditLogSerializer
)


class RegionViewSet(viewsets.ModelViewSet):
    queryset = Region.objects.all()
    serializer_class = RegionSerializer
    permission_classes = [IsAuthenticated, IsCsmAccessAllowed]

    def get_queryset(self):
        from core.admin_utils import get_csm_admin_org_ids
        user = self.request.user
        qs = Region.objects.all()

        org_id = self.request.query_params.get('organisation')
        if org_id:
            qs = qs.filter(organisation_id=org_id)

        admin_org_ids = get_csm_admin_org_ids(user)
        return qs.filter(organisation_id__in=admin_org_ids)

    def perform_create(self, serializer):
        from core.admin_utils import get_csm_admin_org_ids
        org_id = self.request.data.get('organisation')
        if not org_id:
            admin_org_ids = get_csm_admin_org_ids(self.request.user)
            if admin_org_ids:
                org_id = admin_org_ids[0]
        serializer.save(organisation_id=org_id)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        customer_count = instance.customers.count()
        if customer_count > 0:
            return Response(
                {
                    'detail': (
                        f'Cannot delete: {customer_count} customer(s) are using this region. '
                        'Reassign them first.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

class CustomerOrganisationViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerOrganisationSerializer

    def get_permissions(self):
        if self.action in ('create', 'list', 'my_admin_orgs'):
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsCsmAccessAllowed()]

    def get_queryset(self):
        from core.admin_utils import get_csm_admin_org_ids
        user = self.request.user
        admin_org_ids = get_csm_admin_org_ids(user)
        return CustomerOrganisation.objects.filter(id__in=admin_org_ids)

    @action(detail=False, methods=['get'], url_path='my-admin-orgs')
    def my_admin_orgs(self, request):
        """Return organisations where current user is CSM admin."""
        from core.admin_utils import get_csm_admin_org_ids
        admin_org_ids = get_csm_admin_org_ids(request.user)
        orgs = CustomerOrganisation.objects.filter(id__in=admin_org_ids)
        data = [{'id': o.id, 'name': o.name} for o in orgs.order_by('name')]
        return Response(data)

    def perform_create(self, serializer):
        """Any authenticated user can create an org; auto-assign as admin creator."""
        from csm.models import CustomerUser
        org = serializer.save()
        CustomerUser.objects.create(
            user=self.request.user,
            organisation=org,
            user_type='admin',
            is_active=True,
            is_creator=True,
        )

    def destroy(self, request, *args, **kwargs):
        from core.admin_utils import get_csm_admin_org_ids
        instance = self.get_object()
        admin_org_ids = get_csm_admin_org_ids(request.user)
        if instance.id not in admin_org_ids:
            return Response(
                {'detail': 'You do not have permission to delete this organisation.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        customer_count = instance.customers.count()
        if customer_count > 0:
            return Response(
                {
                    'detail': (
                        f'Cannot delete: {customer_count} customer(s) belong to this organisation. '
                        'Reassign them first.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

class CustomerViewSet(ProjectScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Customer.objects.select_related('experience_group').all()
    serializer_class = CustomerSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.action in ('list', 'create'):
            context['project_id'] = self.get_required_project_id()
        return context

    def get_queryset(self):
        base = Customer.objects.select_related('experience_group')
        if self.action == 'list':
            project_id = self.get_required_project_id()
            return base.filter(project_id=project_id)
        return self.filter_by_accessible_projects(base)

    def perform_create(self, serializer):
        project_id = self.get_required_project_id()
        # Resolved under the request's tenant schema, where the project id is unambiguous.
        organization_id = (
            Project.objects.filter(pk=project_id).values_list('organization_id', flat=True).first()
        )
        serializer.save(project_id=project_id, organization_id=organization_id)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomerStatusLabelViewSet(ProjectScopedViewSetMixin, viewsets.ModelViewSet):
    """Project-scoped customer status labels — one shared set per project."""
    serializer_class = CustomerStatusLabelSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = CustomerStatusLabel.objects.filter(is_active=True)
        if self.action in ('list', 'reorder'):
            project_id = self.get_required_project_id()
            return qs.filter(project_id=project_id).order_by('order', 'name')
        return self.filter_by_accessible_projects(qs)

    def perform_create(self, serializer):
        serializer.save(project_id=self.get_required_project_id())

    def destroy(self, request, *args, **kwargs):
        """Delete a label.

        If the label is in use on one or more customers, the first attempt is
        blocked with a confirmation warning (HTTP 409). The client re-issues the
        request with ``?force=true`` to confirm; deletion then proceeds and the
        affected customers' ``status_label`` is cleared (FK is SET_NULL).
        """
        instance = self.get_object()
        force = str(request.query_params.get('force', '')).lower() in ('1', 'true', 'yes')
        customer_count = instance.customers.count()
        if customer_count > 0 and not force:
            return Response(
                {
                    'detail': (
                        f'This label is used by {customer_count} customer(s). '
                        'Deleting it will remove the label from those customers. '
                        'Confirm to proceed.'
                    ),
                    'customer_count': customer_count,
                    'requires_confirmation': True,
                },
                status=status.HTTP_409_CONFLICT,
            )
        instance.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['put'], url_path='reorder')
    def reorder(self, request):
        """Persist a new label order. Body: {"ids": [<label_id>, ...]}."""
        ids = request.data.get('ids')
        if not isinstance(ids, list) or not ids:
            return Response(
                {'ids': 'A non-empty list of label ids is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(ids) != len(set(ids)):
            return Response(
                {'ids': 'Duplicate label ids are not allowed.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Scope to labels the user administers — reuse the same queryset filter.
        labels = list(self.get_queryset().filter(pk__in=ids))
        found_ids = {label.pk for label in labels}
        missing = [pk for pk in ids if pk not in found_ids]
        if missing:
            return Response(
                {'ids': f'Label ids not found or not accessible: {missing}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # bulk_update bypasses auto_now, so set updated_at explicitly.
        now = timezone.now()
        by_id = {label.pk: label for label in labels}
        for index, pk in enumerate(ids):
            by_id[pk].order = index
            by_id[pk].updated_at = now
        CustomerStatusLabel.objects.bulk_update(by_id.values(), ['order', 'updated_at'])

        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data)


class CustomerInternalNoteViewSet(ProjectScopedViewSetMixin, viewsets.ModelViewSet):
    """Internal notes on customer profiles — never visible to customers.

    Scoped to customers in projects the user can access, so notes never leak
    across projects (consistent with CustomerViewSet).
    """
    serializer_class = CustomerInternalNoteSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Only notes whose customer belongs to a project the user can access.
        qs = CustomerInternalNote.objects.select_related('author', 'customer').filter(
            customer__project_id__in=self._accessible_project_ids(),
        )
        customer_id = self.request.query_params.get('customer')
        if customer_id:
            qs = qs.filter(customer_id=customer_id)
        return qs

    def perform_create(self, serializer):
        """Set author to current user; block creating notes on inaccessible customers."""
        customer = serializer.validated_data.get('customer')
        if customer is not None and customer.project_id not in self._accessible_project_ids():
            raise PermissionDenied('You cannot add notes to this customer.')
        note = serializer.save(author=self.request.user)
        self._record_audit('note.created', note)

    def perform_update(self, serializer):
        """Only the author may edit their own note."""
        if serializer.instance.author != self.request.user:
            raise PermissionDenied("You can only edit your own notes.")
        note = serializer.save(is_edited=True)
        self._record_audit('note.edited', note)

    def perform_destroy(self, instance):
        """Record deletion to audit log before deleting"""
        # Check permissions: author can delete own notes, admin can delete any
        if instance.author != self.request.user:
            from core.admin_utils import is_csm_admin
            if not is_csm_admin(self.request.user):
                raise PermissionDenied(
                    "You can only delete your own notes. Contact admin to delete others' notes."
                )

        self._record_audit('note.deleted', instance)
        instance.delete()

    def _record_audit(self, event_type, note):
        """Record audit log entry. Snapshot the plain-text body (not the JSON)."""
        CustomerInternalNoteAuditLog.objects.create(
            customer=note.customer,
            actor=self.request.user,
            event_type=event_type,
            note_id=note.id,
            note_body=(note.body_text or '')[:500] or None,
        )


class CustomerInternalNoteAuditLogViewSet(ProjectScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only audit log for internal note changes (agents/admins only).

    Scoped to customers in projects the user can access (same as the notes).
    """
    serializer_class = CustomerInternalNoteAuditLogSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = CustomerInternalNoteAuditLog.objects.select_related('actor', 'customer').filter(
            customer__project_id__in=self._accessible_project_ids(),
        )
        customer_id = self.request.query_params.get('customer')
        if customer_id:
            qs = qs.filter(customer_id=customer_id)
        return qs