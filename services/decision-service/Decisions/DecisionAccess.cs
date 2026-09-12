using Microsoft.AspNetCore.Mvc;
using Npgsql;

namespace DecisionService.Decisions;

public enum DecisionAccessAction
{
    View,
    Edit,
    Approve,
}

public sealed record DecisionAccessContext(
    bool Allowed,
    int UserId,
    string? Email,
    bool IsSuperuser,
    int? ProjectId,
    int? OrganizationId,
    string? Role,
    int? RoleLevel
);

public interface IDecisionAccessService
{
    Task<DecisionAccessContext?> AuthorizeAsync(
        HttpRequest request,
        int? projectId,
        DecisionAccessAction action,
        CancellationToken cancellationToken
    );
}

public sealed class JwtDecisionAccessService(NpgsqlDataSource dataSource, DjangoJwtValidator jwtValidator) : IDecisionAccessService
{
    public async Task<DecisionAccessContext?> AuthorizeAsync(
        HttpRequest request,
        int? projectId,
        DecisionAccessAction action,
        CancellationToken cancellationToken
    )
    {
        var token = BearerToken(request);
        if (string.IsNullOrWhiteSpace(token))
        {
            return null;
        }

        var principal = jwtValidator.Validate(token);
        if (principal is null)
        {
            return null;
        }

        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var userCommand = connection.CreateCommand();
        userCommand.CommandText = """
            SELECT id, email, is_superuser, is_active, auth_token_version
            FROM core_customuser
            WHERE id = @user_id
            """;
        userCommand.Parameters.AddWithValue("user_id", principal.UserId);

        await using var userReader = await userCommand.ExecuteReaderAsync(cancellationToken);
        if (!await userReader.ReadAsync(cancellationToken))
        {
            return null;
        }

        var email = userReader.GetString(userReader.GetOrdinal("email"));
        var isSuperuser = userReader.GetBoolean(userReader.GetOrdinal("is_superuser"));
        var isActive = userReader.GetBoolean(userReader.GetOrdinal("is_active"));
        var tokenVersion = userReader.GetInt32(userReader.GetOrdinal("auth_token_version"));
        await userReader.CloseAsync();

        if (!isActive || tokenVersion != principal.AuthTokenVersion)
        {
            return null;
        }

        if (isSuperuser)
        {
            var organizationId = projectId is null
                ? null
                : await ProjectOrganizationIdAsync(connection, projectId.Value, cancellationToken);
            return new DecisionAccessContext(
                true,
                principal.UserId,
                email,
                true,
                projectId,
                organizationId,
                "superuser",
                DecisionPermissionPolicy.SuperuserLevel
            );
        }

        if (projectId is null)
        {
            return new DecisionAccessContext(false, principal.UserId, email, false, null, null, null, null);
        }

        await using var memberCommand = connection.CreateCommand();
        memberCommand.CommandText = """
            SELECT pm.role, p.organization_id
            FROM core_projectmember pm
            INNER JOIN core_project p ON p.id = pm.project_id
            WHERE pm.user_id = @user_id
              AND pm.project_id = @project_id
              AND pm.is_active = true
              AND p.is_deleted = false
            LIMIT 1
            """;
        memberCommand.Parameters.AddWithValue("user_id", principal.UserId);
        memberCommand.Parameters.AddWithValue("project_id", projectId.Value);

        await using var memberReader = await memberCommand.ExecuteReaderAsync(cancellationToken);
        if (!await memberReader.ReadAsync(cancellationToken))
        {
            return new DecisionAccessContext(false, principal.UserId, email, false, projectId, null, null, null);
        }

        var role = memberReader.GetString(memberReader.GetOrdinal("role")).Trim();
        var memberOrganizationIdOrdinal = memberReader.GetOrdinal("organization_id");
        var memberOrganizationId = memberReader.IsDBNull(memberOrganizationIdOrdinal)
            ? (int?)null
            : memberReader.GetInt32(memberOrganizationIdOrdinal);
        var roleLevel = DecisionPermissionPolicy.RoleLevel(role);
        var allowed = roleLevel <= DecisionPermissionPolicy.RequiredLevel(action);

        return new DecisionAccessContext(
            allowed,
            principal.UserId,
            email,
            false,
            projectId,
            memberOrganizationId,
            role,
            roleLevel
        );
    }

    private static string? BearerToken(HttpRequest request)
    {
        var authorization = request.Headers.Authorization.ToString();
        const string prefix = "Bearer ";
        return authorization.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)
            ? authorization[prefix.Length..].Trim()
            : null;
    }

    private static async Task<int?> ProjectOrganizationIdAsync(
        NpgsqlConnection connection,
        int projectId,
        CancellationToken cancellationToken
    )
    {
        await using var command = connection.CreateCommand();
        command.CommandText = "SELECT organization_id FROM core_project WHERE id = @project_id AND is_deleted = false";
        command.Parameters.AddWithValue("project_id", projectId);
        var result = await command.ExecuteScalarAsync(cancellationToken);
        return result is null or DBNull ? null : Convert.ToInt32(result);
    }
}

public static class DecisionAccessResponses
{
    public static ObjectResult UnauthorizedOrForbidden(DecisionAccessContext? access)
    {
        if (access is null)
        {
            return new UnauthorizedObjectResult(new { detail = "Authentication credentials were not provided." });
        }
        return new ObjectResult(new { detail = "You do not have permission to access this decision." })
        {
            StatusCode = StatusCodes.Status403Forbidden,
        };
    }
}
