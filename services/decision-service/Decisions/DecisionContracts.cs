using System.Text.Json.Serialization;

namespace DecisionService.Decisions;

public enum DecisionStatus
{
    PREDRAFT,
    DRAFT,
    AWAITING_APPROVAL,
    COMMITTED,
    REVIEWED,
    ARCHIVED
}

public enum DecisionRiskLevel
{
    LOW,
    MEDIUM,
    HIGH
}

public sealed record DecisionSummaryDto(
    int Id,
    string Slug,
    int ProjectSeq,
    DecisionStatus Status,
    string? Title,
    string? ContextSummary,
    string? SelectedOptionText,
    DecisionRiskLevel? RiskLevel,
    int? ConfidenceScore,
    DateTimeOffset CreatedAt,
    int? CreatedBy,
    DateTimeOffset? CommittedAt,
    int? ProjectId,
    string? ProjectName,
    string Topic,
    string TopicLabel,
    bool HasReviews,
    bool CreatedByAgent,
    Guid? AgentSessionId
);

public sealed record DecisionDetailDto(
    int Id,
    string Slug,
    int ProjectSeq,
    DecisionStatus Status,
    string? Title,
    string? ContextSummary,
    string? Reasoning,
    DecisionRiskLevel? RiskLevel,
    int? ConfidenceScore,
    string Topic,
    string TopicLabel,
    DateTimeOffset CreatedAt,
    int? CreatedBy,
    DateTimeOffset LastEditedAt,
    int? LastEditedBy,
    DateTimeOffset? CommittedAt,
    int? ProjectId,
    string? ProjectName,
    bool IsReferenceCase,
    bool CreatedByAgent,
    Guid? AgentSessionId,
    DateTimeOffset? PlannedDecisionDate,
    IReadOnlyList<DecisionSignalDto> Signals,
    IReadOnlyList<DecisionOptionDto> Options,
    DecisionOriginMeetingDto? OriginMeeting = null
);

public sealed record DecisionOriginMeetingDto(
    int Id,
    string Title,
    string? Url,
    string? DetailUrl,
    int? ProjectId,
    string? Type,
    DateOnly? ScheduledDate
);

public sealed record DecisionSignalDto(
    int Id,
    int DecisionId,
    int? CreatedBy,
    DateTimeOffset CreatedAt,
    DateTimeOffset UpdatedAt,
    string? Metric,
    string? Movement,
    string? Period,
    string Comparison,
    string? ScopeType,
    string? ScopeValue,
    decimal? DeltaValue,
    string? DeltaUnit,
    string DisplayText,
    string? DisplayTextOverride
);

public sealed record DecisionOptionDto(
    int Id,
    int DecisionId,
    string Text,
    bool IsSelected,
    int Order,
    DateTimeOffset CreatedAt,
    DateTimeOffset UpdatedAt
);

public sealed record DecisionListResponse(
    IReadOnlyList<DecisionSummaryDto> Items,
    string? NextPageToken
);

public sealed record CreateDecisionDraftRequest(
    string? Title,
    string? ContextSummary,
    DecisionRiskLevel? RiskLevel,
    int? ConfidenceScore,
    string? Reasoning,
    string? Topic,
    bool IsReferenceCase,
    bool CreatedByAgent,
    Guid? AgentSessionId,
    DateTimeOffset? PlannedDecisionDate,
    IReadOnlyList<DecisionSignalInput>? Signals,
    IReadOnlyList<DecisionOptionInput>? Options,
    IReadOnlyList<int>? ParentDecisionIds,
    [property: JsonPropertyName("origin_meeting_id")] int? OriginMeetingId = null
);

public sealed record UpdateDecisionDraftRequest(
    string? Title,
    string? ContextSummary,
    DecisionRiskLevel? RiskLevel,
    int? ConfidenceScore,
    string? Reasoning,
    string? Topic,
    bool? IsReferenceCase,
    DateTimeOffset? PlannedDecisionDate,
    IReadOnlyList<DecisionSignalInput>? Signals,
    IReadOnlyList<DecisionOptionInput>? Options,
    IReadOnlyList<int>? ParentDecisionIds,
    [property: JsonPropertyName("origin_meeting_id")] int? OriginMeetingId = null
);

public sealed record DecisionSignalInput(
    string? Metric,
    string? Movement,
    string? Period,
    string? Comparison,
    string? ScopeType,
    string? ScopeValue,
    decimal? DeltaValue,
    string? DeltaUnit,
    string? DisplayTextOverride
);

public sealed record DecisionOptionInput(
    string Text,
    bool IsSelected,
    int Order
);

public sealed record DecisionActionRequest(
    string? Note,
    Dictionary<string, object?>? Metadata
);

public sealed record DecisionActionResponse(
    string Detail,
    DecisionStatus Status,
    string? NextAction,
    DecisionDetailDto Decision
);

public sealed record DecisionSignalListResponse(IReadOnlyList<DecisionSignalDto> Items);

public sealed record DecisionConnectionNodeDto(int Id, string Slug, int ProjectSeq, string? Title);

public sealed record DecisionConnectionEdgeDto(int FromId, int ToId, int FromSeq, int ToSeq);

public sealed record DecisionConnectionSelfDto(int Id, int ProjectSeq);

public sealed record DecisionConnectionsResponse(
    DecisionConnectionSelfDto Self,
    IReadOnlyList<DecisionConnectionNodeDto> Connected,
    IReadOnlyList<DecisionConnectionEdgeDto> Edges
);

public sealed record DecisionGraphNodeDto(
    int Id,
    string Slug,
    string? Title,
    DecisionStatus Status,
    int? ProjectSeq,
    DateTimeOffset CreatedAt,
    DateTimeOffset UpdatedAt,
    int? ProjectId,
    string? ProjectName,
    string? ProjectTheme,
    string? ProjectSubtitle,
    string Topic,
    string TopicLabel,
    DecisionRiskLevel? RiskLevel
);

public sealed record DecisionGraphEdgeDto(
    int From,
    int To,
    string? EdgeType
);

public sealed record DecisionGraphTopicDto(
    string Topic,
    string Title,
    string DefaultTitle
);

public sealed record DecisionGraphResponse(
    IReadOnlyList<DecisionGraphNodeDto> Nodes,
    IReadOnlyList<DecisionGraphEdgeDto> Edges,
    IReadOnlyList<DecisionGraphTopicDto> Topics
);

public sealed record DecisionTopicLabelRequest(string? Title);

public sealed record DecisionTopicLabelResponse(
    string Topic,
    string Title,
    string DefaultTitle
);

public sealed record DecisionConnectionsUpdateRequest(
    IReadOnlyList<int>? ConnectedDecisionIds,
    IReadOnlyList<int>? ConnectedDecisionSeqs
);

public sealed record DecisionReviewDto(
    int Id,
    string OutcomeText,
    string ReflectionText,
    string DecisionQuality,
    DateTimeOffset ReviewedAt,
    int? ReviewerId
);

public sealed record DecisionReviewInput(
    string OutcomeText,
    string ReflectionText,
    string DecisionQuality
);
