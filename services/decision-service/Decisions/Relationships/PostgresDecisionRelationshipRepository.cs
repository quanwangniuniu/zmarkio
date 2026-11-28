using Npgsql;
using NpgsqlTypes;

namespace DecisionService.Decisions.Relationships;

internal readonly record struct ConnectionSource(int Id, int? ProjectId, int ProjectSeq);

public sealed class PostgresDecisionRelationshipRepository
{
    internal async Task<ConnectionSource?> FindSourceAsync(TenantSession session, string lookup, CancellationToken ct)
    {
        var lookupIsId = int.TryParse(lookup, out var numericId);
        using var command = CreateCommand(session);
        command.CommandText = """
            SELECT id, project_id, project_seq
            FROM decisions
            WHERE is_deleted = false
              AND ((@lookup_is_id = true AND id = @id) OR slug = @slug)
            """;
        command.Parameters.AddWithValue("lookup_is_id", lookupIsId);
        command.Parameters.AddWithValue("id", numericId);
        command.Parameters.AddWithValue("slug", lookup);

        await using var reader = await command.ExecuteReaderAsync(ct);
        return await reader.ReadAsync(ct)
            ? new ConnectionSource(reader.GetInt32(0), reader.IsDBNull(1) ? null : reader.GetInt32(1), reader.GetInt32(2))
            : null;
    }

    internal async Task<DecisionConnectionsResponse> BuildConnectionsPayloadAsync(TenantSession session, int decisionId, int? projectId, int projectSeq, CancellationToken ct)
    {
        using var command = CreateCommand(session);
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
                ReadNullableString(reader, "other_slug") ?? $"decision-{otherId}",
                reader.GetInt32(reader.GetOrdinal("other_seq")),
                ReadNullableString(reader, "other_title")
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

    internal async Task<IReadOnlyList<int>> FindAllowedTargetIdsAsync(
        TenantSession session, int projectId, int[] ids, int? actorId, bool isSuperuser, CancellationToken ct)
    {
        using var byIdCommand = CreateCommand(session);
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
              AND (
                  target.project_id = @project_id
                  OR @is_superuser
                  OR EXISTS (
                      SELECT 1
                      FROM core_projectmember member
                      WHERE member.project_id = target.project_id
                        AND member.user_id = @user_id
                        AND member.is_active = true
                        AND member.is_deleted = false
                  )
              )
            """;
        byIdCommand.Parameters.AddWithValue("project_id", projectId);
        byIdCommand.Parameters.AddWithValue("ids", ids);
        byIdCommand.Parameters.AddWithValue("is_superuser", isSuperuser);
        AddNullableInteger(byIdCommand, "user_id", actorId);

        var byIdResult = new List<int>();
        await using var byIdReader = await byIdCommand.ExecuteReaderAsync(ct);
        while (await byIdReader.ReadAsync(ct))
        {
            byIdResult.Add(byIdReader.GetInt32(0));
        }
        return byIdResult;
    }

    internal async Task<IReadOnlyList<int>> FindTargetsBySequenceAsync(
        TenantSession session, int projectId, int[] seqs, CancellationToken ct)
    {
        using var command = CreateCommand(session);
        command.CommandText = """
            SELECT id
            FROM decisions
            WHERE project_id = @project_id
              AND project_seq = ANY(@seqs)
              AND is_deleted = false
            """;
        command.Parameters.AddWithValue("project_id", projectId);
        command.Parameters.AddWithValue("seqs", seqs);
        var result = new List<int>();
        await using var reader = await command.ExecuteReaderAsync(ct);
        while (await reader.ReadAsync(ct))
        {
            result.Add(reader.GetInt32(0));
        }
        return result;
    }

    internal async Task ReplaceRelatedEdgesAsync(
        TenantSession session, int decisionId, IReadOnlyList<int> targetIds, int? actorId, CancellationToken ct)
    {
        using (var deleteCommand = CreateCommand(session))
        {
            deleteCommand.CommandText = """
                DELETE FROM decision_edges
                WHERE edge_type = 'RELATED'
                  AND (from_decision_id = @decision_id OR to_decision_id = @decision_id)
                """;
            deleteCommand.Parameters.AddWithValue("decision_id", decisionId);
            await deleteCommand.ExecuteNonQueryAsync(ct);
        }

        foreach (var targetId in targetIds.Where(id => id != decisionId).Distinct())
        {
            var fromId = Math.Min(decisionId, targetId);
            var toId = Math.Max(decisionId, targetId);
            using var insertCommand = CreateCommand(session);
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
    }

    internal async Task<long> CountValidParentsAsync(
        TenantSession session, int projectId, int[] candidateParentIds, CancellationToken ct)
    {
        using var parentCheck = CreateCommand(session);
        parentCheck.CommandText = """
            SELECT COUNT(*)
            FROM decisions
            WHERE id = ANY(@parent_ids)
              AND project_id = @project_id
              AND is_deleted = false
            """;
        parentCheck.Parameters.AddWithValue("parent_ids", candidateParentIds);
        parentCheck.Parameters.AddWithValue("project_id", projectId);
        return Convert.ToInt64(await parentCheck.ExecuteScalarAsync(ct));
    }

    internal async Task<bool> IntroducesCycleAsync(
        TenantSession session, int decisionId, int[] candidateParentIds, CancellationToken ct)
    {
        using var validationCommand = CreateCommand(session);
        validationCommand.CommandText = """
            WITH RECURSIVE descendants(id) AS (
                SELECT to_decision_id
                FROM decision_edges
                WHERE from_decision_id = @decision_id
                  AND edge_type = 'FOLLOW_UP'
                  AND is_deleted = false
                UNION
                SELECT edge.to_decision_id
                FROM decision_edges edge
                JOIN descendants ON descendants.id = edge.from_decision_id
                WHERE edge.edge_type = 'FOLLOW_UP'
                  AND edge.is_deleted = false
            )
            SELECT EXISTS (
                SELECT 1 FROM descendants WHERE id = ANY(@parent_ids)
            )
            """;
        validationCommand.Parameters.AddWithValue("decision_id", decisionId);
        validationCommand.Parameters.AddWithValue("parent_ids", candidateParentIds);
        return Convert.ToBoolean(await validationCommand.ExecuteScalarAsync(ct));
    }

    internal async Task ReplaceFollowUpEdgesAsync(
        TenantSession session, int decisionId, int projectId, int? actorId, int[] candidateParentIds, CancellationToken ct)
    {
        using (var deleteCommand = CreateCommand(session))
        {
            deleteCommand.CommandText = "DELETE FROM decision_edges WHERE to_decision_id = @decision_id AND edge_type = 'FOLLOW_UP'";
            deleteCommand.Parameters.AddWithValue("decision_id", decisionId);
            await deleteCommand.ExecuteNonQueryAsync(ct);
        }

        foreach (var parentId in candidateParentIds)
        {
            using var command = CreateCommand(session);
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

    private static NpgsqlCommand CreateCommand(TenantSession session)
    {
        var command = session.Connection.CreateCommand();
        command.Transaction = session.Transaction;
        return command;
    }

    private static void AddNullableInteger(NpgsqlCommand command, string name, int? value) =>
        command.Parameters.AddWithValue(name, NpgsqlDbType.Integer, value.HasValue ? value.Value : DBNull.Value);

    private static string? ReadNullableString(NpgsqlDataReader reader, string column)
    {
        var ordinal = reader.GetOrdinal(column);
        return reader.IsDBNull(ordinal) ? null : reader.GetString(ordinal);
    }
}
