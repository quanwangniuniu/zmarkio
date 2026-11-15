using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Npgsql;

namespace DecisionService.Decisions;

public sealed partial class SchemaMigrator(NpgsqlDataSource dataSource)
{
    public async Task<IReadOnlyList<string>> ApplyAsync(string migrationsDirectory, CancellationToken cancellationToken)
    {
        var migrations = await LoadAsync(migrationsDirectory, cancellationToken);

        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var transaction = await connection.BeginTransactionAsync(cancellationToken);

        await using (var ledger = new NpgsqlCommand(
            """
            CREATE TABLE IF NOT EXISTS decision_schema_migrations (
                version text PRIMARY KEY,
                checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """,
            connection,
            transaction))
        {
            await ledger.ExecuteNonQueryAsync(cancellationToken);
        }

        var history = new Dictionary<string, string>(StringComparer.Ordinal);
        await using (var select = new NpgsqlCommand(
            "SELECT version, checksum FROM decision_schema_migrations",
            connection,
            transaction))
        await using (var reader = await select.ExecuteReaderAsync(cancellationToken))
        {
            while (await reader.ReadAsync(cancellationToken))
            {
                history[reader.GetString(0)] = reader.GetString(1);
            }
        }

        var latest = history.Keys.OrderBy(version => version, StringComparer.Ordinal).LastOrDefault() ?? string.Empty;
        var applied = new List<string>();
        foreach (var migration in migrations)
        {
            if (history.TryGetValue(migration.Version, out var checksum))
            {
                if (checksum != migration.Checksum)
                {
                    throw new InvalidOperationException($"Checksum mismatch for {migration.Name}.");
                }
                continue;
            }
            if (string.CompareOrdinal(migration.Version, latest) <= 0)
            {
                throw new InvalidOperationException($"Cannot apply {migration.Name} after recorded version {latest}.");
            }

            await using (var run = new NpgsqlCommand(migration.Sql, connection, transaction))
            {
                await run.ExecuteNonQueryAsync(cancellationToken);
            }
            await using (var record = new NpgsqlCommand(
                "INSERT INTO decision_schema_migrations (version, checksum) VALUES (@version, @checksum)",
                connection,
                transaction))
            {
                record.Parameters.AddWithValue("version", migration.Version);
                record.Parameters.AddWithValue("checksum", migration.Checksum);
                await record.ExecuteNonQueryAsync(cancellationToken);
            }
            applied.Add(migration.Name);
        }

        await transaction.CommitAsync(cancellationToken);
        return applied;
    }

    private static async Task<IReadOnlyList<Migration>> LoadAsync(string directory, CancellationToken cancellationToken)
    {
        var migrations = new List<Migration>();
        var versions = new HashSet<string>(StringComparer.Ordinal);
        var paths = Directory.GetFiles(directory, "*.sql").OrderBy(path => Path.GetFileName(path), StringComparer.Ordinal);
        foreach (var path in paths)
        {
            var name = Path.GetFileName(path);
            var match = MigrationFileName().Match(name);
            if (!match.Success)
            {
                throw new InvalidOperationException($"Migration must use NNN_name.sql: {name}");
            }
            var version = match.Groups[1].Value;
            if (!versions.Add(version))
            {
                throw new InvalidOperationException($"Duplicate migration version: {version}");
            }
            var sql = (await File.ReadAllTextAsync(path, cancellationToken)).Replace("\r\n", "\n");
            var checksum = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(sql)));
            migrations.Add(new Migration(version, name, sql, checksum));
        }
        return migrations;
    }

    private sealed record Migration(string Version, string Name, string Sql, string Checksum);

    [GeneratedRegex(@"^(\d{3})_[A-Za-z0-9_-]+\.sql$")]
    private static partial Regex MigrationFileName();
}
