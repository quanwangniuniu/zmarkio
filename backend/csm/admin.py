from django.contrib import admin
from .models import (
    Queue, QueueAgent, QueueTeam, CustomerUser, Ticket, CSMInvitation,
    SupportProject, CsmWorkType, TicketForm, TicketFormField,
    TicketFormAssignment, TicketFormSubmission, TicketAttachment,
    SupportChannel, SupportChannelExperienceGroup,
    ConversationQualityReview,
)


@admin.register(Queue)
class QueueAdmin(admin.ModelAdmin):
    list_display = ['name', 'project', 'organisation', 'tier', 'display_order', 'is_active', 'created_at']
    list_filter = ['tier', 'is_active']
    search_fields = ['name']


@admin.register(QueueAgent)
class QueueAgentAdmin(admin.ModelAdmin):
    list_display = ['queue', 'user', 'assigned_by', 'created_at']


@admin.register(QueueTeam)
class QueueTeamAdmin(admin.ModelAdmin):
    list_display = ['queue', 'team', 'created_at']


@admin.register(CustomerUser)
class CustomerUserAdmin(admin.ModelAdmin):
    list_display = ['user', 'team', 'queue', 'user_type', 'is_active', 'created_at']
    list_filter = ['user_type', 'is_active']
    search_fields = ['user__email']


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'queue', 'status', 'priority', 'form', 'experience_group',
        'assigned_to', 'created_at',
    ]
    list_filter = ['status', 'priority']
    search_fields = ['title', 'customer_email']
    raw_id_fields = ['form', 'experience_group', 'support_project', 'work_type']


@admin.register(CSMInvitation)
class CSMInvitationAdmin(admin.ModelAdmin):
    list_display = ['email', 'project', 'team', 'accepted', 'expires_at', 'created_at']
    list_filter = ['accepted']
    search_fields = ['email']


class TicketFormFieldInline(admin.TabularInline):
    model = TicketFormField
    extra = 0
    ordering = ['sort_order']


@admin.register(TicketForm)
class TicketFormAdmin(admin.ModelAdmin):
    list_display = ['name', 'project', 'is_default', 'is_active', 'created_by', 'updated_at']
    list_filter = ['is_default', 'is_active']
    search_fields = ['name']
    raw_id_fields = ['project', 'created_by']
    inlines = [TicketFormFieldInline]


@admin.register(TicketFormField)
class TicketFormFieldAdmin(admin.ModelAdmin):
    list_display = ['form', 'field_key', 'label', 'field_type', 'is_required', 'sort_order']
    list_filter = ['field_type', 'is_required']
    search_fields = ['field_key', 'label']
    raw_id_fields = ['form']


@admin.register(TicketFormAssignment)
class TicketFormAssignmentAdmin(admin.ModelAdmin):
    list_display = ['form', 'experience_group', 'support_project', 'created_at']
    raw_id_fields = ['form', 'experience_group', 'support_project']


@admin.register(SupportProject)
class SupportProjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'project', 'is_archived', 'default_queue']
    list_filter = ['is_archived']
    search_fields = ['name']
    raw_id_fields = ['project', 'default_queue']


@admin.register(CsmWorkType)
class CsmWorkTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'project', 'sort_order', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name']
    raw_id_fields = ['project']


@admin.register(TicketFormSubmission)
class TicketFormSubmissionAdmin(admin.ModelAdmin):
    list_display = ['id', 'form', 'experience_group', 'submitted_by', 'created_at']
    raw_id_fields = ['form', 'experience_group', 'submitted_by']


@admin.register(TicketAttachment)
class TicketAttachmentAdmin(admin.ModelAdmin):
    list_display = ['original_name', 'ticket', 'submission', 'size_bytes', 'created_at']
    raw_id_fields = ['ticket', 'submission']


class SupportChannelExperienceGroupInline(admin.TabularInline):
    model = SupportChannelExperienceGroup
    extra = 0
    raw_id_fields = ['experience_group']


@admin.register(SupportChannel)
class SupportChannelAdmin(admin.ModelAdmin):
    list_display = [
        'display_name', 'channel_type', 'project', 'is_active', 'sort_order', 'updated_at',
    ]
    list_filter = ['channel_type', 'is_active']
    search_fields = ['display_name', 'email_address']
    raw_id_fields = ['project', 'default_queue', 'ticket_form']
    readonly_fields = ['embed_key']
    inlines = [SupportChannelExperienceGroupInline]


@admin.register(SupportChannelExperienceGroup)
class SupportChannelExperienceGroupAdmin(admin.ModelAdmin):
    list_display = ['channel', 'experience_group', 'created_at']
    raw_id_fields = ['channel', 'experience_group']


@admin.register(ConversationQualityReview)
class ConversationQualityReviewAdmin(admin.ModelAdmin):
    list_display = ['conversation', 'rating', 'agent_name', 'reviewer_name', 'reviewed_at']
    list_filter = ['rating']
    search_fields = ['agent_name', 'reviewer_name', 'comment']
    raw_id_fields = [
        'conversation', 'reviewer', 'agent_user', 'agent_customer_user',
        'queue', 'organisation',
    ]
