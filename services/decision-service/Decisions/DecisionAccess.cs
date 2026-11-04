using Dapper;
using Microsoft.AspNetCore.Mvc;

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

public sealed class JwtDecisionAccessService(TenantConnectionFactory connections, DjangoJwtValidator jwtValidator) : IDecisionAccessService
{
    public async Task<DecisionAccessContext?> AuthorizeAsync(
        HttpRequest request,
        int? projectId,
        DecisionAccessAction action,
        CancellationToken cancellationToken
    )
    {
        var token = request.BearerToken();
        if (string.IsNullOrWhiteSpace(token))
        {
            return null;
        }

        var principal = jwtValidator.Validate(token);
        if (principal is null)
        {
            return null;
        }

        await using var session = await connections.OpenAsync(cancellationToken);
        var connection = session.Connection;
        var userParameters = new DynamicParameters();
        userParameters.Add("UserId", principal.UserId);
        var user = await connection.QuerySingleOrDefaultAsync<UserRow>(new CommandDefinition(
            """
            SELECT
                email AS Email,
                is_superuser AS IsSuperuser,
                is_active AS IsActive,
                auth_token_version AS AuthTokenVersion
            FROM public.core_customuser
            WHERE id = @UserId
            """,
            userParameters,
            session.Transaction,
            cancellationToken: cancellationToken
        ));
        if (user is null)
        {
            return null;
        }

        var email = user.Email;
        if (!user.IsActive || user.AuthTokenVersion != principal.AuthTokenVersion)
        {
            return null;
        }

        if (user.IsSuperuser)
        {
            var organizationId = projectId is null
                ? null
                : await ProjectOrganizationIdAsync(session, projectId.Value, cancellationToken);
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

        var memberParameters = new DynamicParameters();
        memberParameters.Add("UserId", principal.UserId);
        memberParameters.Add("ProjectId", projectId.Value);
        var member = await connection.QueryFirstOrDefaultAsync<MemberRow>(new CommandDefinition(
            """
            SELECT pm.role AS Role, p.organization_id AS OrganizationId
            FROM core_projectmember pm
            INNER JOIN core_project p ON p.id = pm.project_id
            WHERE pm.user_id = @UserId
              AND pm.project_id = @ProjectId
              AND pm.is_active = true
              AND pm.is_deleted = false
              AND p.is_deleted = false
            LIMIT 1
            """,
            memberParameters,
            session.Transaction,
            cancellationToken: cancellationToken
        ));
        if (member is null)
        {
            return new DecisionAccessContext(false, principal.UserId, email, false, projectId, null, null, null);
        }

        var role = member.Role.Trim();
        var memberOrganizationId = (int?)member.OrganizationId;
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

    private static Task<int?> ProjectOrganizationIdAsync(
        TenantSession session,
        int projectId,
        CancellationToken cancellationToken
    )
    {
        var parameters = new DynamicParameters();
        parameters.Add("ProjectId", projectId);
        return session.Connection.ExecuteScalarAsync<int?>(new CommandDefinition(
            "SELECT organization_id FROM core_project WHERE id = @ProjectId AND is_deleted = false",
            parameters,
            session.Transaction,
            cancellationToken: cancellationToken
        ));
    }

    private sealed record UserRow(string Email, bool IsSuperuser, bool IsActive, int AuthTokenVersion);

    private sealed record MemberRow(string Role, long? OrganizationId);
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
