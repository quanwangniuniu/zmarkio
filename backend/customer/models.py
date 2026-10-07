import uuid
from django.db import models
from django.conf import settings


class Customer(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='customer_profile',
    )
    email = models.EmailField()
    full_name = models.CharField(max_length=200)
    company = models.CharField(max_length=200, blank=True, default='')
    phone = models.CharField(max_length=50, blank=True, default='')
    experience_group = models.ForeignKey(
        'experience_group.ExperienceGroup',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='customers',
    )
    project = models.ForeignKey(                                                                                                                                  
      'core.Project',                                                                                                                                           
      on_delete=models.CASCADE,                                                                                                                                 
      related_name='customers',                                                                                                                                 
      null=True,                                                                                                                                                
      blank=True,
  )       
    # The workspace this customer belongs to. project_id alone is ambiguous
    # (project ids are numbered per organisation schema), so external API
    # scoping filters on organization + project.
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='csm_customers',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    region = models.ForeignKey(
        'Region',
        null=True,
        blank = True,
        on_delete = models.SET_NULL,
        related_name='customers'

    )
    organisation = models.ForeignKey(
        'CustomerOrganisation',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='customers'
        )
    status_label = models.ForeignKey(
        'CustomerStatusLabel',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='customers',
    )

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'email'],
                name='customer_unique_email_per_project',
            ),
        ]
    def __str__(self):
        return f"{self.full_name} <{self.email}>"
class Region(models.Model):
    name = models.CharField(max_length=200)
    project = models.ForeignKey('core.Project', on_delete=models.CASCADE, null=True, blank=True, related_name='regions')
    organisation = models.ForeignKey(
        'CustomerOrganisation',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='regions',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['organisation', 'name'],
                name='region_unique_name_per_org'
            )
        ]
class CustomerOrganisation(models.Model):
    name = models.CharField(max_length=200)
    organization = models.ForeignKey(
        'core.Organization',
        on_delete=models.CASCADE,
        related_name='customer_organisations',
        null=True, blank=True,
    )
    domains = models.JSONField(default = list)
    industry = models.CharField(max_length = 200, blank = True, default = '')
    plan = models.CharField(max_length = 200, blank = True, default = '')
    region = models.ForeignKey(
        'Region',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='organisations'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'name'],
                name = 'customerorganisation_unique_name_per_org'
            )
        ]
    def __str__(self):
        return f"{self.name}"


class CustomerStatusLabel(models.Model):
    """Customer status label (e.g., Gold, Silver, Partner, Internal).

    Project-scoped (confirmed with product owner): one shared set of labels per
    project, consistent with the other CSM workspace settings in the same admin
    area (work types, support projects, SLA policy — all per-project).
    """

    project = models.ForeignKey(
        'core.Project',
        on_delete=models.CASCADE,
        related_name='customer_status_labels',
    )
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=20)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'name'],
                name='customer_status_label_unique_name_per_project',
            )
        ]

    def __str__(self):
        return f"{self.name}"


class CustomerInternalNote(models.Model):
    """Internal notes on customer profile — never visible to customer"""

    customer = models.ForeignKey(
        'Customer',
        on_delete=models.CASCADE,
        related_name='internal_notes',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='customer_internal_notes',
    )
    body = models.JSONField()
    body_text = models.TextField(blank=True, default='')
    body_format = models.CharField(max_length=20, default='rich_text_json')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_edited = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def _extract_text_from_tiptap(self, content):
        """Recursively extract plain text from Tiptap JSON structure"""
        if not content:
            return ''

        if isinstance(content, str):
            return content

        if isinstance(content, dict):
            # If this node has text, extract it
            if 'text' in content:
                return content['text']

            # Recursively process content array
            if 'content' in content and isinstance(content['content'], list):
                inner = ''.join(self._extract_text_from_tiptap(item) for item in content['content'])
                # Separate block-level nodes with a newline so text from different
                # blocks doesn't merge (e.g. two paragraphs -> "Hello\nworld").
                if content.get('type') in ('paragraph', 'heading', 'blockquote', 'listItem'):
                    return inner + '\n'
                return inner

            return ''

        if isinstance(content, list):
            return ''.join(self._extract_text_from_tiptap(item) for item in content)

        return ''

    def save(self, *args, **kwargs):
        # Auto-extract plain text from Tiptap JSON before saving. Collapse the
        # trailing block newlines and trim so body_text stays clean for search.
        if self.body:
            self.body_text = self._extract_text_from_tiptap(self.body).strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Note by {self.author.email} on {self.customer.email}"


class CustomerInternalNoteAuditLog(models.Model):
    """Immutable audit log for internal note deletions"""

    EVENT_NOTE_DELETED = 'note.deleted'
    EVENT_NOTE_CREATED = 'note.created'
    EVENT_NOTE_EDITED = 'note.edited'

    EVENT_TYPE_CHOICES = [
        (EVENT_NOTE_CREATED, 'Note Created'),
        (EVENT_NOTE_EDITED, 'Note Edited'),
        (EVENT_NOTE_DELETED, 'Note Deleted'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(
        'Customer',
        on_delete=models.CASCADE,
        related_name='internal_note_audit_log',
        db_index=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='customer_note_audit_entries',
        db_index=True,
    )
    event_type = models.CharField(
        max_length=20,
        choices=EVENT_TYPE_CHOICES,
        db_index=True,
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    note_id = models.IntegerField(null=True, blank=True)
    note_body = models.TextField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['customer', '-timestamp'], name='audit_note_ts_idx'),
            models.Index(fields=['customer', 'event_type', '-timestamp'], name='audit_note_type_ts_idx'),
        ]

    def __str__(self):
        actor_name = self.actor.email if self.actor else 'System'
        return f"AuditLog({self.customer_id}, {self.event_type}, {actor_name}, {self.timestamp})"