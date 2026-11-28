using System.Text.Json;
using Npgsql;
using NpgsqlTypes;

namespace DecisionService.Decisions;

public sealed class PostgresDecisionRepository(TenantConnectionFactory connections) : IDecisionRepository
{
    public async Task<DecisionListResponse> ListAsync(int? projectId, DecisionStatus? status, int pageSize, int pageToken, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT
                d.id,
                d.slug,
                d.project_seq,
                d.status,
                d.title,
                d.context_summary,
                selected_option.text AS selected_option_text,
                d.risk_level,
                d.confidence,
                d.created_at,
                d.author_id,
                d.committed_at,
                d.project_id,
                p.name AS project_name,
                d.topic,
                d.created_by_agent,
                d.agent_session_id,
                EXISTS (
                    SELECT 1 FROM decision_reviews r WHERE r.decision_id = d.id AND r.is_deleted = false
                ) AS has_reviews
            FROM decisions d
            LEFT JOIN core_project p ON p.id = d.project_id
            LEFT JOIN LATERAL (
                SELECT o.text
                FROM decision_options o
                WHERE o.decision_id = d.id AND o.is_selected = true AND o.is_deleted = false
                ORDER BY o."order", o.created_at
                LIMIT 1
            ) selected_option ON true
            WHERE d.is_deleted = false
              AND (@project_id IS NULL OR d.project_id = @project_id)
              AND (@status IS NULL OR d.status = @status)
            ORDER BY d.updated_at DESC, d.id DESC
            OFFSET @offset
            LIMIT @limit
            """;
        AddNullableInteger(command, "project_id", projectId);
        AddNullableText(command, "status", status?.ToString());
        command.Parameters.AddWithValue("offset", pageToken);
        command.Parameters.AddWithValue("limit", pageSize + 1);

        var items = new List<DecisionSummaryDto>();
        await using var reader = await command.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            items.Add(new DecisionSummaryDto(
                reader.GetInt32(reader.GetOrdinal("id")),
                GetNullableString(reader, "slug") ?? Slug(reader.GetInt32(reader.GetOrdinal("id"))),
                reader.GetInt32(reader.GetOrdinal("project_seq")),
                ParseStatus(reader.GetString(reader.GetOrdinal("status"))),
                GetNullableString(reader, "title"),
                GetNullableString(reader, "context_summary"),
                GetNullableString(reader, "selected_option_text"),
                ParseRisk(GetNullableString(reader, "risk_level")),
                GetNullableInt32(reader, "confidence"),
                GetDateTimeOffset(reader, "created_at")!.Value,
                GetNullableInt32(reader, "author_id"),
                GetDateTimeOffset(reader, "committed_at"),
                GetNullableInt32(reader, "project_id"),
                GetNullableString(reader, "project_name"),
                NormalizeTopic(GetNullableString(reader, "topic")),
                TopicLabel(NormalizeTopic(GetNullableString(reader, "topic"))),
                reader.GetBoolean(reader.GetOrdinal("has_reviews")),
                reader.GetBoolean(reader.GetOrdinal("created_by_agent")),
                GetNullableGuid(reader, "agent_session_id")
            ));
        }

        var hasMore = items.Count > pageSize;
        return new DecisionListResponse(
            items.Take(pageSize).ToList(),
            hasMore ? (pageToken + pageSize).ToString() : null
        );
    }

    public async Task<DecisionDetailDto?> GetAsync(string lookup, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        return await GetAsync(connection, lookup, ct);
    }

    public async Task<DecisionDetailDto> CreateAsync(int? projectId, int? actorId, CreateDecisionDraftRequest request, CancellationToken ct)
    {
        if (projectId is null)
        {
            throw new InvalidOperationException("Project context is required.");
        }

        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var originMeeting = request.OriginMeetingId.HasValue
            ? await ResolveOriginMeetingAsync(connection, request.OriginMeetingId.Value, projectId.Value, ct)
            : null;
        var transaction = session.Transaction;
        var now = DateTimeOffset.UtcNow;
        var projectSeq = await NextProjectSequenceAsync(connection, projectId.Value, ct);
        var topic = NormalizeTopic(request.Topic);

        using var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = """
            INSERT INTO decisions (
                created_at,
                updated_at,
                is_deleted,
                title,
                context_summary,
                reasoning,
                risk_level,
                confidence,
                status,
                requires_approval,
                planned_decision_date,
                committed_at,
                approved_at,
                is_reference_case,
                created_by_agent,
                agent_session_id,
                is_pre_draft,
                project_id,
                project_seq,
                author_id,
                last_edited_by_id,
                committed_by_id,
                approved_by_id,
                topic,
                slug
            )
            VALUES (
                @now,
                @now,
                false,
                @title,
                @context_summary,
                @reasoning,
                @risk_level,
                @confidence,
                'DRAFT',
                false,
                @planned_decision_date,
                NULL,
                NULL,
                @is_reference_case,
                false,
                NULL,
                false,
                @project_id,
                @project_seq,
                @actor_id,
                @actor_id,
                NULL,
                NULL,
                @topic,
                @slug
            )
            RETURNING id
            """;
        command.Parameters.AddWithValue("now", now);
        AddNullableText(command, "title", request.Title);
        AddNullableText(command, "context_summary", request.ContextSummary);
        AddNullableText(command, "reasoning", request.Reasoning);
        AddNullableText(command, "risk_level", request.RiskLevel?.ToString());
        AddNullableInteger(command, "confidence", request.ConfidenceScore);
        AddNullableTimestamp(command, "planned_decision_date", request.PlannedDecisionDate);
        command.Parameters.AddWithValue("is_reference_case", request.IsReferenceCase);
        command.Parameters.AddWithValue("project_id", projectId.Value);
        command.Parameters.AddWithValue("project_seq", projectSeq);
        AddNullableInteger(command, "actor_id", actorId);
        command.Parameters.AddWithValue("topic", topic);
        command.Parameters.AddWithValue("slug", $"decision-{Guid.NewGuid():N}");

        var id = Convert.ToInt32(await command.ExecuteScalarAsync(ct));
        await ReplaceOptionsAsync(connection, transaction, id, now, request.Options, ct);
        await ReplaceSignalsAsync(connection, transaction, id, actorId, now, request.Signals, ct);
        await ReplaceParentEdgesAsync(connection, transaction, id, projectId.Value, actorId, request.ParentDecisionIds, ct);
        if (originMeeting is not null)
        {
            await CreateMeetingDecisionOriginAsync(connection, transaction, originMeeting.Id, id, actorId, now, ct);
        }
        var created = (await GetAsync(connection, id.ToString(), ct))!;
        await session.CommitAsync();
        return created;
    }

    public async Task<DecisionDetailDto?> UpdateDraftAsync(string lookup, int? actorId, UpdateDecisionDraftRequest request, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var transaction = session.Transaction;
        var existing = await GetAsync(connection, lookup, ct);
        if (existing is null ||
            existing.Status is not (
                DecisionStatus.PREDRAFT or
                DecisionStatus.DRAFT or
                DecisionStatus.AWAITING_APPROVAL or
                DecisionStatus.COMMITTED or
                DecisionStatus.REVIEWED or
                DecisionStatus.ARCHIVED))
        {
            return null;
        }

        if (existing.Status is (DecisionStatus.COMMITTED or DecisionStatus.REVIEWED or DecisionStatus.ARCHIVED))
        {
            var protectedFields = new (string Field, bool IsProvided)[]
            {
                ("title", request.Title is not null && request.Title != existing.Title),
                ("contextSummary", request.ContextSummary is not null && request.ContextSummary != existing.ContextSummary),
                ("reasoning", request.Reasoning is not null && request.Reasoning != existing.Reasoning),
                ("riskLevel", request.RiskLevel is not null && request.RiskLevel != existing.RiskLevel),
                ("confidenceScore", request.ConfidenceScore is not null && request.ConfidenceScore != existing.ConfidenceScore),
                ("plannedDecisionDate", request.PlannedDecisionDate is not null && request.PlannedDecisionDate != existing.PlannedDecisionDate),
                ("options", request.Options is not null),
                ("signals", request.Signals is not null),
                ("parentDecisionIds", request.ParentDecisionIds is not null)

            };
            var offendingField = protectedFields.FirstOrDefault(field => field.IsProvided).Field;

            if (offendingField is not null)
            {
                throw new DecisionValidationException(offendingField,
                    $"Cannot modify {offendingField} because this decision is {existing.Status}.");
            }
        }

        var now = DateTimeOffset.UtcNow;
        using var command = connection.CreateCommand();
        command.Transaction = transaction;

        command.Parameters.AddWithValue("id", existing.Id);
        command.Parameters.AddWithValue("now", now);
        AddNullableText(command, "topic", request.Topic is null ? null : NormalizeTopic(request.Topic));
        AddNullableBoolean(command, "is_reference_case", request.IsReferenceCase);
        AddNullableInteger(command, "actor_id", actorId);

        if (existing.Status is (DecisionStatus.COMMITTED or DecisionStatus.REVIEWED or DecisionStatus.ARCHIVED))
        {
            command.CommandText = """
            UPDATE decisions
            SET
                updated_at = @now,
                topic = COALESCE(@topic, topic),
                is_reference_case = COALESCE(@is_reference_case, is_reference_case),
                last_edited_by_id = @actor_id
            WHERE id = @id
                AND is_deleted = false
                AND status IN ('COMMITTED', 'REVIEWED', 'ARCHIVED')
            """;

        }
        else
        {
            command.CommandText = """
            UPDATE decisions
            SET
                updated_at = @now,
                title = COALESCE(@title, title),
                context_summary = COALESCE(@context_summary, context_summary),
                reasoning = COALESCE(@reasoning, reasoning),
                risk_level = COALESCE(@risk_level, risk_level),
                confidence = COALESCE(@confidence, confidence),
                topic = COALESCE(@topic, topic),
                is_reference_case = COALESCE(@is_reference_case, is_reference_case),
                planned_decision_date = COALESCE(@planned_decision_date, planned_decision_date),
                last_edited_by_id = @actor_id
            WHERE id = @id
                AND is_deleted = false
                AND status IN ('PREDRAFT', 'DRAFT', 'AWAITING_APPROVAL')
            """;
            AddNullableText(command, "title", request.Title);
            AddNullableText(command, "context_summary", request.ContextSummary);
            AddNullableText(command, "reasoning", request.Reasoning);
            AddNullableText(command, "risk_level", request.RiskLevel?.ToString());
            AddNullableInteger(command, "confidence", request.ConfidenceScore);
            AddNullableTimestamp(command, "planned_decision_date", request.PlannedDecisionDate);
        }

        var rowAffected = await command.ExecuteNonQueryAsync(ct);
        if (rowAffected != 1)
        {
            throw new DecisionValidationException("status",
                "Decision could not be updated because its status may have changed.");
        }

        if (existing.Status is (DecisionStatus.PREDRAFT or DecisionStatus.DRAFT or DecisionStatus.AWAITING_APPROVAL))
        {
            if (request.Options is not null)
            {
                await ReplaceOptionsAsync(connection, transaction, existing.Id, now, request.Options, ct);
            }
            if (request.Signals is not null)
            {
                await ReplaceSignalsAsync(connection, transaction, existing.Id, actorId, now, request.Signals, ct);
            }
            if (request.ParentDecisionIds is not null && existing.ProjectId is not null)
            {
                await ReplaceParentEdgesAsync(connection, transaction, existing.Id, existing.ProjectId.Value, actorId, request.ParentDecisionIds, ct);
            }
        }

        var updated = await GetAsync(connection, existing.Id.ToString(), ct);
        await session.CommitAsync();
        return updated;
    }

    public async Task<DecisionDetailDto?> TransitionAsync(
        string lookup,
        int? actorId,
        DecisionTransitionAction action,
        CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var transaction = session.Transaction;
        var existing = await GetAsync(connection, lookup, ct);
        if (existing is null)
        {
            return null;
        }

        var allowed = (existing.Status, action) switch
        {
            (DecisionStatus.PREDRAFT, DecisionTransitionAction.PromoteToDraft) => true,
            (DecisionStatus.PREDRAFT, DecisionTransitionAction.Commit) => true,
            (DecisionStatus.DRAFT, DecisionTransitionAction.Commit) => true,
            (DecisionStatus.AWAITING_APPROVAL, DecisionTransitionAction.Approve) => true,
            (DecisionStatus.COMMITTED, DecisionTransitionAction.MarkReviewed) => true,
            (DecisionStatus.COMMITTED, DecisionTransitionAction.Archive) => true,
            (DecisionStatus.REVIEWED, DecisionTransitionAction.Archive) => true,
            _ => false,
        };
        if (!allowed)
        {
            return null;
        }
        if (action == DecisionTransitionAction.Approve && actorId is int approverId)
        {
            using var checkSubmitter = connection.CreateCommand();
            checkSubmitter.Transaction = transaction;
            checkSubmitter.CommandText = """
        SELECT committed_by_id = @approver_id
        FROM decisions
        WHERE id = @id
        """;
            checkSubmitter.Parameters.AddWithValue("approver_id", approverId);
            checkSubmitter.Parameters.AddWithValue("id", existing.Id);

            if (await checkSubmitter.ExecuteScalarAsync(ct) is true)
            {
                throw new DecisionSelfApprovalException();
            }
        }
        DecisionStatus targetStatus;
        switch (action)
        {
            case DecisionTransitionAction.Commit:
                targetStatus = existing.RiskLevel == DecisionRiskLevel.HIGH
                ? DecisionStatus.AWAITING_APPROVAL
                : DecisionStatus.COMMITTED;
                break;
            case DecisionTransitionAction.Approve:
                targetStatus = DecisionStatus.COMMITTED;
                break;
            case DecisionTransitionAction.PromoteToDraft:
                targetStatus = DecisionStatus.DRAFT;
                break;
            case DecisionTransitionAction.MarkReviewed:
                targetStatus = DecisionStatus.REVIEWED;
                break;
            case DecisionTransitionAction.Archive:
                targetStatus = DecisionStatus.ARCHIVED;
                break;
            default:
                return null;
        }
        if (action == DecisionTransitionAction.Commit ||
    action == DecisionTransitionAction.Approve)
        {
            if (string.IsNullOrWhiteSpace(existing.Title))
            {
                throw new DecisionValidationException(
                    "title",
                    "Title is required before commit."
                );
            }
        }
        var now = DateTimeOffset.UtcNow;
        using var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = """
            UPDATE decisions
            SET
                status = @status,
                updated_at = @now,
                last_edited_by_id = @actor_id,
                requires_approval = CASE
                    WHEN @action = 'Commit' THEN @requires_approval
                    ELSE requires_approval
                END,
                committed_at = CASE
                WHEN @action = 'Commit' THEN @now
                ELSE committed_at
                END,
                committed_by_id = CASE
                WHEN @action = 'Commit' THEN @actor_id
                ELSE committed_by_id END,
                approved_at = CASE WHEN @action = 'Approve' THEN @now
                ELSE approved_at END,
                approved_by_id = CASE
                WHEN @action = 'Approve' THEN @actor_id
                ELSE approved_by_id END
                WHERE id = @id
                    AND is_deleted = false
                    AND status = @from_status
                    AND title IS NOT DISTINCT FROM @from_title
                    AND risk_level IS NOT DISTINCT FROM @from_risk_level
                    AND (
                    @action <> 'Approve'
                    OR (committed_by_id = @actor_id) IS NOT TRUE
                    )
            """;
        command.Parameters.AddWithValue("action", action.ToString());
        command.Parameters.AddWithValue("requires_approval", existing.RiskLevel == DecisionRiskLevel.HIGH);
        command.Parameters.AddWithValue("id", existing.Id);
        command.Parameters.AddWithValue("status", targetStatus.ToString());
        command.Parameters.AddWithValue("from_status", existing.Status.ToString());
        AddNullableText(command, "from_title", existing.Title);
        AddNullableText(
        command,
        "from_risk_level",
        existing.RiskLevel?.ToString()
        );
        command.Parameters.AddWithValue("now", now);
        AddNullableInteger(command, "actor_id", actorId);
        var affectedRows = await command.ExecuteNonQueryAsync(ct);
        if (affectedRows == 0)
        {
            if (action is DecisionTransitionAction.Commit or DecisionTransitionAction.Approve)
            {
                throw new DecisionTransitionConflictException(
                    "The decision changed. Refresh and try again."
                );
            }
            return null;
        }

        if (affectedRows != 1)
        {
            throw new InvalidOperationException(
                $"Expected one decision to be updated, but updated {affectedRows}."
            );
        }

        await RecordTransitionAsync(connection, transaction, existing.Id, existing.Status, targetStatus, actorId, now, ct);
        var updated = await GetAsync(connection, existing.Id.ToString(), ct);

        if (updated is null)
        {
            throw new InvalidOperationException(
                "Decision could not be retrieved after status update.");
        }

        if (action == DecisionTransitionAction.Commit)
        {
            var snapshot = DecisionValidationSnapshot.Build(updated, now);
            await CreateCommitRecordAsync(connection, transaction, existing.Id, actorId, now, snapshot, ct);
        }

        await session.CommitAsync();
        return updated;
    }

    public async Task<bool> DeleteAsync(string lookup, int? actorId, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var existing = await GetAsync(connection, lookup, ct);
        if (existing is null)
        {
            return false;
        }
        using var command = connection.CreateCommand();
        command.CommandText = """
            UPDATE decisions
            SET is_deleted = true, updated_at = @now, last_edited_by_id = @actor_id
            WHERE id = @id AND is_deleted = false
            """;
        command.Parameters.AddWithValue("id", existing.Id);
        command.Parameters.AddWithValue("now", DateTimeOffset.UtcNow);
        AddNullableInteger(command, "actor_id", actorId);
        var deleted = await command.ExecuteNonQueryAsync(ct) > 0;
        await session.CommitAsync();
        return deleted;
    }

    public async Task<DecisionSignalListResponse> ListSignalsAsync(string lookup, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var decision = await GetAsync(connection, lookup, ct);
        return new DecisionSignalListResponse(decision is null ? Array.Empty<DecisionSignalDto>() : await ListSignalsAsync(connection, decision.Id, ct));
    }

    public async Task<DecisionSignalDto?> CreateSignalAsync(string lookup, int? actorId, DecisionSignalInput input, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var decision = await GetAsync(connection, lookup, ct);
        if (decision is null)
        {
            return null;
        }

        await EnsureSignalOptionsEnabledAsync(connection, session.Transaction, decision.Id, [input], ct);
        var now = DateTimeOffset.UtcNow;
        var displayText = input.DisplayTextOverride
            ?? string.Join(" ", new[] { input.Metric, input.Movement, input.Period }.Where(value => !string.IsNullOrWhiteSpace(value)));
        using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO decision_signals (
                created_at,
                updated_at,
                is_deleted,
                decision_id,
                author_id,
                metric,
                movement,
                period,
                comparison,
                scope_type,
                scope_value,
                delta_value,
                delta_unit,
                display_text,
                display_text_override
            )
            VALUES (
                @now,
                @now,
                false,
                @decision_id,
                @actor_id,
                @metric,
                @movement,
                @period,
                @comparison,
                @scope_type,
                @scope_value,
                @delta_value,
                @delta_unit,
                @display_text,
                @display_text_override
            )
            RETURNING id
            """;
        command.Parameters.AddWithValue("now", now);
        command.Parameters.AddWithValue("decision_id", decision.Id);
        AddNullableInteger(command, "actor_id", actorId);
        AddNullableText(command, "metric", input.Metric);
        AddNullableText(command, "movement", input.Movement);
        AddNullableText(command, "period", input.Period);
        command.Parameters.AddWithValue("comparison", input.Comparison ?? "NONE");
        AddNullableText(command, "scope_type", input.ScopeType);
        AddNullableText(command, "scope_value", input.ScopeValue);
        AddNullableDecimal(command, "delta_value", input.DeltaValue);
        AddNullableText(command, "delta_unit", input.DeltaUnit);
        command.Parameters.AddWithValue("display_text", displayText);
        AddNullableText(command, "display_text_override", input.DisplayTextOverride);
        var signalId = Convert.ToInt32(await command.ExecuteScalarAsync(ct));
        var created = (await ListSignalsAsync(connection, decision.Id, ct)).FirstOrDefault(signal => signal.Id == signalId);
        await session.CommitAsync();
        return created;
    }

    public async Task<DecisionSignalDto?> UpdateSignalAsync(string lookup, int signalId, DecisionSignalPatch patch, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var decision = await GetAsync(connection, lookup, ct);
        if (decision is null)
        {
            return null;
        }

        var existing = (await ListSignalsAsync(connection, decision.Id, ct, signalId, session.Transaction)).SingleOrDefault();
        if (existing is null)
        {
            return null;
        }

        await EnsureSignalOptionsEnabledAsync(connection, session.Transaction, decision.Id, [patch.Input], ct);
        var updated = patch.Apply(existing);
        using var command = connection.CreateCommand();
        command.Transaction = session.Transaction;
        command.CommandText = """
            UPDATE decision_signals
            SET
                updated_at = @now,
                metric = @metric,
                movement = @movement,
                period = @period,
                comparison = @comparison,
                scope_type = @scope_type,
                scope_value = @scope_value,
                delta_value = @delta_value,
                delta_unit = @delta_unit,
                display_text = @display_text,
                display_text_override = @display_text_override
            WHERE id = @signal_id AND decision_id = @decision_id AND is_deleted = false
            """;
        command.Parameters.AddWithValue("now", updated.UpdatedAt);
        command.Parameters.AddWithValue("signal_id", signalId);
        command.Parameters.AddWithValue("decision_id", decision.Id);
        AddNullableText(command, "metric", updated.Metric);
        AddNullableText(command, "movement", updated.Movement);
        AddNullableText(command, "period", updated.Period);
        AddNullableText(command, "comparison", updated.Comparison);
        AddNullableText(command, "scope_type", updated.ScopeType);
        AddNullableText(command, "scope_value", updated.ScopeValue);
        AddNullableDecimal(command, "delta_value", updated.DeltaValue);
        AddNullableText(command, "delta_unit", updated.DeltaUnit);
        AddNullableText(command, "display_text", updated.DisplayText);
        AddNullableText(command, "display_text_override", updated.DisplayTextOverride);
        if (await command.ExecuteNonQueryAsync(ct) == 0)
        {
            return null;
        }
        var saved = (await ListSignalsAsync(connection, decision.Id, ct, signalId, session.Transaction)).Single();
        await session.CommitAsync();
        return saved;
    }

    public async Task<bool> DeleteSignalAsync(string lookup, int signalId, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var decision = await GetAsync(connection, lookup, ct);
        if (decision is null)
        {
            return false;
        }
        using var command = connection.CreateCommand();
        command.CommandText = """
            UPDATE decision_signals
            SET is_deleted = true, updated_at = @now
            WHERE id = @signal_id AND decision_id = @decision_id AND is_deleted = false
            """;
        command.Parameters.AddWithValue("now", DateTimeOffset.UtcNow);
        command.Parameters.AddWithValue("signal_id", signalId);
        command.Parameters.AddWithValue("decision_id", decision.Id);
        var deleted = await command.ExecuteNonQueryAsync(ct) > 0;
        await session.CommitAsync();
        return deleted;
    }

    public async Task<DecisionConnectionsResponse?> GetConnectionsAsync(string lookup, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var decision = await GetAsync(connection, lookup, ct);
        return decision is null ? null : await BuildConnectionsPayloadAsync(connection, decision.Id, decision.ProjectId, decision.ProjectSeq, ct);
    }

    public async Task<DecisionConnectionsResponse?> UpdateConnectionsAsync(string lookup, int? actorId, DecisionConnectionsUpdateRequest request, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var transaction = session.Transaction;
        var decision = await GetAsync(connection, lookup, ct);
        if (decision is null || decision.ProjectId is null)
        {
            return null;
        }

        var targetIds = await ResolveConnectionTargetIdsAsync(connection, decision, request, ct);
        if (targetIds is null)
        {
            return null;
        }

        using (var deleteCommand = connection.CreateCommand())
        {
            deleteCommand.Transaction = transaction;
            deleteCommand.CommandText = """
                DELETE FROM decision_edges
                WHERE edge_type = 'RELATED'
                  AND (from_decision_id = @decision_id OR to_decision_id = @decision_id)
                """;
            deleteCommand.Parameters.AddWithValue("decision_id", decision.Id);
            await deleteCommand.ExecuteNonQueryAsync(ct);
        }

        foreach (var targetId in targetIds.Where(id => id != decision.Id).Distinct())
        {
            var fromId = Math.Min(decision.Id, targetId);
            var toId = Math.Max(decision.Id, targetId);
            using var insertCommand = connection.CreateCommand();
            insertCommand.Transaction = transaction;
            insertCommand.CommandText = """
                INSERT INTO decision_edges (
                    created_at,
                    updated_at,
                    is_deleted,
                    from_decision_id,
                    to_decision_id,
                    created_by_id,
                    edge_type
                )
                VALUES (@now, @now, false, @from_id, @to_id, @actor_id, 'RELATED')
                ON CONFLICT (from_decision_id, to_decision_id) DO UPDATE SET edge_type = 'RELATED', updated_at = EXCLUDED.updated_at
                """;
            insertCommand.Parameters.AddWithValue("now", DateTimeOffset.UtcNow);
            insertCommand.Parameters.AddWithValue("from_id", fromId);
            insertCommand.Parameters.AddWithValue("to_id", toId);
            AddNullableInteger(insertCommand, "actor_id", actorId);
            await insertCommand.ExecuteNonQueryAsync(ct);
        }

        var payload = await BuildConnectionsPayloadAsync(connection, decision.Id, decision.ProjectId, decision.ProjectSeq, ct);
        await session.CommitAsync();
        return payload;
    }

    public async Task<DecisionGraphResponse> GetGraphAsync(int projectId, bool allProjects, int userId, bool isSuperuser, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var projectIds = await ResolveGraphProjectIdsAsync(connection, projectId, allProjects, userId, isSuperuser, ct);
        if (projectIds.Count == 0)
        {
            return new DecisionGraphResponse(Array.Empty<DecisionGraphNodeDto>(), Array.Empty<DecisionGraphEdgeDto>(), Array.Empty<DecisionGraphTopicDto>());
        }

        var topicLabels = await LoadTopicLabelsAsync(connection, projectIds, ct);
        using var nodesCommand = connection.CreateCommand();
        nodesCommand.CommandText = """
            SELECT
                d.id,
                d.slug,
                d.title,
                d.status,
                d.project_seq,
                d.created_at,
                d.updated_at,
                d.project_id,
                p.name AS project_name,
                p.objectives AS project_objectives,
                p.project_type AS project_type,
                p.description AS project_description,
                d.topic,
                d.risk_level
            FROM decisions d
            LEFT JOIN core_project p ON p.id = d.project_id
            WHERE d.project_id = ANY(@project_ids)
              AND d.is_deleted = false
            ORDER BY d.project_id, d.project_seq, d.id
            """;
        nodesCommand.Parameters.AddWithValue("project_ids", projectIds.ToArray());

        var nodes = new List<DecisionGraphNodeDto>();
        await using (var reader = await nodesCommand.ExecuteReaderAsync(ct))
        {
            while (await reader.ReadAsync(ct))
            {
                var topic = NormalizeTopic(GetNullableString(reader, "topic"));
                var label = topicLabels.TryGetValue((reader.GetInt32(reader.GetOrdinal("project_id")), topic), out var configuredLabel)
                    ? configuredLabel
                    : TopicLabel(topic);
                nodes.Add(new DecisionGraphNodeDto(
                    reader.GetInt32(reader.GetOrdinal("id")),
                    GetNullableString(reader, "slug") ?? Slug(reader.GetInt32(reader.GetOrdinal("id"))),
                    GetNullableString(reader, "title"),
                    ParseStatus(reader.GetString(reader.GetOrdinal("status"))),
                    GetNullableInt32(reader, "project_seq"),
                    GetDateTimeOffset(reader, "created_at")!.Value,
                    GetDateTimeOffset(reader, "updated_at")!.Value,
                    GetNullableInt32(reader, "project_id"),
                    GetNullableString(reader, "project_name"),
                    ProjectTheme(
                        GetNullableString(reader, "project_name"),
                        GetNullableString(reader, "project_objectives"),
                        GetNullableString(reader, "project_type")
                    ),
                    ProjectSubtitle(
                        GetNullableString(reader, "project_description"),
                        GetNullableString(reader, "project_type")
                    ),
                    topic,
                    label,
                    ParseRisk(GetNullableString(reader, "risk_level"))
                ));
            }
        }

        using var edgesCommand = connection.CreateCommand();
        edgesCommand.CommandText = """
            SELECT e.from_decision_id, e.to_decision_id, e.edge_type
            FROM decision_edges e
            JOIN decisions fd ON fd.id = e.from_decision_id
            JOIN decisions td ON td.id = e.to_decision_id
            WHERE fd.project_id = ANY(@project_ids)
              AND td.project_id = ANY(@project_ids)
              AND e.is_deleted = false
              AND fd.is_deleted = false
              AND td.is_deleted = false
            ORDER BY e.from_decision_id, e.to_decision_id
            """;
        edgesCommand.Parameters.AddWithValue("project_ids", projectIds.ToArray());
        var edges = new List<DecisionGraphEdgeDto>();
        await using (var reader = await edgesCommand.ExecuteReaderAsync(ct))
        {
            while (await reader.ReadAsync(ct))
            {
                edges.Add(new DecisionGraphEdgeDto(
                    reader.GetInt32(reader.GetOrdinal("from_decision_id")),
                    reader.GetInt32(reader.GetOrdinal("to_decision_id")),
                    GetNullableString(reader, "edge_type")
                ));
            }
        }

        var topics = nodes
            .Select(node => node.Topic)
            .Distinct()
            .OrderBy(topic => TopicLabelForAnyProject(topicLabels, topic))
            .Select(topic => new DecisionGraphTopicDto(topic, TopicLabelForAnyProject(topicLabels, topic), TopicLabel(topic)))
            .ToList();

        return new DecisionGraphResponse(nodes, edges, topics);
    }

    public async Task<DecisionTopicLabelResponse?> UpsertTopicLabelAsync(int projectId, string topic, string title, CancellationToken ct)
    {
        var normalizedTopic = NormalizeTopic(topic);
        var trimmedTitle = title.Trim();
        if (string.IsNullOrWhiteSpace(trimmedTitle) || trimmedTitle.Length > 80)
        {
            return null;
        }

        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        using var command = connection.CreateCommand();
        command.CommandText = """
            INSERT INTO decision_topic_labels (created_at, updated_at, is_deleted, project_id, topic, title)
            VALUES (@now, @now, false, @project_id, @topic, @title)
            ON CONFLICT (project_id, topic)
            DO UPDATE SET title = EXCLUDED.title, updated_at = EXCLUDED.updated_at, is_deleted = false
            RETURNING topic, title
            """;
        command.Parameters.AddWithValue("now", DateTimeOffset.UtcNow);
        command.Parameters.AddWithValue("project_id", projectId);
        command.Parameters.AddWithValue("topic", normalizedTopic);
        command.Parameters.AddWithValue("title", trimmedTitle);

        await using var reader = await command.ExecuteReaderAsync(ct);
        if (!await reader.ReadAsync(ct))
        {
            return null;
        }

        var savedTopic = NormalizeTopic(GetNullableString(reader, "topic"));
        var saved = new DecisionTopicLabelResponse(
            savedTopic,
            GetNullableString(reader, "title") ?? TopicLabel(savedTopic),
            TopicLabel(savedTopic)
        );
        await reader.CloseAsync();
        await session.CommitAsync();
        return saved;
    }

    public async Task<TopicLabelDeleteResult> DeleteTopicLabelAsync(int projectId, string topic, CancellationToken ct)
    {
        var normalizedTopic = NormalizeTopic(topic);
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;

        using (var usageCommand = connection.CreateCommand())
        {
            usageCommand.CommandText = """
                SELECT EXISTS (
                    SELECT 1
                    FROM decisions
                    WHERE project_id = @project_id
                      AND topic = @topic
                      AND is_deleted = false
                )
                """;
            usageCommand.Parameters.AddWithValue("project_id", projectId);
            usageCommand.Parameters.AddWithValue("topic", normalizedTopic);
            if ((bool)(await usageCommand.ExecuteScalarAsync(ct))!)
            {
                return TopicLabelDeleteResult.NotEmpty;
            }
        }

        using var command = connection.CreateCommand();
        command.CommandText = """
            UPDATE decision_topic_labels
            SET is_deleted = true, updated_at = @now
            WHERE project_id = @project_id
              AND topic = @topic
              AND is_deleted = false
            """;
        command.Parameters.AddWithValue("now", DateTimeOffset.UtcNow);
        command.Parameters.AddWithValue("project_id", projectId);
        command.Parameters.AddWithValue("topic", normalizedTopic);
        var result = await command.ExecuteNonQueryAsync(ct) > 0 ? TopicLabelDeleteResult.Deleted : TopicLabelDeleteResult.NotFound;
        await session.CommitAsync();
        return result;
    }

    public async Task<IReadOnlyList<DecisionReviewDto>?> ListReviewsAsync(string lookup, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var decision = await GetAsync(connection, lookup, ct);
        if (decision is null)
        {
            return null;
        }

        using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT id, outcome_text, reflection_text, quality, reviewed_at, reviewer_id
            FROM decision_reviews
            WHERE decision_id = @decision_id AND is_deleted = false
            ORDER BY reviewed_at DESC, id DESC
            """;
        command.Parameters.AddWithValue("decision_id", decision.Id);
        var reviews = new List<DecisionReviewDto>();
        await using var reader = await command.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            reviews.Add(new DecisionReviewDto(
                reader.GetInt32(reader.GetOrdinal("id")),
                GetNullableString(reader, "outcome_text") ?? "",
                GetNullableString(reader, "reflection_text") ?? "",
                GetNullableString(reader, "quality") ?? "ACCEPTABLE",
                GetDateTimeOffset(reader, "reviewed_at")!.Value,
                GetNullableInt32(reader, "reviewer_id")
            ));
        }
        return reviews;
    }

    public async Task<DecisionDetailDto?> CreateReviewAsync(string lookup, int? actorId, DecisionReviewInput input, CancellationToken ct)
    {
        await using var session = await connections.OpenAsync(ct);
        var connection = session.Connection;
        var transaction = session.Transaction;
        var decision = await GetAsync(connection, lookup, ct);

        if (decision is null ||
            decision.Status is not (DecisionStatus.COMMITTED or DecisionStatus.REVIEWED))
        {
            return null;
        }

        var now = DateTimeOffset.UtcNow;
        using (var command = connection.CreateCommand())
        {
            command.Transaction = transaction;
            command.CommandText = """
                INSERT INTO decision_reviews (
                    created_at,
                    updated_at,
                    is_deleted,
                    decision_id,
                    reviewer_id,
                    outcome_text,
                    reflection_text,
                    quality,
                    reviewed_at
                )
                VALUES (@now, @now, false, @decision_id, @reviewer_id, @outcome_text, @reflection_text, @quality, @now)
                """;
            command.Parameters.AddWithValue("now", now);
            command.Parameters.AddWithValue("decision_id", decision.Id);
            AddNullableInteger(command, "reviewer_id", actorId);
            command.Parameters.AddWithValue("outcome_text", input.OutcomeText);
            command.Parameters.AddWithValue("reflection_text", input.ReflectionText);
            command.Parameters.AddWithValue("quality", string.IsNullOrWhiteSpace(input.DecisionQuality) ? "ACCEPTABLE" : input.DecisionQuality);
            await command.ExecuteNonQueryAsync(ct);
        }

        if (decision.Status == DecisionStatus.COMMITTED)
        {
            using var updateCommand = connection.CreateCommand();
            updateCommand.Transaction = transaction;
            updateCommand.CommandText = """
                UPDATE decisions
                SET status = 'REVIEWED', updated_at = @now, last_edited_by_id = @actor_id
                WHERE id = @decision_id AND is_deleted = false
                """;
            updateCommand.Parameters.AddWithValue("now", now);
            updateCommand.Parameters.AddWithValue("decision_id", decision.Id);
            AddNullableInteger(updateCommand, "actor_id", actorId);
            await updateCommand.ExecuteNonQueryAsync(ct);
            await RecordTransitionAsync(connection, transaction, decision.Id, DecisionStatus.COMMITTED, DecisionStatus.REVIEWED, actorId, now, ct);
        }

        var reviewed = await GetAsync(connection, decision.Id.ToString(), ct);
        await session.CommitAsync();
        return reviewed;
    }

    private async Task<DecisionDetailDto?> GetAsync(NpgsqlConnection connection, string lookup, CancellationToken ct)
    {
        var lookupIsId = int.TryParse(lookup, out var numericId);
        using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT
                d.id,
                d.slug,
                d.project_seq,
                d.status,
                d.title,
                d.context_summary,
                d.reasoning,
                d.risk_level,
                d.confidence,
                d.topic,
                d.created_at,
                d.author_id,
                d.updated_at,
                d.last_edited_by_id,
                d.committed_at,
                d.project_id,
                p.name AS project_name,
                d.is_reference_case,
                d.created_by_agent,
                d.agent_session_id,
                d.planned_decision_date
            FROM decisions d
            LEFT JOIN core_project p ON p.id = d.project_id
            WHERE d.is_deleted = false
              AND ((@lookup_is_id = true AND d.id = @id) OR d.slug = @slug)
            """;

        command.Parameters.AddWithValue("lookup_is_id", lookupIsId);
        command.Parameters.AddWithValue("id", numericId);
        command.Parameters.AddWithValue("slug", lookup);

        await using var reader = await command.ExecuteReaderAsync(ct);
        if (!await reader.ReadAsync(ct))
        {
            return null;
        }

        var topic = NormalizeTopic(GetNullableString(reader, "topic"));
        var detail = new DecisionDetailDto(
            reader.GetInt32(reader.GetOrdinal("id")),
            GetNullableString(reader, "slug") ?? Slug(reader.GetInt32(reader.GetOrdinal("id"))),
            reader.GetInt32(reader.GetOrdinal("project_seq")),
            ParseStatus(reader.GetString(reader.GetOrdinal("status"))),
            GetNullableString(reader, "title"),
            GetNullableString(reader, "context_summary"),
            GetNullableString(reader, "reasoning"),
            ParseRisk(GetNullableString(reader, "risk_level")),
            GetNullableInt32(reader, "confidence"),
            topic,
            TopicLabel(topic),
            GetDateTimeOffset(reader, "created_at")!.Value,
            GetNullableInt32(reader, "author_id"),
            GetDateTimeOffset(reader, "updated_at")!.Value,
            GetNullableInt32(reader, "last_edited_by_id"),
            GetDateTimeOffset(reader, "committed_at"),
            GetNullableInt32(reader, "project_id"),
            GetNullableString(reader, "project_name"),
            reader.GetBoolean(reader.GetOrdinal("is_reference_case")),
            reader.GetBoolean(reader.GetOrdinal("created_by_agent")),
            GetNullableGuid(reader, "agent_session_id"),
            GetDateTimeOffset(reader, "planned_decision_date"),
            Array.Empty<DecisionSignalDto>(),
            Array.Empty<DecisionOptionDto>(),
            null
        );
        await reader.CloseAsync();

        return detail with
        {
            Signals = await ListSignalsAsync(connection, detail.Id, ct),
            Options = await ListOptionsAsync(connection, detail.Id, ct),
            OriginMeeting = await GetOriginMeetingAsync(connection, detail.Id, ct),
        };
    }

    private async Task<DecisionOriginMeetingDto> ResolveOriginMeetingAsync(NpgsqlConnection connection, int meetingId, int projectId, CancellationToken ct)
    {
        var origin = await GetMeetingAsync(connection, meetingId, ct);
        if (origin is null)
        {
            throw new DecisionValidationException("origin_meeting_id", "Meeting does not exist.");
        }
        if (origin.ProjectId != projectId)
        {
            throw new DecisionValidationException("origin_meeting_id", "Meeting must belong to the same project as the decision.");
        }
        if (await IsMeetingArchivedAsync(connection, meetingId, ct))
        {
            throw new DecisionValidationException("origin_meeting_id", "Archived meetings cannot be used as a decision origin.");
        }
        return origin;
    }

    private async Task<DecisionOriginMeetingDto?> GetOriginMeetingAsync(NpgsqlConnection connection, int decisionId, CancellationToken ct)
    {
        using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT meeting_id
            FROM meetings_meetingdecisionorigin
            WHERE decision_id = @decision_id
            LIMIT 1
            """;
        command.Parameters.AddWithValue("decision_id", decisionId);
        var meetingId = await command.ExecuteScalarAsync(ct);
        return meetingId is null ? null : await GetMeetingAsync(connection, Convert.ToInt32(meetingId), ct);
    }

    private async Task<DecisionOriginMeetingDto?> GetMeetingAsync(NpgsqlConnection connection, int meetingId, CancellationToken ct)
    {
        using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT
                m.id,
                m.title,
                m.slug,
                m.project_id,
                t.slug AS type_slug,
                m.scheduled_date
            FROM meetings_meeting m
            LEFT JOIN meetings_meetingtypedefinition t ON t.id = m.type_definition_id
            WHERE m.id = @meeting_id
              AND m.is_deleted = false
            """;
        command.Parameters.AddWithValue("meeting_id", meetingId);

        await using var reader = await command.ExecuteReaderAsync(ct);
        if (!await reader.ReadAsync(ct))
        {
            return null;
        }

        var projectId = GetNullableInt32(reader, "project_id");
        var slug = GetNullableString(reader, "slug");
        var url = projectId.HasValue && !string.IsNullOrWhiteSpace(slug)
            ? $"/projects/{projectId.Value}/meetings/{slug}"
            : null;
        return new DecisionOriginMeetingDto(
            reader.GetInt32(reader.GetOrdinal("id")),
            GetNullableString(reader, "title") ?? "",
            url,
            url,
            projectId,
            GetNullableString(reader, "type_slug"),
            GetNullableDateOnly(reader, "scheduled_date")
        );
    }

    private async Task<bool> IsMeetingArchivedAsync(NpgsqlConnection connection, int meetingId, CancellationToken ct)
    {
        using var command = connection.CreateCommand();
        command.CommandText = "SELECT is_archived FROM meetings_meeting WHERE id = @meeting_id";
        command.Parameters.AddWithValue("meeting_id", meetingId);
        return await command.ExecuteScalarAsync(ct) is true;
    }

    private static async Task CreateMeetingDecisionOriginAsync(
        NpgsqlConnection connection,
        NpgsqlTransaction transaction,
        int meetingId,
        int decisionId,
        int? actorId,
        DateTimeOffset now,
        CancellationToken ct
    )
    {
        if (actorId is null)
        {
            throw new DecisionValidationException("origin_meeting_id", "Authenticated user is required to create a meeting origin.");
        }

        using var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = """
            INSERT INTO meetings_meetingdecisionorigin (
                meeting_id,
                decision_id,
                origin_timestamp,
                creation_context,
                created_by_id,
                created_at,
                updated_at
            )
            VALUES (
                @meeting_id,
                @decision_id,
                @now,
                @creation_context,
                @actor_id,
                @now,
                @now
            )
            """;
        command.Parameters.AddWithValue("meeting_id", meetingId);
        command.Parameters.AddWithValue("decision_id", decisionId);
        command.Parameters.AddWithValue("now", now);
        command.Parameters.AddWithValue("actor_id", actorId.Value);
        command.Parameters.Add("creation_context", NpgsqlDbType.Jsonb).Value = JsonSerializer.Serialize(new
        {
            source = "decision_service",
            meeting_id = meetingId,
            decision_id = decisionId,
        });
        await command.ExecuteNonQueryAsync(ct);
    }

    private async Task<DecisionConnectionsResponse> BuildConnectionsPayloadAsync(NpgsqlConnection connection, int decisionId, int? projectId, int projectSeq, CancellationToken ct)
    {
        using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT
                e.from_decision_id,
                e.to_decision_id,
                fd.project_seq AS from_seq,
                td.project_seq AS to_seq,
                other_d.id AS other_id,
                other_d.slug AS other_slug,
                other_d.project_seq AS other_seq,
                other_d.title AS other_title
            FROM decision_edges e
            JOIN decisions fd ON fd.id = e.from_decision_id
            JOIN decisions td ON td.id = e.to_decision_id
            JOIN decisions other_d ON other_d.id = CASE
                WHEN e.from_decision_id = @decision_id THEN e.to_decision_id
                ELSE e.from_decision_id
            END
            WHERE (e.from_decision_id = @decision_id OR e.to_decision_id = @decision_id)
              AND e.is_deleted = false
              AND fd.is_deleted = false
              AND td.is_deleted = false
              AND (@project_id IS NULL OR (fd.project_id = @project_id AND td.project_id = @project_id))
            ORDER BY other_d.project_seq, other_d.id
            """;
        command.Parameters.AddWithValue("decision_id", decisionId);
        AddNullableInteger(command, "project_id", projectId);

        var connected = new Dictionary<int, DecisionConnectionNodeDto>();
        var edges = new List<DecisionConnectionEdgeDto>();
        await using var reader = await command.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            var otherId = reader.GetInt32(reader.GetOrdinal("other_id"));
            connected[otherId] = new DecisionConnectionNodeDto(
                otherId,
                GetNullableString(reader, "other_slug") ?? Slug(otherId),
                reader.GetInt32(reader.GetOrdinal("other_seq")),
                GetNullableString(reader, "other_title")
            );
            edges.Add(new DecisionConnectionEdgeDto(
                reader.GetInt32(reader.GetOrdinal("from_decision_id")),
                reader.GetInt32(reader.GetOrdinal("to_decision_id")),
                reader.GetInt32(reader.GetOrdinal("from_seq")),
                reader.GetInt32(reader.GetOrdinal("to_seq"))
            ));
        }

        return new DecisionConnectionsResponse(
            new DecisionConnectionSelfDto(decisionId, projectSeq),
            connected.Values.OrderBy(node => node.ProjectSeq).ThenBy(node => node.Id).ToList(),
            edges
        );
    }

    private async Task<IReadOnlyList<int>?> ResolveConnectionTargetIdsAsync(
        NpgsqlConnection connection,
        DecisionDetailDto decision,
        DecisionConnectionsUpdateRequest request,
        CancellationToken ct
    )
    {
        if (request.ConnectedDecisionIds is { Count: > 0 } && request.ConnectedDecisionSeqs is { Count: > 0 })
        {
            return null;
        }
        if (request.ConnectedDecisionIds is { } ids)
        {
            var requestedIds = ids.Where(id => id != decision.Id).Distinct().ToList();
            if (requestedIds.Count == 0)
            {
                return Array.Empty<int>();
            }
            if (decision.ProjectId is null)
            {
                return null;
            }

            using var byIdCommand = connection.CreateCommand();
            byIdCommand.CommandText = """
                SELECT target.id
                FROM decisions target
                JOIN core_project target_project ON target_project.id = target.project_id
                JOIN core_project current_project ON current_project.id = @project_id
                WHERE target.id = ANY(@ids)
                  AND target.is_deleted = false
                  AND target_project.is_deleted = false
                  AND current_project.is_deleted = false
                  AND target_project.organization_id = current_project.organization_id
                """;
            byIdCommand.Parameters.AddWithValue("project_id", decision.ProjectId.Value);
            byIdCommand.Parameters.AddWithValue("ids", requestedIds.ToArray());

            var byIdResult = new List<int>();
            await using var byIdReader = await byIdCommand.ExecuteReaderAsync(ct);
            while (await byIdReader.ReadAsync(ct))
            {
                byIdResult.Add(byIdReader.GetInt32(0));
            }
            return byIdResult.Count == requestedIds.Count ? byIdResult : null;
        }
        if (decision.ProjectId is null)
        {
            return Array.Empty<int>();
        }

        var seqs = (request.ConnectedDecisionSeqs ?? Array.Empty<int>())
            .Where(seq => seq != decision.ProjectSeq)
            .Distinct()
            .ToList();
        if (seqs.Count == 0)
        {
            return Array.Empty<int>();
        }

        using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT id
            FROM decisions
            WHERE project_id = @project_id
              AND project_seq = ANY(@seqs)
              AND is_deleted = false
            """;
        command.Parameters.AddWithValue("project_id", decision.ProjectId.Value);
        command.Parameters.AddWithValue("seqs", seqs.ToArray());
        var result = new List<int>();
        await using var reader = await command.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            result.Add(reader.GetInt32(0));
        }
        return result.Count == seqs.Count ? result : null;
    }

    private async Task<IReadOnlyList<int>> ResolveGraphProjectIdsAsync(NpgsqlConnection connection, int projectId, bool allProjects, int userId, bool isSuperuser, CancellationToken ct)
    {
        if (!allProjects)
        {
            return new[] { projectId };
        }

        using var command = connection.CreateCommand();
        command.CommandText = isSuperuser
            ? """
                SELECT related.id
                FROM core_project current
                JOIN core_project related ON related.organization_id = current.organization_id
                WHERE current.id = @project_id
                  AND current.is_deleted = false
                  AND related.is_deleted = false
                ORDER BY related.id
                """
            : """
                SELECT related.id
                FROM core_project current
                JOIN core_project related ON related.organization_id = current.organization_id
                JOIN core_projectmember member ON member.project_id = related.id
                WHERE current.id = @project_id
                  AND current.is_deleted = false
                  AND related.is_deleted = false
                  AND member.user_id = @user_id
                  AND member.is_active = true
                  AND member.is_deleted = false
                ORDER BY related.id
                """;
        command.Parameters.AddWithValue("project_id", projectId);
        command.Parameters.AddWithValue("user_id", userId);

        var result = new List<int>();
        await using var reader = await command.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            result.Add(reader.GetInt32(0));
        }
        return result;
    }

    private async Task<Dictionary<(int ProjectId, string Topic), string>> LoadTopicLabelsAsync(NpgsqlConnection connection, IReadOnlyList<int> projectIds, CancellationToken ct)
    {
        using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT project_id, topic, title
            FROM decision_topic_labels
            WHERE project_id = ANY(@project_ids)
              AND is_deleted = false
            """;
        command.Parameters.AddWithValue("project_ids", projectIds.ToArray());

        var labels = new Dictionary<(int ProjectId, string Topic), string>();
        await using var reader = await command.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            labels[(reader.GetInt32(reader.GetOrdinal("project_id")), NormalizeTopic(GetNullableString(reader, "topic")))] =
                reader.GetString(reader.GetOrdinal("title"));
        }
        return labels;
    }

    private static string TopicLabelForAnyProject(Dictionary<(int ProjectId, string Topic), string> labels, string topic)
    {
        return labels.FirstOrDefault(item => item.Key.Topic == topic).Value ?? TopicLabel(topic);
    }

    private async Task<int> NextProjectSequenceAsync(NpgsqlConnection connection, int projectId, CancellationToken ct)
    {
        using var command = connection.CreateCommand();
        command.CommandText = "SELECT COALESCE(MAX(project_seq), 0) + 1 FROM decisions WHERE project_id = @project_id";
        command.Parameters.AddWithValue("project_id", projectId);
        return Convert.ToInt32(await command.ExecuteScalarAsync(ct));
    }

    private async Task<IReadOnlyList<DecisionSignalDto>> ListSignalsAsync(NpgsqlConnection connection, int decisionId, CancellationToken ct, int? signalId = null, NpgsqlTransaction? transaction = null)
    {
        using var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = """
            SELECT
                id,
                decision_id,
                author_id,
                created_at,
                updated_at,
                metric,
                movement,
                period,
                comparison,
                scope_type,
                scope_value,
                delta_value,
                delta_unit,
                display_text,
                display_text_override
            FROM decision_signals
            WHERE decision_id = @decision_id AND is_deleted = false
            AND (@signal_id IS NULL OR id = @signal_id)
            ORDER BY created_at DESC, id DESC
            """;
        if (transaction is not null)
        {
            command.CommandText += " FOR UPDATE";
        }
        command.Parameters.AddWithValue("decision_id", decisionId);
        AddNullableInteger(command, "signal_id", signalId);

        var items = new List<DecisionSignalDto>();
        await using var reader = await command.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            items.Add(new DecisionSignalDto(
                reader.GetInt32(reader.GetOrdinal("id")),
                reader.GetInt32(reader.GetOrdinal("decision_id")),
                GetNullableInt32(reader, "author_id"),
                GetDateTimeOffset(reader, "created_at")!.Value,
                GetDateTimeOffset(reader, "updated_at")!.Value,
                GetNullableString(reader, "metric"),
                GetNullableString(reader, "movement"),
                GetNullableString(reader, "period"),
                GetNullableString(reader, "comparison") ?? "NONE",
                GetNullableString(reader, "scope_type"),
                GetNullableString(reader, "scope_value"),
                GetNullableDecimal(reader, "delta_value"),
                GetNullableString(reader, "delta_unit"),
                GetNullableString(reader, "display_text") ?? "",
                GetNullableString(reader, "display_text_override")
            ));
        }
        return items;
    }

    private async Task<IReadOnlyList<DecisionOptionDto>> ListOptionsAsync(NpgsqlConnection connection, int decisionId, CancellationToken ct)
    {
        using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT id, decision_id, text, is_selected, "order", created_at, updated_at
            FROM decision_options
            WHERE decision_id = @decision_id AND is_deleted = false
            ORDER BY "order", created_at, id
            """;
        command.Parameters.AddWithValue("decision_id", decisionId);

        var items = new List<DecisionOptionDto>();
        await using var reader = await command.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            items.Add(new DecisionOptionDto(
                reader.GetInt32(reader.GetOrdinal("id")),
                reader.GetInt32(reader.GetOrdinal("decision_id")),
                reader.GetString(reader.GetOrdinal("text")),
                reader.GetBoolean(reader.GetOrdinal("is_selected")),
                reader.GetInt32(reader.GetOrdinal("order")),
                GetDateTimeOffset(reader, "created_at")!.Value,
                GetDateTimeOffset(reader, "updated_at")!.Value
            ));
        }
        return items;
    }

    private async Task ReplaceOptionsAsync(
        NpgsqlConnection connection,
        NpgsqlTransaction transaction,
        int decisionId,
        DateTimeOffset now,
        IReadOnlyList<DecisionOptionInput>? options,
        CancellationToken ct
    )
    {
        using (var deleteCommand = connection.CreateCommand())
        {
            deleteCommand.Transaction = transaction;
            deleteCommand.CommandText = "DELETE FROM decision_options WHERE decision_id = @decision_id";
            deleteCommand.Parameters.AddWithValue("decision_id", decisionId);
            await deleteCommand.ExecuteNonQueryAsync(ct);
        }

        foreach (var option in options ?? Array.Empty<DecisionOptionInput>())
        {
            using var command = connection.CreateCommand();
            command.Transaction = transaction;
            command.CommandText = """
                INSERT INTO decision_options (
                    created_at,
                    updated_at,
                    is_deleted,
                    decision_id,
                    text,
                    is_selected,
                    "order"
                )
                VALUES (@now, @now, false, @decision_id, @text, @is_selected, @order)
                """;
            command.Parameters.AddWithValue("now", now);
            command.Parameters.AddWithValue("decision_id", decisionId);
            command.Parameters.AddWithValue("text", option.Text);
            command.Parameters.AddWithValue("is_selected", option.IsSelected);
            command.Parameters.AddWithValue("order", option.Order);
            await command.ExecuteNonQueryAsync(ct);
        }
    }

    private async Task ReplaceSignalsAsync(
        NpgsqlConnection connection,
        NpgsqlTransaction transaction,
        int decisionId,
        int? actorId,
        DateTimeOffset now,
        IReadOnlyList<DecisionSignalInput>? signals,
        CancellationToken ct
    )
    {
        await EnsureSignalOptionsEnabledAsync(connection, transaction, decisionId, signals ?? [], ct);
        using (var deleteCommand = connection.CreateCommand())
        {
            deleteCommand.Transaction = transaction;
            deleteCommand.CommandText = "DELETE FROM decision_signals WHERE decision_id = @decision_id";
            deleteCommand.Parameters.AddWithValue("decision_id", decisionId);
            await deleteCommand.ExecuteNonQueryAsync(ct);
        }

        foreach (var signal in signals ?? Array.Empty<DecisionSignalInput>())
        {
            var displayText = signal.DisplayTextOverride
                ?? string.Join(" ", new[] { signal.Metric, signal.Movement, signal.Period }.Where(value => !string.IsNullOrWhiteSpace(value)));
            using var command = connection.CreateCommand();
            command.Transaction = transaction;
            command.CommandText = """
                INSERT INTO decision_signals (
                    created_at,
                    updated_at,
                    is_deleted,
                    decision_id,
                    author_id,
                    metric,
                    movement,
                    period,
                    comparison,
                    scope_type,
                    scope_value,
                    delta_value,
                    delta_unit,
                    display_text,
                    display_text_override
                )
                VALUES (
                    @now,
                    @now,
                    false,
                    @decision_id,
                    @actor_id,
                    @metric,
                    @movement,
                    @period,
                    @comparison,
                    @scope_type,
                    @scope_value,
                    @delta_value,
                    @delta_unit,
                    @display_text,
                    @display_text_override
                )
                """;
            command.Parameters.AddWithValue("now", now);
            command.Parameters.AddWithValue("decision_id", decisionId);
            AddNullableInteger(command, "actor_id", actorId);
            AddNullableText(command, "metric", signal.Metric);
            AddNullableText(command, "movement", signal.Movement);
            AddNullableText(command, "period", signal.Period);
            command.Parameters.AddWithValue("comparison", signal.Comparison ?? "NONE");
            AddNullableText(command, "scope_type", signal.ScopeType);
            AddNullableText(command, "scope_value", signal.ScopeValue);
            AddNullableDecimal(command, "delta_value", signal.DeltaValue);
            AddNullableText(command, "delta_unit", signal.DeltaUnit);
            command.Parameters.AddWithValue("display_text", displayText);
            AddNullableText(command, "display_text_override", signal.DisplayTextOverride);
            await command.ExecuteNonQueryAsync(ct);
        }
    }

    private async Task ReplaceParentEdgesAsync(
        NpgsqlConnection connection,
        NpgsqlTransaction transaction,
        int decisionId,
        int projectId,
        int? actorId,
        IReadOnlyList<int>? parentDecisionIds,
        CancellationToken ct
    )
    {
        if (parentDecisionIds is null)
        {
            return;
        }

        using (var deleteCommand = connection.CreateCommand())
        {
            deleteCommand.Transaction = transaction;
            deleteCommand.CommandText = "DELETE FROM decision_edges WHERE to_decision_id = @decision_id AND edge_type = 'FOLLOW_UP'";
            deleteCommand.Parameters.AddWithValue("decision_id", decisionId);
            await deleteCommand.ExecuteNonQueryAsync(ct);
        }

        foreach (var parentId in parentDecisionIds.Distinct().Where(parentId => parentId != decisionId))
        {
            using var command = connection.CreateCommand();
            command.Transaction = transaction;
            command.CommandText = """
                INSERT INTO decision_edges (
                    created_at,
                    updated_at,
                    is_deleted,
                    from_decision_id,
                    to_decision_id,
                    edge_type,
                    created_by_id
                )
                SELECT @now, @now, false, @from_decision_id, @to_decision_id, 'FOLLOW_UP', @created_by_id
                WHERE EXISTS (
                    SELECT 1 FROM decisions
                    WHERE id = @from_decision_id AND project_id = @project_id AND is_deleted = false
                )
                ON CONFLICT DO NOTHING
                """;
            command.Parameters.AddWithValue("now", DateTimeOffset.UtcNow);
            command.Parameters.AddWithValue("from_decision_id", parentId);
            command.Parameters.AddWithValue("to_decision_id", decisionId);
            command.Parameters.AddWithValue("project_id", projectId);
            AddNullableInteger(command, "created_by_id", actorId);
            await command.ExecuteNonQueryAsync(ct);
        }
    }

    private async Task RecordTransitionAsync(
        NpgsqlConnection connection,
        NpgsqlTransaction transaction,
        int decisionId,
        DecisionStatus fromStatus,
        DecisionStatus toStatus,
        int? actorId,
        DateTimeOffset now,
        CancellationToken ct
    )
    {
        using var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = """
            INSERT INTO decision_state_transitions (
                created_at,
                updated_at,
                is_deleted,
                decision_id,
                from_status,
                to_status,
                transition_method,
                metadata,
                triggered_by_id,
                timestamp,
                note
            )
            VALUES (
                @now,
                @now,
                false,
                @decision_id,
                @from_status,
                @to_status,
                @transition_method,
                '{}'::jsonb,
                @triggered_by_id,
                @now,
                NULL
            )
            """;
        command.Parameters.AddWithValue("now", now);
        command.Parameters.AddWithValue("decision_id", decisionId);
        command.Parameters.AddWithValue("from_status", fromStatus.ToString());
        command.Parameters.AddWithValue("to_status", toStatus.ToString());
        command.Parameters.AddWithValue("transition_method", toStatus.ToString().ToLowerInvariant());
        AddNullableInteger(command, "triggered_by_id", actorId);
        await command.ExecuteNonQueryAsync(ct);
    }

    private static readonly (string Field, string Column, Func<DecisionSignalInput, string?> Value)[] SignalOptionFields =
    [
        ("signal_metric", "metric", signal => signal.Metric),
        ("signal_movement", "movement", signal => signal.Movement),
        ("signal_period", "period", signal => signal.Period),
    ];

    private static async Task EnsureSignalOptionsEnabledAsync(
        NpgsqlConnection connection,
        NpgsqlTransaction? transaction,
        int decisionId,
        IEnumerable<DecisionSignalInput> signals,
        CancellationToken ct
    )
    {
        var requested = signals
            .SelectMany(signal => SignalOptionFields.Select(option => (option.Field, Value: option.Value(signal))))
            .Where(item => !string.IsNullOrWhiteSpace(item.Value))
            .Distinct()
            .ToList();
        if (requested.Count == 0)
        {
            return;
        }

        using var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = """
            SELECT o.field
            FROM config."option" o
            JOIN decisions d ON d.id = @decision_id AND d.project_id = o.project_id
            JOIN core_project p ON p.id = d.project_id AND p.organization_id = o.organization_id
            WHERE o.module = 'decisions'
              AND o.enabled = false
              AND (o.field, o.value) IN (SELECT * FROM unnest(@fields::text[], @values::text[]))
              AND NOT EXISTS (
                  SELECT 1
                  FROM decision_signals s
                  WHERE s.decision_id = d.id
                    AND s.is_deleted = false
                    AND CASE o.field
                        WHEN 'signal_metric' THEN s.metric
                        WHEN 'signal_movement' THEN s.movement
                        ELSE s.period
                    END = o.value
              )
            LIMIT 1
            """;
        command.Parameters.AddWithValue("decision_id", decisionId);
        command.Parameters.AddWithValue("fields", requested.Select(item => item.Field).ToArray());
        command.Parameters.AddWithValue("values", requested.Select(item => item.Value!).ToArray());
        if (await command.ExecuteScalarAsync(ct) is string field)
        {
            throw new DecisionValidationException(
                SignalOptionFields.First(option => option.Field == field).Column,
                "This option is disabled for this project.");
        }
    }

    private static void AddNullableText(NpgsqlCommand command, string name, string? value)
    {
        command.Parameters.AddWithValue(name, NpgsqlDbType.Text, value is null ? DBNull.Value : value);
    }

    private static void AddNullableInteger(NpgsqlCommand command, string name, int? value)
    {
        command.Parameters.AddWithValue(name, NpgsqlDbType.Integer, value.HasValue ? value.Value : DBNull.Value);
    }

    private static void AddNullableBoolean(NpgsqlCommand command, string name, bool? value)
    {
        command.Parameters.AddWithValue(name, NpgsqlDbType.Boolean, value.HasValue ? value.Value : DBNull.Value);
    }

    private static void AddNullableDecimal(NpgsqlCommand command, string name, decimal? value)
    {
        command.Parameters.AddWithValue(name, NpgsqlDbType.Numeric, value.HasValue ? value.Value : DBNull.Value);
    }

    private static void AddNullableTimestamp(NpgsqlCommand command, string name, DateTimeOffset? value)
    {
        command.Parameters.AddWithValue(name, NpgsqlDbType.TimestampTz, value.HasValue ? value.Value : DBNull.Value);
    }

    private static string? GetNullableString(NpgsqlDataReader reader, string column)
    {
        var ordinal = reader.GetOrdinal(column);
        return reader.IsDBNull(ordinal) ? null : reader.GetString(ordinal);
    }

    private static int? GetNullableInt32(NpgsqlDataReader reader, string column)
    {
        var ordinal = reader.GetOrdinal(column);
        return reader.IsDBNull(ordinal) ? null : reader.GetInt32(ordinal);
    }

    private static decimal? GetNullableDecimal(NpgsqlDataReader reader, string column)
    {
        var ordinal = reader.GetOrdinal(column);
        return reader.IsDBNull(ordinal) ? null : reader.GetDecimal(ordinal);
    }

    private static Guid? GetNullableGuid(NpgsqlDataReader reader, string column)
    {
        var ordinal = reader.GetOrdinal(column);
        return reader.IsDBNull(ordinal) ? null : reader.GetGuid(ordinal);
    }

    private static DateOnly? GetNullableDateOnly(NpgsqlDataReader reader, string column)
    {
        var ordinal = reader.GetOrdinal(column);
        if (reader.IsDBNull(ordinal))
        {
            return null;
        }

        var value = reader.GetValue(ordinal);
        return value switch
        {
            DateOnly dateOnly => dateOnly,
            DateTime dateTime => DateOnly.FromDateTime(dateTime),
            _ => null,
        };
    }

    private static DateTimeOffset? GetDateTimeOffset(NpgsqlDataReader reader, string column)
    {
        var ordinal = reader.GetOrdinal(column);
        return reader.IsDBNull(ordinal) ? null : reader.GetFieldValue<DateTimeOffset>(ordinal);
    }

    private static DecisionStatus ParseStatus(string value)
    {
        return Enum.TryParse<DecisionStatus>(value, ignoreCase: true, out var status)
            ? status
            : DecisionStatus.DRAFT;
    }

    private static DecisionRiskLevel? ParseRisk(string? value)
    {
        return Enum.TryParse<DecisionRiskLevel>(value, ignoreCase: true, out var risk) ? risk : null;
    }

    private static string NormalizeTopic(string? topic)
    {
        return string.IsNullOrWhiteSpace(topic) ? "other" : topic.Trim().ToLowerInvariant();
    }

    private static string TopicLabel(string topic)
    {
        return topic == "other" ? "Other" : string.Join(" ", topic.Split('_', '-').Select(ToTitleCase));
    }

    private static string? ProjectTheme(string? projectName, string? objectivesJson, string? projectTypesJson)
    {
        var objectiveLabels = LabelList(objectivesJson, ObjectiveLabels);
        if (objectiveLabels.Count > 0)
        {
            return string.Join(" + ", objectiveLabels.Take(2));
        }

        var typeLabels = LabelList(projectTypesJson, ProjectTypeLabels);
        return typeLabels.Count > 0 ? string.Join(" + ", typeLabels.Take(2)) : projectName;
    }

    private static string? ProjectSubtitle(string? description, string? projectTypesJson)
    {
        var typeLabels = LabelList(projectTypesJson, ProjectTypeLabels);
        if (typeLabels.Count > 0)
        {
            return string.Join(" + ", typeLabels.Take(2));
        }

        var trimmed = description?.Trim();
        if (string.IsNullOrWhiteSpace(trimmed))
        {
            return null;
        }
        return trimmed.Length > 80 ? trimmed[..80] : trimmed;
    }

    private static IReadOnlyList<string> LabelList(string? json, IReadOnlyDictionary<string, string> labels)
    {
        if (string.IsNullOrWhiteSpace(json))
        {
            return Array.Empty<string>();
        }

        try
        {
            using var document = JsonDocument.Parse(json);
            if (document.RootElement.ValueKind != JsonValueKind.Array)
            {
                return Array.Empty<string>();
            }

            return document.RootElement
                .EnumerateArray()
                .Where(item => item.ValueKind == JsonValueKind.String)
                .Select(item => item.GetString())
                .Where(value => !string.IsNullOrWhiteSpace(value))
                .Select(value => labels.TryGetValue(value!, out var label) ? label : TopicLabel(value!))
                .ToList();
        }
        catch (JsonException)
        {
            return Array.Empty<string>();
        }
    }

    private static readonly IReadOnlyDictionary<string, string> ObjectiveLabels = new Dictionary<string, string>
    {
        ["awareness"] = "Awareness",
        ["consideration"] = "Consideration",
        ["conversions"] = "Conversions",
        ["sales"] = "Sales",
        ["leads"] = "Lead generation",
        ["traffic"] = "Traffic",
        ["engagement"] = "Engagement",
        ["retention"] = "Retention",
        ["tiktok_growth"] = "TikTok Growth",
        ["meta_retargeting"] = "Meta Retargeting",
        ["google_search"] = "Google Search",
        ["email_lifecycle"] = "Email Lifecycle",
        ["landing_page_cro"] = "Landing Page CRO",
        ["influencer_ugc"] = "Influencer / UGC",
    };

    private static readonly IReadOnlyDictionary<string, string> ProjectTypeLabels = new Dictionary<string, string>
    {
        ["paid_social"] = "Paid social",
        ["paid_search"] = "Paid search",
        ["programmatic"] = "Programmatic",
        ["influencer_ugc"] = "Influencer / UGC",
        ["cross_channel"] = "Cross-channel",
        ["performance"] = "Performance",
        ["brand_campaigns"] = "Brand campaigns",
        ["app_acquisition"] = "App acquisition",
    };

    private static string ToTitleCase(string value)
    {
        return string.IsNullOrWhiteSpace(value)
            ? value
            : char.ToUpperInvariant(value[0]) + value[1..];
    }

    private static string Slug(int id)
    {
        return $"decision-{id}";
    }
    private static async Task CreateCommitRecordAsync(
        NpgsqlConnection connection,
        NpgsqlTransaction transaction,
        int decisionId,
        int? actorId,
        DateTimeOffset now,
        object snapshot,
        CancellationToken ct)
    {
        using var command = connection.CreateCommand();
        command.Transaction = transaction;

        command.CommandText = """
            INSERT INTO decision_commit_records (
                created_at,
                updated_at,
                is_deleted,
                committed_at,
                decision_id,
                committed_by_id,
                validation_snapshot
            )
            VALUES (
                @now,
                @now,
                false,
                @now,
                @decision_id,
                @actor_id,
                @validation_snapshot
            )
            """;

        command.Parameters.AddWithValue("now", now);
        command.Parameters.AddWithValue("decision_id", decisionId);
        AddNullableInteger(command, "actor_id", actorId);

        command.Parameters.Add("validation_snapshot", NpgsqlDbType.Jsonb).Value =
            JsonSerializer.Serialize(snapshot);

        await command.ExecuteNonQueryAsync(ct);
    }
}
