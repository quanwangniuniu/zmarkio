from rest_framework import serializers

from .models import (
    Customer, CustomerOrganisation, Region, CustomerStatusLabel,
    CustomerInternalNote, CustomerInternalNoteAuditLog
)

class RegionSerializer(serializers.ModelSerializer):
    class Meta:
      model = Region
      fields = ['id', 'name', 'organisation', 'is_active', 'created_at', 'updated_at']
      read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_name(self, value):
      org_id = self.initial_data.get('organisation') or (self.instance.organisation_id if self.instance else None)
      qs = Region.objects.filter(name__iexact=value, organisation_id=org_id)
      if self.instance:
          qs = qs.exclude(pk=self.instance.pk)
      if qs.exists():
          raise serializers.ValidationError(
              'A region with this name already exists in this organisation.'
          )
      return value
                          
class CustomerOrganisationSerializer(serializers.ModelSerializer):
    customers = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CustomerOrganisation
        fields = ['id', 'name', 'organization', 'domains', 'industry', 'plan', 'region', 'customers', 'created_at', 'updated_at']
        read_only_fields = ['id', 'customers', 'created_at', 'updated_at']

    def get_customers(self, obj):
        from .serializers import CustomerSerializer
        return CustomerSerializer(obj.customers.all(), many=True).data

    def validate_name(self, value):
        org_id = self.initial_data.get('organization') or (self.instance.organization_id if self.instance else None)
        qs = CustomerOrganisation.objects.filter(name__iexact=value, organization_id=org_id)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                'An organisation with this name already exists.'
            )
        return value

    
class CustomerSerializer(serializers.ModelSerializer):
    experience_group_name = serializers.SerializerMethodField(read_only=True)
    status_label_name = serializers.CharField(source='status_label.name', read_only=True, default=None)
    status_label_color = serializers.CharField(source='status_label.color', read_only=True, default=None)

    class Meta:
        model = Customer
        fields = [
            'id',
            # Whether this customer has an account here, and which one. Callers
            # that invite customers need to tell the two apart: an account can
            # be added as a real participant, while someone without one can
            # only ever be reached by email.
            'user_id',
            'email',
            'full_name',
            'company',
            'phone',
            'experience_group',
            'experience_group_name',
            'region',
            'organisation',
            'status_label',
            'status_label_name',
            'status_label_color',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'user_id', 'created_at', 'updated_at', 'experience_group_name',
            'status_label_name', 'status_label_color',
        ]

    def get_experience_group_name(self, obj):
        if obj.experience_group_id:
            return obj.experience_group.name
        return None

    def _project_id(self):
        if self.instance and self.instance.project_id:
            return self.instance.project_id
        return self.context.get('project_id')

    def validate_email(self, value):
        project_id = self._project_id()
        qs = Customer.objects.filter(email__iexact=value)
        if project_id is not None:
            qs = qs.filter(project_id=project_id)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                'A customer with this email already exists in this project.'
            )
        return value.lower()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        experience_group = attrs.get('experience_group')
        if experience_group is None and self.instance:
            experience_group = self.instance.experience_group
        project_id = self._project_id()
        if experience_group is not None and project_id is not None:
            if experience_group.project_id != project_id:
                raise serializers.ValidationError(
                    {'experience_group': 'Group must belong to the same project.'}
                )
        return attrs


class CustomerStatusLabelSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomerStatusLabel
        fields = [
            'id', 'project',
            'name', 'color', 'order', 'is_active',
            'created_at', 'updated_at',
        ]
        # project is set from the ?project= query param by the viewset.
        read_only_fields = ['id', 'project', 'created_at', 'updated_at']

    def validate_name(self, value):
        """Reject a duplicate name within the same project (case-insensitive).

        `project` is read-only (set from the query param), so resolve it from the
        instance (edit) or the request's ?project= (create) rather than the body.
        """
        if self.instance is not None:
            project_id = self.instance.project_id
        else:
            request = self.context.get('request')
            project_id = request.query_params.get('project') if request else None
            from core.slug_mixins import resolve_project_pk
            project_id = resolve_project_pk(project_id)

        if project_id is None:
            return value

        qs = CustomerStatusLabel.objects.filter(project_id=project_id, name__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                'A label with this name already exists in this project.'
            )
        return value


class CustomerInternalNoteSerializer(serializers.ModelSerializer):
    author_email = serializers.CharField(source='author.email', read_only=True)
    author_name = serializers.SerializerMethodField(read_only=True)
    author_avatar = serializers.SerializerMethodField(read_only=True)
    is_author = serializers.SerializerMethodField()
    body_text = serializers.CharField(read_only=True)

    class Meta:
        model = CustomerInternalNote
        fields = [
            'id', 'customer', 'author', 'author_email', 'author_name', 'author_avatar',
            'body', 'body_text', 'body_format',
            'is_edited', 'created_at', 'updated_at', 'is_author',
        ]
        read_only_fields = ['id', 'author', 'body_text', 'body_format', 'created_at', 'updated_at', 'is_author']

    def get_author_name(self, obj):
        full = obj.author.get_full_name()
        return full if full.strip() else obj.author.email

    def get_author_avatar(self, obj):
        avatar = getattr(obj.author, 'avatar', None)
        if not avatar:
            return None
        request = self.context.get('request')
        url = avatar.url
        return request.build_absolute_uri(url) if request else url

    def get_is_author(self, obj):
        request = self.context.get('request')
        return request and request.user == obj.author


class CustomerInternalNoteAuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.CharField(source='actor.email', read_only=True, allow_null=True)
    event_type_display = serializers.CharField(source='get_event_type_display', read_only=True)

    class Meta:
        model = CustomerInternalNoteAuditLog
        fields = [
            'id', 'customer', 'actor', 'actor_email',
            'event_type', 'event_type_display',
            'timestamp', 'note_id', 'note_body',
        ]
        read_only_fields = ['id', 'customer', 'actor', 'event_type', 'timestamp', 'note_id', 'note_body']
