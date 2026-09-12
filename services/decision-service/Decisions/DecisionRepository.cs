using System.Collections.Concurrent;

namespace DecisionService.Decisions;

public interface IDecisionRepository
{
    DecisionListResponse List(int? projectId, DecisionStatus? status, int pageSize, int pageToken);
    DecisionDetailDto? Get(string lookup);
    DecisionDetailDto Create(int? projectId, int? actorId, CreateDecisionDraftRequest request);
    DecisionDetailDto? UpdateDraft(string lookup, int? actorId, UpdateDecisionDraftRequest request);
    DecisionDetailDto? Transition(string lookup, int? actorId, DecisionStatus targetStatus);
    bool Delete(string lookup, int? actorId);
    DecisionSignalListResponse ListSignals(string lookup);
    DecisionSignalDto? CreateSignal(string lookup, int? actorId, DecisionSignalInput input);
    DecisionSignalDto? UpdateSignal(string lookup, int signalId, DecisionSignalInput input);
    bool DeleteSignal(string lookup, int signalId);
    DecisionConnectionsResponse? GetConnections(string lookup);
    DecisionConnectionsResponse? UpdateConnections(string lookup, int? actorId, DecisionConnectionsUpdateRequest request);
    DecisionGraphResponse GetGraph(int projectId, bool allProjects, int userId, bool isSuperuser);
    DecisionTopicLabelResponse? UpsertTopicLabel(int projectId, string topic, string title);
    TopicLabelDeleteResult DeleteTopicLabel(int projectId, string topic);
    IReadOnlyList<DecisionReviewDto>? ListReviews(string lookup);
    DecisionDetailDto? CreateReview(string lookup, int? actorId, DecisionReviewInput input);
}

public enum TopicLabelDeleteResult
{
    Deleted,
    NotEmpty,
    NotFound
}

public sealed class DecisionValidationException(string field, string message) : Exception(message)
{
    public string Field { get; } = field;
}

public sealed class InMemoryDecisionRepository : IDecisionRepository
{
    private readonly ConcurrentDictionary<int, DecisionRecord> _decisions = new();
    private int _nextDecisionId = 1;
    private int _nextOptionId = 1;
    private int _nextSignalId = 1;

    public DecisionListResponse List(int? projectId, DecisionStatus? status, int pageSize, int pageToken)
    {
        var query = _decisions.Values
            .Where(decision => !projectId.HasValue || decision.ProjectId == projectId)
            .Where(decision => !status.HasValue || decision.Status == status)
            .OrderByDescending(decision => decision.LastEditedAt)
            .ToList();

        var items = query.Skip(pageToken).Take(pageSize + 1).ToList();
        var hasMore = items.Count > pageSize;
        var page = items.Take(pageSize).Select(ToSummary).ToList();

        return new DecisionListResponse(
            page,
            hasMore ? (pageToken + pageSize).ToString() : null
        );
    }

    public DecisionDetailDto? Get(string lookup)
    {
        return Find(lookup) is { } decision ? ToDetail(decision) : null;
    }

    public DecisionDetailDto Create(int? projectId, int? actorId, CreateDecisionDraftRequest request)
    {
        var now = DateTimeOffset.UtcNow;
        var id = Interlocked.Increment(ref _nextDecisionId) - 1;
        var projectSeq = NextProjectSequence(projectId);
        var decision = new DecisionRecord
        {
            Id = id,
            Slug = $"decision-{id}",
            ProjectSeq = projectSeq,
            Status = DecisionStatus.DRAFT,
            Title = request.Title,
            ContextSummary = request.ContextSummary,
            Reasoning = request.Reasoning,
            RiskLevel = request.RiskLevel,
            ConfidenceScore = request.ConfidenceScore,
            Topic = NormalizeTopic(request.Topic),
            CreatedAt = now,
            CreatedBy = actorId,
            LastEditedAt = now,
            LastEditedBy = actorId,
            ProjectId = projectId,
            ProjectName = projectId.HasValue ? $"Project {projectId}" : null,
            IsReferenceCase = request.IsReferenceCase,
            CreatedByAgent = request.CreatedByAgent,
            AgentSessionId = request.AgentSessionId,
            PlannedDecisionDate = request.PlannedDecisionDate,
            OriginMeeting = request.OriginMeetingId.HasValue
                ? new DecisionOriginMeetingDto(
                    request.OriginMeetingId.Value,
                    $"Meeting {request.OriginMeetingId.Value}",
                    $"/projects/{projectId}/meetings/{request.OriginMeetingId.Value}",
                    $"/projects/{projectId}/meetings/{request.OriginMeetingId.Value}",
                    projectId,
                    null,
                    null
                )
                : null,
            Signals = BuildSignals(id, actorId, now, request.Signals),
            Options = BuildOptions(id, now, request.Options),
        };
        _decisions[id] = decision;
        return ToDetail(decision);
    }

    public DecisionDetailDto? UpdateDraft(string lookup, int? actorId, UpdateDecisionDraftRequest request)
    {
        if (Find(lookup) is not { } existing)
        {
            return null;
        }
        if (existing.Status is not (DecisionStatus.PREDRAFT or DecisionStatus.DRAFT or DecisionStatus.AWAITING_APPROVAL))
        {
            return null;
        }

        var now = DateTimeOffset.UtcNow;
        var updated = existing with
        {
            Title = request.Title ?? existing.Title,
            ContextSummary = request.ContextSummary ?? existing.ContextSummary,
            Reasoning = request.Reasoning ?? existing.Reasoning,
            RiskLevel = request.RiskLevel ?? existing.RiskLevel,
            ConfidenceScore = request.ConfidenceScore ?? existing.ConfidenceScore,
            Topic = request.Topic is null ? existing.Topic : NormalizeTopic(request.Topic),
            IsReferenceCase = request.IsReferenceCase ?? existing.IsReferenceCase,
            PlannedDecisionDate = request.PlannedDecisionDate ?? existing.PlannedDecisionDate,
            LastEditedAt = now,
            LastEditedBy = actorId,
            Signals = request.Signals is null ? existing.Signals : BuildSignals(existing.Id, actorId, now, request.Signals),
            Options = request.Options is null ? existing.Options : BuildOptions(existing.Id, now, request.Options),
        };
        _decisions[existing.Id] = updated;
        return ToDetail(updated);
    }

    public DecisionDetailDto? Transition(string lookup, int? actorId, DecisionStatus targetStatus)
    {
        if (Find(lookup) is not { } existing)
        {
            return null;
        }

        var allowed = (existing.Status, targetStatus) switch
        {
            (DecisionStatus.PREDRAFT, DecisionStatus.DRAFT) => true,
            (DecisionStatus.PREDRAFT, DecisionStatus.COMMITTED) => true,
            (DecisionStatus.DRAFT, DecisionStatus.COMMITTED) => true,
            (DecisionStatus.AWAITING_APPROVAL, DecisionStatus.COMMITTED) => true,
            (DecisionStatus.COMMITTED, DecisionStatus.REVIEWED) => true,
            (DecisionStatus.COMMITTED, DecisionStatus.ARCHIVED) => true,
            (DecisionStatus.REVIEWED, DecisionStatus.ARCHIVED) => true,
            _ => false,
        };
        if (!allowed)
        {
            return null;
        }

        var now = DateTimeOffset.UtcNow;
        var updated = existing with
        {
            Status = targetStatus,
            LastEditedAt = now,
            LastEditedBy = actorId,
            CommittedAt = targetStatus == DecisionStatus.COMMITTED ? now : existing.CommittedAt,
        };
        _decisions[existing.Id] = updated;
        return ToDetail(updated);
    }

    public bool Delete(string lookup, int? actorId)
    {
        if (Find(lookup) is not { } existing)
        {
            return false;
        }
        return _decisions.TryRemove(existing.Id, out _);
    }

    public DecisionSignalListResponse ListSignals(string lookup)
    {
        var signals = Find(lookup)?.Signals ?? Array.Empty<DecisionSignalDto>();
        return new DecisionSignalListResponse(signals);
    }

    public DecisionSignalDto? CreateSignal(string lookup, int? actorId, DecisionSignalInput input)
    {
        if (Find(lookup) is not { } existing)
        {
            return null;
        }
        var now = DateTimeOffset.UtcNow;
        var signal = BuildSignals(existing.Id, actorId, now, new[] { input }).Single();
        _decisions[existing.Id] = existing with { Signals = existing.Signals.Concat(new[] { signal }).ToList() };
        return signal;
    }

    public DecisionSignalDto? UpdateSignal(string lookup, int signalId, DecisionSignalInput input)
    {
        if (Find(lookup) is not { } existing)
        {
            return null;
        }
        var signal = existing.Signals.FirstOrDefault(item => item.Id == signalId);
        if (signal is null)
        {
            return null;
        }
        var now = DateTimeOffset.UtcNow;
        var displayText = input.DisplayTextOverride
            ?? string.Join(" ", new[] { input.Metric, input.Movement, input.Period }.Where(value => !string.IsNullOrWhiteSpace(value)));
        var updated = signal with
        {
            UpdatedAt = now,
            Metric = input.Metric,
            Movement = input.Movement,
            Period = input.Period,
            Comparison = input.Comparison ?? "NONE",
            ScopeType = input.ScopeType,
            ScopeValue = input.ScopeValue,
            DeltaValue = input.DeltaValue,
            DeltaUnit = input.DeltaUnit,
            DisplayText = displayText,
            DisplayTextOverride = input.DisplayTextOverride,
        };
        _decisions[existing.Id] = existing with
        {
            Signals = existing.Signals.Select(item => item.Id == signalId ? updated : item).ToList(),
        };
        return updated;
    }

    public bool DeleteSignal(string lookup, int signalId)
    {
        if (Find(lookup) is not { } existing || existing.Signals.All(item => item.Id != signalId))
        {
            return false;
        }
        _decisions[existing.Id] = existing with
        {
            Signals = existing.Signals.Where(item => item.Id != signalId).ToList(),
        };
        return true;
    }

    public DecisionConnectionsResponse? GetConnections(string lookup)
    {
        if (Find(lookup) is not { } existing)
        {
            return null;
        }
        return new DecisionConnectionsResponse(
            new DecisionConnectionSelfDto(existing.Id, existing.ProjectSeq),
            Array.Empty<DecisionConnectionNodeDto>(),
            Array.Empty<DecisionConnectionEdgeDto>()
        );
    }

    public DecisionConnectionsResponse? UpdateConnections(string lookup, int? actorId, DecisionConnectionsUpdateRequest request)
    {
        return GetConnections(lookup);
    }

    public DecisionGraphResponse GetGraph(int projectId, bool allProjects, int userId, bool isSuperuser)
    {
        var projectIds = allProjects
            ? _decisions.Values
                .Where(decision => decision.ProjectId.HasValue)
                .Select(decision => decision.ProjectId!.Value)
                .Distinct()
                .ToHashSet()
            : new HashSet<int> { projectId };

        var nodes = _decisions.Values
            .Where(decision => decision.ProjectId.HasValue && projectIds.Contains(decision.ProjectId.Value))
            .OrderBy(decision => decision.ProjectId)
            .ThenBy(decision => decision.ProjectSeq)
            .ThenBy(decision => decision.Id)
            .Select(decision => new DecisionGraphNodeDto(
                decision.Id,
                decision.Slug,
                decision.Title,
                decision.Status,
                decision.ProjectSeq,
                decision.CreatedAt,
                decision.LastEditedAt,
                decision.ProjectId,
                decision.ProjectName,
                null,
                null,
                decision.Topic,
                TopicLabel(decision.Topic),
                decision.RiskLevel
            ))
            .ToList();

        var topics = nodes
            .Select(node => node.Topic)
            .Distinct()
            .OrderBy(TopicLabel)
            .Select(topic => new DecisionGraphTopicDto(topic, TopicLabel(topic), TopicLabel(topic)))
            .ToList();

        return new DecisionGraphResponse(nodes, Array.Empty<DecisionGraphEdgeDto>(), topics);
    }

    public DecisionTopicLabelResponse? UpsertTopicLabel(int projectId, string topic, string title)
    {
        var normalizedTopic = NormalizeTopic(topic);
        return new DecisionTopicLabelResponse(normalizedTopic, title.Trim(), TopicLabel(normalizedTopic));
    }

    public TopicLabelDeleteResult DeleteTopicLabel(int projectId, string topic)
    {
        var normalizedTopic = NormalizeTopic(topic);
        return _decisions.Values.Any(decision =>
            decision.ProjectId == projectId &&
            string.Equals(decision.Topic, normalizedTopic, StringComparison.OrdinalIgnoreCase))
            ? TopicLabelDeleteResult.NotEmpty
            : TopicLabelDeleteResult.Deleted;
    }

    public IReadOnlyList<DecisionReviewDto>? ListReviews(string lookup)
    {
        return Find(lookup) is null ? null : Array.Empty<DecisionReviewDto>();
    }

    public DecisionDetailDto? CreateReview(string lookup, int? actorId, DecisionReviewInput input)
    {
        return Transition(lookup, actorId, DecisionStatus.REVIEWED) ?? Get(lookup);
    }

    private DecisionRecord? Find(string lookup)
    {
        if (int.TryParse(lookup, out var id) && _decisions.TryGetValue(id, out var byId))
        {
            return byId;
        }
        return _decisions.Values.FirstOrDefault(decision => decision.Slug == lookup);
    }

    private int NextProjectSequence(int? projectId)
    {
        if (!projectId.HasValue)
        {
            return 1;
        }
        return _decisions.Values
            .Where(decision => decision.ProjectId == projectId)
            .Select(decision => decision.ProjectSeq)
            .DefaultIfEmpty(0)
            .Max() + 1;
    }

    private List<DecisionSignalDto> BuildSignals(
        int decisionId,
        int? actorId,
        DateTimeOffset now,
        IReadOnlyList<DecisionSignalInput>? inputs
    )
    {
        return (inputs ?? Array.Empty<DecisionSignalInput>())
            .Select(input =>
            {
                var id = Interlocked.Increment(ref _nextSignalId) - 1;
                var displayText = input.DisplayTextOverride
                    ?? string.Join(" ", new[] { input.Metric, input.Movement, input.Period }.Where(value => !string.IsNullOrWhiteSpace(value)));
                return new DecisionSignalDto(
                    id,
                    decisionId,
                    actorId,
                    now,
                    now,
                    input.Metric,
                    input.Movement,
                    input.Period,
                    input.Comparison ?? "NONE",
                    input.ScopeType,
                    input.ScopeValue,
                    input.DeltaValue,
                    input.DeltaUnit,
                    displayText,
                    input.DisplayTextOverride
                );
            })
            .ToList();
    }

    private List<DecisionOptionDto> BuildOptions(
        int decisionId,
        DateTimeOffset now,
        IReadOnlyList<DecisionOptionInput>? inputs
    )
    {
        return (inputs ?? Array.Empty<DecisionOptionInput>())
            .Select(input =>
            {
                var id = Interlocked.Increment(ref _nextOptionId) - 1;
                return new DecisionOptionDto(id, decisionId, input.Text, input.IsSelected, input.Order, now, now);
            })
            .OrderBy(option => option.Order)
            .ThenBy(option => option.CreatedAt)
            .ToList();
    }

    private static string NormalizeTopic(string? topic)
    {
        return string.IsNullOrWhiteSpace(topic) ? "other" : topic.Trim().ToLowerInvariant();
    }

    private static string TopicLabel(string topic)
    {
        return topic == "other" ? "Other" : string.Join(" ", topic.Split('_', '-').Select(ToTitleCase));
    }

    private static string ToTitleCase(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return value;
        }
        return char.ToUpperInvariant(value[0]) + value[1..];
    }

    private static DecisionSummaryDto ToSummary(DecisionRecord decision)
    {
        return new DecisionSummaryDto(
            decision.Id,
            decision.Slug,
            decision.ProjectSeq,
            decision.Status,
            decision.Title,
            decision.ContextSummary,
            decision.Options.FirstOrDefault(option => option.IsSelected)?.Text,
            decision.RiskLevel,
            decision.ConfidenceScore,
            decision.CreatedAt,
            decision.CreatedBy,
            decision.CommittedAt,
            decision.ProjectId,
            decision.ProjectName,
            decision.Topic,
            TopicLabel(decision.Topic),
            false,
            decision.CreatedByAgent,
            decision.AgentSessionId
        );
    }

    private static DecisionDetailDto ToDetail(DecisionRecord decision)
    {
        return new DecisionDetailDto(
            decision.Id,
            decision.Slug,
            decision.ProjectSeq,
            decision.Status,
            decision.Title,
            decision.ContextSummary,
            decision.Reasoning,
            decision.RiskLevel,
            decision.ConfidenceScore,
            decision.Topic,
            TopicLabel(decision.Topic),
            decision.CreatedAt,
            decision.CreatedBy,
            decision.LastEditedAt,
            decision.LastEditedBy,
            decision.CommittedAt,
            decision.ProjectId,
            decision.ProjectName,
            decision.IsReferenceCase,
            decision.CreatedByAgent,
            decision.AgentSessionId,
            decision.PlannedDecisionDate,
            decision.Signals,
            decision.Options,
            decision.OriginMeeting
        );
    }

    private sealed record DecisionRecord
    {
        public required int Id { get; init; }
        public required string Slug { get; init; }
        public required int ProjectSeq { get; init; }
        public required DecisionStatus Status { get; init; }
        public string? Title { get; init; }
        public string? ContextSummary { get; init; }
        public string? Reasoning { get; init; }
        public DecisionRiskLevel? RiskLevel { get; init; }
        public int? ConfidenceScore { get; init; }
        public required string Topic { get; init; }
        public required DateTimeOffset CreatedAt { get; init; }
        public int? CreatedBy { get; init; }
        public required DateTimeOffset LastEditedAt { get; init; }
        public int? LastEditedBy { get; init; }
        public DateTimeOffset? CommittedAt { get; init; }
        public int? ProjectId { get; init; }
        public string? ProjectName { get; init; }
        public bool IsReferenceCase { get; init; }
        public bool CreatedByAgent { get; init; }
        public Guid? AgentSessionId { get; init; }
        public DateTimeOffset? PlannedDecisionDate { get; init; }
        public DecisionOriginMeetingDto? OriginMeeting { get; init; }
        public required IReadOnlyList<DecisionSignalDto> Signals { get; init; }
        public required IReadOnlyList<DecisionOptionDto> Options { get; init; }
    }
}
