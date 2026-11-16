using System.Text.RegularExpressions;
using Dapper;
using Npgsql;

namespace DecisionService.Decisions;

public sealed partial class TenantConnectionFactory(
    NpgsqlDataSource dataSource,
    DjangoJwtValidator jwtValidator,
    IHttpContextAccessor httpContextAccessor
)
{
    private const string PublicSchema = "public";

    private string? schema;

    public async Task<TenantSession> OpenAsync(CancellationToken cancellationToken)
    {
        schema ??= await ResolveSchemaAsync(cancellationToken);
        var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        try
        {
            var transaction = await connection.BeginTransactionAsync(cancellationToken);
            await connection.ExecuteAsync(new CommandDefinition(
                SearchPathSql(schema),
                transaction: transaction,
                cancellationToken: cancellationToken
            ));
            return new TenantSession(connection, transaction);
        }
        catch
        {
            await connection.DisposeAsync();
            throw;
        }
    }

    private static string SlugToSchemaName(string slug)
    {
        return "org_" + NonAlphanumeric().Replace(slug, "_");
    }

    private static bool IsValidSchemaName(string value)
    {
        return ValidSchema().IsMatch(value);
    }

    private static string SearchPathSql(string schemaName)
    {
        if (!IsValidSchemaName(schemaName))
        {
            throw new InvalidOperationException($"Refusing to use invalid tenant schema name '{schemaName}'.");
        }
        return $"SET LOCAL search_path = \"{schemaName}\", public";
    }

    private async Task<string> ResolveSchemaAsync(CancellationToken cancellationToken)
    {
        var token = httpContextAccessor.HttpContext?.Request.BearerToken();
        if (string.IsNullOrWhiteSpace(token))
        {
            return PublicSchema;
        }

        var principal = jwtValidator.Validate(token);
        if (principal is null)
        {
            return PublicSchema;
        }

        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        var parameters = new DynamicParameters();
        parameters.Add("UserId", principal.UserId);
        var slug = await connection.ExecuteScalarAsync<string?>(new CommandDefinition(
            """
            SELECT o.slug
            FROM public.core_customuser u
            INNER JOIN public.core_organization o
                ON o.id = COALESCE(u.current_organization_id, u.organization_id)
            WHERE u.id = @UserId
            """,
            parameters,
            cancellationToken: cancellationToken
        ));
        if (string.IsNullOrWhiteSpace(slug))
        {
            return PublicSchema;
        }

        return SlugToSchemaName(slug);
    }

    [GeneratedRegex("[^a-zA-Z0-9]")]
    private static partial Regex NonAlphanumeric();

    [GeneratedRegex("^(public|org_[A-Za-z0-9_]+)$")]
    private static partial Regex ValidSchema();
}

// search_path is SET LOCAL, so it only lives inside this transaction: read results back before Commit().
public sealed class TenantSession(NpgsqlConnection connection, NpgsqlTransaction transaction) : IDisposable, IAsyncDisposable
{
    public NpgsqlConnection Connection { get; } = connection;

    public NpgsqlTransaction Transaction { get; } = transaction;

    public Task CommitAsync() => Transaction.CommitAsync(CancellationToken.None);

    public void Dispose()
    {
        Transaction.Dispose();
        Connection.Dispose();
    }

    public async ValueTask DisposeAsync()
    {
        await Transaction.DisposeAsync();
        await Connection.DisposeAsync();
    }
}
