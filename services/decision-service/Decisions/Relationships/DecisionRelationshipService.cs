namespace DecisionService.Decisions.Relationships;

public interface IDecisionConnectionsOperations
{
    Task<DecisionConnectionsResponse?> GetConnectionsAsync(string lookup, CancellationToken ct);
    Task<DecisionConnectionsResponse?> UpdateConnectionsAsync(
        string lookup, int? actorId, bool isSuperuser, DecisionConnectionsUpdateRequest request, CancellationToken ct);
}

public sealed class DecisionRelationshipService(
    TenantConnectionFactory connections, PostgresDecisionRelationshipRepository repository) : IDecisionConnectionsOperations
{
    public async Task<DecisionConnectionsResponse?> GetConnectionsAsync(string lookup, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var source = await repository.FindSourceAsync(session, lookup, ct);
        return source is { } decision
            ? await repository.BuildConnectionsPayloadAsync(session, decision.Id, decision.ProjectId, decision.ProjectSeq, ct)
            : null;
    }

    public async Task<DecisionConnectionsResponse?> UpdateConnectionsAsync(
        string lookup, int? actorId, bool isSuperuser, DecisionConnectionsUpdateRequest request, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var payload = await UpdateConnectionsAsync(session, lookup, actorId, isSuperuser, request, ct);
        if (payload is not null)
        {
            await session.CommitAsync();
        }
        return payload;
    }

    internal async Task<DecisionConnectionsResponse?> UpdateConnectionsAsync(
        TenantSession session, string lookup, int? actorId, bool isSuperuser, DecisionConnectionsUpdateRequest request,
        CancellationToken ct)
    {
        var source = await repository.FindSourceAsync(session, lookup, ct);
        if (source is not { ProjectId: int projectId } decision)
        {
            return null;
        }

        var targetIds = await ResolveConnectionTargetIdsAsync(session, decision, projectId, actorId, isSuperuser, request, ct);
        if (targetIds is null)
        {
            return null;
        }

        await repository.ReplaceRelatedEdgesAsync(session, decision.Id, targetIds, actorId, ct);
        return await repository.BuildConnectionsPayloadAsync(session, decision.Id, projectId, decision.ProjectSeq, ct);
    }

    private async Task<IReadOnlyList<int>?> ResolveConnectionTargetIdsAsync(
        TenantSession session, ConnectionSource source, int projectId, int? actorId, bool isSuperuser,
        DecisionConnectionsUpdateRequest request, CancellationToken ct)
    {
        if (request.ConnectedDecisionIds is { Count: > 0 } && request.ConnectedDecisionSeqs is { Count: > 0 })
        {
            return null;
        }

        if (request.ConnectedDecisionIds is { } ids)
        {
            var requested = ids.Where(id => id != source.Id).Distinct().ToArray();
            if (requested.Length == 0)
            {
                return Array.Empty<int>();
            }

            var allowed = await repository.FindAllowedTargetIdsAsync(session, projectId, requested, actorId, isSuperuser, ct);
            return allowed.Count == requested.Length ? allowed : null;
        }

        var seqs = (request.ConnectedDecisionSeqs ?? Array.Empty<int>())
            .Where(seq => seq != source.ProjectSeq).Distinct().ToArray();
        if (seqs.Length == 0)
        {
            return Array.Empty<int>();
        }

        var targets = await repository.FindTargetsBySequenceAsync(session, projectId, seqs, ct);
        return targets.Count == seqs.Length ? targets : null;
    }

    internal async Task ReplaceParentEdgesAsync(
        TenantSession session, int decisionId, int projectId, int? actorId, IReadOnlyList<int>? parentDecisionIds,
        CancellationToken ct)
    {
        if (parentDecisionIds is null)
        {
            return;
        }

        if (parentDecisionIds.Contains(decisionId))
        {
            throw new DecisionValidationException("parentDecisionIds", "Decision cannot reference itself.");
        }

        var candidates = parentDecisionIds.Distinct().ToArray();
        if (candidates.Length > 0)
        {
            if (await repository.CountValidParentsAsync(session, projectId, candidates, ct) != candidates.Length)
            {
                throw new DecisionValidationException(
                    "parentDecisionIds", "Each parent must be an existing, non-deleted decision in the same project.");
            }

            if (await repository.IntroducesCycleAsync(session, decisionId, candidates, ct))
            {
                throw new DecisionValidationException("parentDecisionIds", "Adding this parent introduces a cycle.");
            }
        }

        await repository.ReplaceFollowUpEdgesAsync(session, decisionId, projectId, actorId, candidates, ct);
    }
}
