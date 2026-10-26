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

    public string Schema => schema ??= ResolveSchema();

    public TenantSession Open()
    {
        var connection = dataSource.OpenConnection();
        try
        {
            var transaction = connection.BeginTransaction();
            connection.Execute(SearchPathSql(Schema), transaction: transaction);
            return new TenantSession(connection, transaction);
        }
        catch
        {
            connection.Dispose();
            throw;
        }
    }

    public async Task<TenantSession> OpenAsync(CancellationToken cancellationToken)
    {
        var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        try
        {
            var transaction = await connection.BeginTransactionAsync(cancellationToken);
            await connection.ExecuteAsync(new CommandDefinition(
                SearchPathSql(Schema),
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

    private string ResolveSchema()
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

        using var connection = dataSource.OpenConnection();
        var parameters = new DynamicParameters();
        parameters.Add("UserId", principal.UserId);
        var slug = connection.ExecuteScalar<string?>(
            """
            SELECT o.slug
            FROM public.core_customuser u
            INNER JOIN public.core_organization o
                ON o.id = COALESCE(u.current_organization_id, u.organization_id)
            WHERE u.id = @UserId
            """,
            parameters
        );
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

    public void Commit() => Transaction.Commit();

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
