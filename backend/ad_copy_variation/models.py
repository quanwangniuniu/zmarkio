from django.conf import settings
from core.slug_mixins import SluggedResourceModelMixin
from django.db import models

from core.models import TimeStampedModel


class AdCopyVariation(SluggedResourceModelMixin, TimeStampedModel):
    slug_source_field = 'headline'

    STATUS_DRAFT = 'draft'
    STATUS_REVIEWED = 'reviewed'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Draft'),
        (STATUS_REVIEWED, 'Reviewed'),
    ]

    SOURCE_MODE_CHOICES = [
        ('existing', 'Existing creative'),
        ('custom', 'Custom content'),
        ('external_url', 'External URL'),
    ]

    project = models.ForeignKey(
        'core.Project',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='ai_copy_variations',
    )
    creative = models.ForeignKey(
        'meta_ads.MetaAdCreative',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='copy_variations',
    )
    source_mode = models.CharField(max_length=32, choices=SOURCE_MODE_CHOICES)
    source_ref = models.TextField(blank=True, default='')

    hook = models.TextField(blank=True, default='')
    headline = models.TextField(blank=True, default='')
    description = models.TextField(blank=True, default='')
    cta = models.CharField(max_length=64, blank=True, default='')
    validation_warnings = models.JSONField(default=list, blank=True)

    instruction = models.TextField(blank=True, default='')
    model_name = models.CharField(max_length=64, default='gemini-2.5-flash-lite')
    prompt_version = models.CharField(max_length=32, default='v1')

    batch_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text='Groups variations generated from the same batch call.',
    )
    batch_position = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['project', 'status', '-created_at']),
            models.Index(fields=['project', 'batch_id', 'status']),
            models.Index(fields=['creative', 'status', '-created_at']),
        ]

    def __str__(self):
        return f'AdCopyVariation({self.source_mode}, creative={self.creative_id})'
