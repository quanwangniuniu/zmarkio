using DecisionService.Decisions;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Configuration;
using Npgsql;
using Testcontainers.PostgreSql;
using Xunit;

namespace DecisionService.Tests;

public sealed class ConcurrentDraftCreateTests : IAsyncLifetime
{
    private const int ProjectId = 10;
    private const int Creates = 20;

    private readonly PostgreSqlContainer postgres = new PostgreSqlBuilder("postgres:16-alpine").Build();
    private NpgsqlDataSource dataSource = null!;

    public async Task InitializeAsync()
    {
        await postgres.StartAsync();
        dataSource = NpgsqlDataSource.Create(postgres.GetConnectionString());
        await using var command = dataSource.CreateCommand(TenantIsolationTests.Tables("public") + $"""
            INSERT INTO public.core_project (id, name) VALUES ({ProjectId}, 'Shared');
            """);
        await command.ExecuteNonQueryAsync();
    }

    public async Task DisposeAsync()
    {
        await dataSource.DisposeAsync();
        await postgres.DisposeAsync();
    }

    [Fact]
    public async Task Parallel_creates_in_one_project_get_distinct_gap_free_sequences()
    {
        using var start = new Barrier(Creates);
        var creates = Enumerable.Range(1, Creates).Select(i => Task.Run(() =>
        {
            start.SignalAndWait();
            return Repository().Create(ProjectId, null, Draft($"Draft {i}"));
        }));

        var created = await Task.WhenAll(creates);

        Assert.Equal(Enumerable.Range(1, Creates), created.Select(decision => decision.ProjectSeq).Order());
    }

    private PostgresDecisionRepository Repository() =>
        new(new TenantConnectionFactory(
            dataSource,
            new DjangoJwtValidator(new ConfigurationManager { ["SECRET_KEY"] = "test-signing-key" }),
            new HttpContextAccessor()));

    private static CreateDecisionDraftRequest Draft(string title) =>
        new(title, null, null, null, null, null, false, false, null, null, null, null, null);
}
