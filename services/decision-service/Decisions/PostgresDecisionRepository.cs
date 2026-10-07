using System.Text.Json;
using Npgsql;
using NpgsqlTypes;

namespace DecisionService.Decisions;

public sealed class PostgresDecisionRepository(NpgsqlDataSource dataSource) : IDecisionRepository
{
    public DecisionListResponse List(int? projectId, DecisionStatus? status, int pageSize, int pageToken)
    {
        using var connection = dataSource.OpenConnection();
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
        using var reader = command.ExecuteReader();
        while (reader.Read())
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

    public DecisionDetailDto? Get(string lookup)
    {
        using var connection = dataSource.OpenConnection();
        return Get(connection, lookup);
    }

    public DecisionDetailDto Create(int? projectId, int? actorId, CreateDecisionDraftRequest request)
    {
        if (projectId is null)
        {
            throw new InvalidOperationException("Project context is required.");
        }

        using var connection = dataSource.OpenConnection();
        var originMeeting = request.OriginMeetingId.HasValue
            ? ResolveOriginMeeting(connection, request.OriginMeetingId.Value, projectId.Value)
            : null;
        using var transaction = connection.BeginTransaction();
        var now = DateTimeOffset.UtcNow;
        var projectSeq = NextProjectSequence(connection, projectId.Value);
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
                @created_by_agent,
                @agent_session_id,
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
        command.Parameters.AddWithValue("created_by_agent", request.CreatedByAgent);
        AddNullableUuid(command, "agent_session_id", request.AgentSessionId);
        command.Parameters.AddWithValue("project_id", projectId.Value);
        command.Parameters.AddWithValue("project_seq", projectSeq);
        AddNullableInteger(command, "actor_id", actorId);
        command.Parameters.AddWithValue("topic", topic);
        command.Parameters.AddWithValue("slug", $"decision-{Guid.NewGuid():N}");

        var id = Convert.ToInt32(command.ExecuteScalar());
        ReplaceOptions(connection, transaction, id, now, request.Options);
        ReplaceSignals(connection, transaction, id, actorId, now, request.Signals);
        ReplaceParentEdges(connection, transaction, id, projectId.Value, actorId, request.ParentDecisionIds);
        if (originMeeting is not null)
        {
            CreateMeetingDecisionOrigin(connection, transaction, originMeeting.Id, id, actorId, now);
        }
        transaction.Commit();

        return Get(connection, id.ToString())!;
    }

    public DecisionDetailDto? UpdateDraft(string lookup, int? actorId, UpdateDecisionDraftRequest request)
    {
        using var connection = dataSource.OpenConnection();
        using var transaction = connection.BeginTransaction();
        var existing = Get(connection, lookup);
        if (existing is null || existing.Status is not (DecisionStatus.PREDRAFT or DecisionStatus.DRAFT or DecisionStatus.AWAITING_APPROVAL or DecisionStatus.COMMITTED or DecisionStatus.REVIEWED))
        {
            return null;
        }

        var now = DateTimeOffset.UtcNow;
        using var command = connection.CreateCommand();
        command.Transaction = transaction;
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
            WHERE id = @id AND is_deleted = false
            """;
        command.Parameters.AddWithValue("id", existing.Id);
        command.Parameters.AddWithValue("now", now);
        AddNullableText(command, "title", request.Title);
        AddNullableText(command, "context_summary", request.ContextSummary);
        AddNullableText(command, "reasoning", request.Reasoning);
        AddNullableText(command, "risk_level", request.RiskLevel?.ToString());
        AddNullableInteger(command, "confidence", request.ConfidenceScore);
        AddNullableText(command, "topic", request.Topic is null ? null : NormalizeTopic(request.Topic));
        AddNullableBoolean(command, "is_reference_case", request.IsReferenceCase);
        AddNullableTimestamp(command, "planned_decision_date", request.PlannedDecisionDate);
        AddNullableInteger(command, "actor_id", actorId);
        command.ExecuteNonQuery();

        if (request.Options is not null)
        {
            ReplaceOptions(connection, transaction, existing.Id, now, request.Options);
        }
        if (request.Signals is not null)
        {
            ReplaceSignals(connection, transaction, existing.Id, actorId, now, request.Signals);
        }
        if (request.ParentDecisionIds is not null && existing.ProjectId is not null)
        {
            ReplaceParentEdges(connection, transaction, existing.Id, existing.ProjectId.Value, actorId, request.ParentDecisionIds);
        }

        transaction.Commit();
        return Get(connection, existing.Id.ToString());
    }

    public DecisionDetailDto? Transition(string lookup, int? actorId, DecisionStatus targetStatus)
    {
        using var connection = dataSource.OpenConnection();
        using var transaction = connection.BeginTransaction();
        var existing = Get(connection, lookup);
        if (existing is null)
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
        using var command = connection.CreateCommand();
        command.Transaction = transaction;
        command.CommandText = """
            UPDATE decisions
            SET
                status = @status,
                updated_at = @now,
                last_edited_by_id = @actor_id,
                committed_at = CASE WHEN @status = 'COMMITTED' THEN COALESCE(committed_at, @now) ELSE committed_at END,
                committed_by_id = CASE WHEN @status = 'COMMITTED' THEN COALESCE(committed_by_id, @actor_id) ELSE committed_by_id END,
                approved_at = CASE WHEN @from_status = 'AWAITING_APPROVAL' AND @status = 'COMMITTED' THEN @now ELSE approved_at END,
                approved_by_id = CASE WHEN @from_status = 'AWAITING_APPROVAL' AND @status = 'COMMITTED' THEN @actor_id ELSE approved_by_id END
            WHERE id = @id AND is_deleted = false
            """;
        command.Parameters.AddWithValue("id", existing.Id);
        command.Parameters.AddWithValue("status", targetStatus.ToString());
        command.Parameters.AddWithValue("from_status", existing.Status.ToString());
        command.Parameters.AddWithValue("now", now);
        AddNullableInteger(command, "actor_id", actorId);
        command.ExecuteNonQuery();

        RecordTransition(connection, transaction, existing.Id, existing.Status, targetStatus, actorId, now);
        transaction.Commit();
        return Get(connection, existing.Id.ToString());
    }

    public bool Delete(string lookup, int? actorId)
    {
        using var connection = dataSource.OpenConnection();
        var existing = Get(connection, lookup);
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
        return command.ExecuteNonQuery() > 0;
    }

    public DecisionSignalListResponse ListSignals(string lookup)
    {
        using var connection = dataSource.OpenConnection();
        var decision = Get(connection, lookup);
        return new DecisionSignalListResponse(decision is null ? Array.Empty<DecisionSignalDto>() : ListSignals(connection, decision.Id));
    }

    public DecisionSignalDto? CreateSignal(string lookup, int? actorId, DecisionSignalInput input)
    {
        using var connection = dataSource.OpenConnection();
        var decision = Get(connection, lookup);
        if (decision is null)
        {
            return null;
        }

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
        var signalId = Convert.ToInt32(command.ExecuteScalar());
        return ListSignals(connection, decision.Id).FirstOrDefault(signal => signal.Id == signalId);
    }

    public DecisionSignalDto? UpdateSignal(string lookup, int signalId, DecisionSignalInput input)
    {
        using var connection = dataSource.OpenConnection();
        var decision = Get(connection, lookup);
        if (decision is null)
        {
            return null;
        }

        var now = DateTimeOffset.UtcNow;
        var displayText = input.DisplayTextOverride
            ?? string.Join(" ", new[] { input.Metric, input.Movement, input.Period }.Where(value => !string.IsNullOrWhiteSpace(value)));
        using var command = connection.CreateCommand();
        command.CommandText = """
            UPDATE decision_signals
            SET
                updated_at = @now,
                metric = COALESCE(@metric, metric),
                movement = COALESCE(@movement, movement),
                period = COALESCE(@period, period),
                comparison = COALESCE(@comparison, comparison),
                scope_type = COALESCE(@scope_type, scope_type),
                scope_value = COALESCE(@scope_value, scope_value),
                delta_value = COALESCE(@delta_value, delta_value),
                delta_unit = COALESCE(@delta_unit, delta_unit),
                display_text = COALESCE(@display_text, display_text),
                display_text_override = COALESCE(@display_text_override, display_text_override)
            WHERE id = @signal_id AND decision_id = @decision_id AND is_deleted = false
            """;
        command.Parameters.AddWithValue("now", now);
        command.Parameters.AddWithValue("signal_id", signalId);
        command.Parameters.AddWithValue("decision_id", decision.Id);
        AddNullableText(command, "metric", input.Metric);
        AddNullableText(command, "movement", input.Movement);
        AddNullableText(command, "period", input.Period);
        AddNullableText(command, "comparison", input.Comparison);
        AddNullableText(command, "scope_type", input.ScopeType);
        AddNullableText(command, "scope_value", input.ScopeValue);
        AddNullableDecimal(command, "delta_value", input.DeltaValue);
        AddNullableText(command, "delta_unit", input.DeltaUnit);
        AddNullableText(command, "display_text", string.IsNullOrWhiteSpace(displayText) ? null : displayText);
        AddNullableText(command, "display_text_override", input.DisplayTextOverride);
        if (command.ExecuteNonQuery() == 0)
        {
            return null;
        }
        return ListSignals(connection, decision.Id).FirstOrDefault(signal => signal.Id == signalId);
    }

    public bool DeleteSignal(string lookup, int signalId)
    {
        using var connection = dataSource.OpenConnection();
        var decision = Get(connection, lookup);
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
        return command.ExecuteNonQuery() > 0;
    }

    public DecisionConnectionsResponse? GetConnections(string lookup)
    {
        using var connection = dataSource.OpenConnection();
        var decision = Get(connection, lookup);
        return decision is null ? null : BuildConnectionsPayload(connection, decision.Id, decision.ProjectId, decision.ProjectSeq);
    }

    public DecisionConnectionsResponse? UpdateConnections(string lookup, int? actorId, DecisionConnectionsUpdateRequest request)
    {
        using var connection = dataSource.OpenConnection();
        using var transaction = connection.BeginTransaction();
        var decision = Get(connection, lookup);
        if (decision is null || decision.ProjectId is null)
        {
            return null;
        }

        var targetIds = ResolveConnectionTargetIds(connection, decision, request);
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
            deleteCommand.ExecuteNonQuery();
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
            insertCommand.ExecuteNonQuery();
        }

        transaction.Commit();
        return BuildConnectionsPayload(connection, decision.Id, decision.ProjectId, decision.ProjectSeq);
    }

    public DecisionGraphResponse GetGraph(int projectId, bool allProjects, int userId, bool isSuperuser)
    {
        using var connection = dataSource.OpenConnection();
        var projectIds = ResolveGraphProjectIds(connection, projectId, allProjects, userId, isSuperuser);
        if (projectIds.Count == 0)
        {
            return new DecisionGraphResponse(Array.Empty<DecisionGraphNodeDto>(), Array.Empty<DecisionGraphEdgeDto>(), Array.Empty<DecisionGraphTopicDto>());
        }

        var topicLabels = LoadTopicLabels(connection, projectIds);
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
        using (var reader = nodesCommand.ExecuteReader())
        {
            while (reader.Read())
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
        using (var reader = edgesCommand.ExecuteReader())
        {
            while (reader.Read())
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

    public DecisionTopicLabelResponse? UpsertTopicLabel(int projectId, string topic, string title)
    {
        var normalizedTopic = NormalizeTopic(topic);
        var trimmedTitle = title.Trim();
        if (string.IsNullOrWhiteSpace(trimmedTitle) || trimmedTitle.Length > 80)
        {
            return null;
        }

        using var connection = dataSource.OpenConnection();
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

        using var reader = command.ExecuteReader();
        if (!reader.Read())
        {
            return null;
        }

        var savedTopic = NormalizeTopic(GetNullableString(reader, "topic"));
        return new DecisionTopicLabelResponse(
            savedTopic,
            GetNullableString(reader, "title") ?? TopicLabel(savedTopic),
            TopicLabel(savedTopic)
        );
    }

    public TopicLabelDeleteResult DeleteTopicLabel(int projectId, string topic)
    {
        var normalizedTopic = NormalizeTopic(topic);
        using var connection = dataSource.OpenConnection();

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
            if ((bool)usageCommand.ExecuteScalar()!)
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
        return command.ExecuteNonQuery() > 0 ? TopicLabelDeleteResult.Deleted : TopicLabelDeleteResult.NotFound;
    }

    public IReadOnlyList<DecisionReviewDto>? ListReviews(string lookup)
    {
        using var connection = dataSource.OpenConnection();
        var decision = Get(connection, lookup);
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
        using var reader = command.ExecuteReader();
        while (reader.Read())
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

    public DecisionDetailDto? CreateReview(string lookup, int? actorId, DecisionReviewInput input)
    {
        using var connection = dataSource.OpenConnection();
        using var transaction = connection.BeginTransaction();
        var decision = Get(connection, lookup);
        if (decision is null || decision.Status is not (DecisionStatus.COMMITTED or DecisionStatus.REVIEWED))
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
            command.ExecuteNonQuery();
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
            updateCommand.ExecuteNonQuery();
            RecordTransition(connection, transaction, decision.Id, DecisionStatus.COMMITTED, DecisionStatus.REVIEWED, actorId, now);
        }

        transaction.Commit();
        return Get(connection, decision.Id.ToString());
    }

    private DecisionDetailDto? Get(NpgsqlConnection connection, string lookup)
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
        command.Parameters.AddWithValue("id", lookupIsId ? numericId : 0);
        command.Parameters.AddWithValue("slug", lookup);

        using var reader = command.ExecuteReader();
        if (!reader.Read())
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
        reader.Close();

        return detail with
        {
            Signals = ListSignals(connection, detail.Id),
            Options = ListOptions(connection, detail.Id),
            OriginMeeting = GetOriginMeeting(connection, detail.Id),
        };
    }

    private DecisionOriginMeetingDto ResolveOriginMeeting(NpgsqlConnection connection, int meetingId, int projectId)
    {
        var origin = GetMeeting(connection, meetingId);
        if (origin is null)
        {
            throw new DecisionValidationException("origin_meeting_id", "Meeting does not exist.");
        }
        if (origin.ProjectId != projectId)
        {
            throw new DecisionValidationException("origin_meeting_id", "Meeting must belong to the same project as the decision.");
        }
        if (IsMeetingArchived(connection, meetingId))
        {
            throw new DecisionValidationException("origin_meeting_id", "Archived meetings cannot be used as a decision origin.");
        }
        return origin;
    }

    private DecisionOriginMeetingDto? GetOriginMeeting(NpgsqlConnection connection, int decisionId)
    {
        using var command = connection.CreateCommand();
        command.CommandText = """
            SELECT meeting_id
            FROM meetings_meetingdecisionorigin
            WHERE decision_id = @decision_id
            LIMIT 1
            """;
        command.Parameters.AddWithValue("decision_id", decisionId);
        var meetingId = command.ExecuteScalar();
        return meetingId is null ? null : GetMeeting(connection, Convert.ToInt32(meetingId));
    }

    private DecisionOriginMeetingDto? GetMeeting(NpgsqlConnection connection, int meetingId)
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

        using var reader = command.ExecuteReader();
        if (!reader.Read())
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

    private bool IsMeetingArchived(NpgsqlConnection connection, int meetingId)
    {
        using var command = connection.CreateCommand();
        command.CommandText = "SELECT is_archived FROM meetings_meeting WHERE id = @meeting_id";
        command.Parameters.AddWithValue("meeting_id", meetingId);
        return command.ExecuteScalar() is true;
    }

    private static void CreateMeetingDecisionOrigin(
        NpgsqlConnection connection,
        NpgsqlTransaction transaction,
        int meetingId,
        int decisionId,
        int? actorId,
        DateTimeOffset now
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
        command.ExecuteNonQuery();
    }

    private DecisionConnectionsResponse BuildConnectionsPayload(NpgsqlConnection connection, int decisionId, int? projectId, int projectSeq)
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
        using var reader = command.ExecuteReader();
        while (reader.Read())
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

    private IReadOnlyList<int>? ResolveConnectionTargetIds(
        NpgsqlConnection connection,
        DecisionDetailDto decision,
        DecisionConnectionsUpdateRequest request
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
            using var byIdReader = byIdCommand.ExecuteReader();
            while (byIdReader.Read())
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
        using var reader = command.ExecuteReader();
        while (reader.Read())
        {
            result.Add(reader.GetInt32(0));
        }
        return result.Count == seqs.Count ? result : null;
    }

    private IReadOnlyList<int> ResolveGraphProjectIds(NpgsqlConnection connection, int projectId, bool allProjects, int userId, bool isSuperuser)
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
                ORDER BY related.id
                """;
        command.Parameters.AddWithValue("project_id", projectId);
        command.Parameters.AddWithValue("user_id", userId);

        var result = new List<int>();
        using var reader = command.ExecuteReader();
        while (reader.Read())
        {
            result.Add(reader.GetInt32(0));
        }
        return result;
    }

    private Dictionary<(int ProjectId, string Topic), string> LoadTopicLabels(NpgsqlConnection connection, IReadOnlyList<int> projectIds)
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
        using var reader = command.ExecuteReader();
        while (reader.Read())
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

    private int NextProjectSequence(NpgsqlConnection connection, int projectId)
    {
        using var command = connection.CreateCommand();
        command.CommandText = "SELECT COALESCE(MAX(project_seq), 0) + 1 FROM decisions WHERE project_id = @project_id";
        command.Parameters.AddWithValue("project_id", projectId);
        return Convert.ToInt32(command.ExecuteScalar());
    }

    private IReadOnlyList<DecisionSignalDto> ListSignals(NpgsqlConnection connection, int decisionId)
    {
        using var command = connection.CreateCommand();
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
            ORDER BY created_at DESC, id DESC
            """;
        command.Parameters.AddWithValue("decision_id", decisionId);

        var items = new List<DecisionSignalDto>();
        using var reader = command.ExecuteReader();
        while (reader.Read())
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

    private IReadOnlyList<DecisionOptionDto> ListOptions(NpgsqlConnection connection, int decisionId)
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
        using var reader = command.ExecuteReader();
        while (reader.Read())
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

    private void ReplaceOptions(
        NpgsqlConnection connection,
        NpgsqlTransaction transaction,
        int decisionId,
        DateTimeOffset now,
        IReadOnlyList<DecisionOptionInput>? options
    )
    {
        using (var deleteCommand = connection.CreateCommand())
        {
            deleteCommand.Transaction = transaction;
            deleteCommand.CommandText = "DELETE FROM decision_options WHERE decision_id = @decision_id";
            deleteCommand.Parameters.AddWithValue("decision_id", decisionId);
            deleteCommand.ExecuteNonQuery();
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
            command.ExecuteNonQuery();
        }
    }

    private void ReplaceSignals(
        NpgsqlConnection connection,
        NpgsqlTransaction transaction,
        int decisionId,
        int? actorId,
        DateTimeOffset now,
        IReadOnlyList<DecisionSignalInput>? signals
    )
    {
        using (var deleteCommand = connection.CreateCommand())
        {
            deleteCommand.Transaction = transaction;
            deleteCommand.CommandText = "DELETE FROM decision_signals WHERE decision_id = @decision_id";
            deleteCommand.Parameters.AddWithValue("decision_id", decisionId);
            deleteCommand.ExecuteNonQuery();
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
            command.ExecuteNonQuery();
        }
    }

    private void ReplaceParentEdges(
        NpgsqlConnection connection,
        NpgsqlTransaction transaction,
        int decisionId,
        int projectId,
        int? actorId,
        IReadOnlyList<int>? parentDecisionIds
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
            deleteCommand.ExecuteNonQuery();
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
            command.ExecuteNonQuery();
        }
    }

    private void RecordTransition(
        NpgsqlConnection connection,
        NpgsqlTransaction transaction,
        int decisionId,
        DecisionStatus fromStatus,
        DecisionStatus toStatus,
        int? actorId,
        DateTimeOffset now
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
        command.ExecuteNonQuery();
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

    private static void AddNullableUuid(NpgsqlCommand command, string name, Guid? value)
    {
        command.Parameters.AddWithValue(name, NpgsqlDbType.Uuid, value.HasValue ? value.Value : DBNull.Value);
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
}
