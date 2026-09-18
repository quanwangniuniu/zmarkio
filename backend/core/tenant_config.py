"""
Tenant model registry for schema-per-org provisioning.

This module is the single source of truth for which Django models belong in
a per-org PostgreSQL schema (org_{slug}) as opposed to the shared public schema.

WHY per-model instead of per-app?
----------------------------------
The `core` app contains both shared models (Organization, CustomUser, Role,
Permission — which must stay in public) and tenant models (Project, Team,
etc.).  Configuring by model class gives explicit, reviewable control without
relying on app-level heuristics.

WHY ordered?
------------
Django's SchemaEditor.create_model() creates FK constraints immediately via
ALTER TABLE, so the referenced table must already exist.  The list returned by
get_tenant_models() is therefore in topological dependency order.

HOW to extend?
--------------
When a new feature module with org-scoped data is added:
  1. Import the new model class below.
  2. Add it after all its FK dependencies in get_tenant_models().
  3. Existing org schemas: run `python manage.py migrate_all_tenants`.
  4. New org schemas: provisioning picks it up automatically.
"""


def get_tenant_models():  # noqa: C901 — long but intentionally explicit
    """
    Return all Django model classes that should live in per-org schemas.
    Order is topological: each model appears after every tenant model it
    references via ForeignKey / OneToOneField.
    """

    # ------------------------------------------------------------------
    # core — tenant subset only (Organization, CustomUser remain in public)
    # Role and Permission are now tenant-scoped for proper isolation
    # ------------------------------------------------------------------
    from core.models import (
        Role,
        Permission,
        Team,
        TeamMember,
        Project,
        ProjectMember,
        ProjectInvitation,
        AdChannel,
    )

    # ------------------------------------------------------------------
    # task
    # ------------------------------------------------------------------
    from task.models import (
        Task,
        TaskPin,
        ApprovalChain,
        ApprovalChainStep,
        ApprovalRecord,
        TaskComment,
        TaskAttachment,
        TaskRelation,
        TaskHierarchy,
        TaskFieldHistory,
    )

    # ------------------------------------------------------------------
    # budget_approval (depends on Task, Project, AdChannel, Role)
    # ------------------------------------------------------------------
    from budget_approval.models import (
        BudgetPool,
        BudgetEscalationRule,
        BudgetRequest,
    )

    # ------------------------------------------------------------------
    # campaign
    # ------------------------------------------------------------------
    from campaign.models import (
        Campaign,
        CampaignStatusHistory,
        PerformanceCheckIn,
        PerformanceSnapshot,
        AutomationTrigger,
        AutomationExecution,
        CampaignNotificationPreference,
        CampaignTemplate,
        CampaignAttachment,
        CampaignTaskLink,
        CampaignDecisionLink,
    )

    # ------------------------------------------------------------------
    # decision
    # ------------------------------------------------------------------
    from decision.models import (
        Decision,
        DecisionTopicLabel,
        DecisionEdge,
        Signal,
        Option,
        Review,
        DecisionStateTransition,
        CommitRecord,
    )

    # ------------------------------------------------------------------
    # asset
    # ------------------------------------------------------------------
    from asset.models import (
        Asset,
        AssetStateTransition,
        AssetVersion,
        AssetVersionStateTransition,
        AssetComment,
        ReviewAssignment,
    )

    # ------------------------------------------------------------------
    # meetings
    # ------------------------------------------------------------------
    from meetings.models import (
        MeetingTypeDefinition,
        MeetingTagDefinition,
        Meeting,
        AgendaItem,
        ParticipantLink,
        MeetingTagAssignment,
        MeetingDecisionOrigin,
        MeetingTaskOrigin,
        MeetingActionItem,
        ArtifactLink,
        MeetingTemplate,
        MeetingDocument,
        MeetingAuditLog,
    )

    # ------------------------------------------------------------------
    # chat
    # ------------------------------------------------------------------
    from chat.models import (
        Chat,
        ChatStar,
        ChatParticipant,
        Message,
        MessageStatus,
        MessageMention,
        MessageReaction,
        ThreadReadStatus,
        MessageAttachment,
        PinnedMessage,
        SavedMessage,
        MessageReminder,
        ScheduledMessage,
    )

    # ------------------------------------------------------------------
    # spreadsheet
    # ------------------------------------------------------------------
    from spreadsheet.models import (
        Spreadsheet,
        Sheet,
        PivotConfig,
        SheetRow,
        SheetColumn,
        SheetStructureOperation,
        Cell,
        CellDependency,
        SpreadsheetHighlight,
        SpreadsheetCellFormat,
        WorkflowPattern,
        WorkflowPatternStep,
        PatternJob,
    )

    # ------------------------------------------------------------------
    # calendars — all models have explicit FK to Organization, so they are
    # naturally multi-tenant-aware; placing them in org schema makes the
    # FK constraint a same-schema self-reference (simpler and faster).
    # calendars.Notification is aliased to avoid confusion with the
    # notifications app's Notification model.
    # ------------------------------------------------------------------
    from calendars.models import (
        RecurrenceRule,
        EventCategory,
        Calendar,
        CalendarSubscription,
        CalendarShare,
        Event,
        RecurrenceException,
        EventAttendee,
        EventReminder,
        EventCategoryAssignment,
        CalendarSettings,
        Notification as CalendarsNotification,
        CalendarEvent,
        BookingLink,
    )

    # ------------------------------------------------------------------
    # campaign × calendar cross-link (depends on Campaign + Event)
    # ------------------------------------------------------------------
    from campaign.models import CampaignCalendarLink

    # ------------------------------------------------------------------
    # access_control — org-level role assignments
    # ------------------------------------------------------------------
    from access_control.models import (
        RolePermission,
        UserRole,
        ModuleApprover,
        AdminOverrideAudit,
    )

    # ------------------------------------------------------------------
    # comments — generic comments on tenant objects via GenericForeignKey
    # ------------------------------------------------------------------
    from comments.models import (
        Comment,
        CommentMention,
        CommentAttachment,
    )

    # ------------------------------------------------------------------
    # miro — collaborative board for projects
    # ------------------------------------------------------------------
    from miro.models import (
        Board,
        BoardItem,
        BoardRevision,
        BoardAccess,
    )

    # ------------------------------------------------------------------
    # notion_editor — notion-style document editor
    # ------------------------------------------------------------------
    from notion_editor.models import (
        Draft,
        ContentBlock,
        DraftRevision,
        BlockAction,
        MediaFile,
        NotionConnection,
    )

    # ------------------------------------------------------------------
    # ad_copy_variation — org-scoped drafts. Depends on Project (tenant)
    # and MetaAdCreative / CustomUser (public).
    # ------------------------------------------------------------------
    from ad_copy_variation.models import AdCopyVariation

    # ------------------------------------------------------------------
    # Return in topological order
    # ------------------------------------------------------------------
    return [
        # core - Role and Permission must come first (no intra-tenant dependencies)
        Role,
        Permission,
        # core - other models
        Team,
        Project,
        ProjectMember,
        ProjectInvitation,
        AdChannel,
        TeamMember,
        # task
        Task,
        TaskPin,
        ApprovalChain,
        ApprovalChainStep,
        ApprovalRecord,
        TaskComment,
        TaskAttachment,
        TaskRelation,
        TaskHierarchy,
        TaskFieldHistory,
        # budget_approval (BudgetPool depends on Project + AdChannel + Task)
        BudgetPool,
        BudgetEscalationRule,       # depends on BudgetPool + Role
        BudgetRequest,              # depends on BudgetPool + Task + AdChannel
        # campaign (base + non-cross-linking)
        Campaign,
        CampaignStatusHistory,
        PerformanceCheckIn,
        PerformanceSnapshot,
        AutomationTrigger,
        AutomationExecution,
        CampaignNotificationPreference,
        CampaignTemplate,
        CampaignAttachment,
        CampaignTaskLink,       # depends on Campaign + Task
        # decision
        Decision,
        DecisionTopicLabel,
        DecisionEdge,
        Signal,
        Option,
        Review,
        DecisionStateTransition,
        CommitRecord,
        CampaignDecisionLink,   # depends on Campaign + Decision
        # asset (depends on Task + Team)
        Asset,
        AssetStateTransition,
        AssetVersion,
        AssetVersionStateTransition,
        AssetComment,
        ReviewAssignment,
        # meetings (Meeting depends on MeetingTypeDefinition)
        MeetingTypeDefinition,
        MeetingTagDefinition,
        Meeting,
        AgendaItem,
        ParticipantLink,
        MeetingTagAssignment,
        MeetingDecisionOrigin,  # depends on Meeting + Decision
        MeetingTaskOrigin,      # depends on Meeting + Task
        MeetingActionItem,
        ArtifactLink,
        MeetingTemplate,
        MeetingDocument,
        MeetingAuditLog,
        # chat
        Chat,
        ChatStar,
        ChatParticipant,
        Message,
        MessageStatus,
        MessageMention,
        MessageReaction,
        ThreadReadStatus,
        MessageAttachment,
        PinnedMessage,
        SavedMessage,
        MessageReminder,
        ScheduledMessage,
        # spreadsheet
        Spreadsheet,
        Sheet,
        PivotConfig,
        SheetRow,
        SheetColumn,
        SheetStructureOperation,
        Cell,
        CellDependency,
        SpreadsheetHighlight,
        SpreadsheetCellFormat,
        WorkflowPattern,
        WorkflowPatternStep,
        PatternJob,
        # calendars (RecurrenceRule + EventCategory have no intra-tenant deps)
        RecurrenceRule,
        EventCategory,
        Calendar,
        CalendarSubscription,
        CalendarShare,
        Event,
        RecurrenceException,
        EventAttendee,
        EventReminder,
        EventCategoryAssignment,
        CalendarSettings,
        CalendarsNotification,
        CalendarEvent,
        BookingLink,            # depends on Calendar
        CampaignCalendarLink,   # depends on Campaign + Event
        # access_control
        RolePermission,
        UserRole,
        ModuleApprover,
        AdminOverrideAudit,  # depends on User + Organization only, same as Role
        # comments
        Comment,
        CommentMention,
        CommentAttachment,
        # miro (Board depends on Project)
        Board,
        BoardItem,
        BoardRevision,
        BoardAccess,
        # notion_editor (Draft depends on User in public schema)
        Draft,
        ContentBlock,
        DraftRevision,
        BlockAction,
        MediaFile,
        NotionConnection,
        # ad_copy_variation (depends on Project; creative/user stay in public)
        AdCopyVariation,
    ]
