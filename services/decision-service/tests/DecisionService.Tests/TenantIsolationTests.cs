using DecisionService.Decisions;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Configuration;
using Npgsql;
using Testcontainers.PostgreSql;
using Xunit;

namespace DecisionService.Tests;

public sealed class TenantIsolationTests : IAsyncLifetime
{
    private const string SigningKey = "test-signing-key";
    private const int AcmeUser = 1;
    private const int GlobexUser = 2;
    private const int SharedProject = 10;
    private const string GlobexSlug = "globex-secret";

    private readonly PostgreSqlContainer postgres = new PostgreSqlBuilder("postgres:16-alpine").Build();
    private NpgsqlDataSource dataSource = null!;

    public async Task InitializeAsync()
    {
        await postgres.StartAsync();
        dataSource = NpgsqlDataSource.Create(postgres.GetConnectionString());
        await Execute("""
            CREATE TABLE public.core_organization (id bigint PRIMARY KEY, slug varchar NOT NULL);
            CREATE TABLE public.core_customuser (
                id bigint PRIMARY KEY,
                email varchar NOT NULL,
                is_superuser boolean NOT NULL DEFAULT false,
                is_active boolean NOT NULL DEFAULT true,
                auth_token_version integer NOT NULL DEFAULT 0,
                organization_id bigint,
                current_organization_id bigint
            );
            INSERT INTO public.core_organization VALUES (1, 'acme'), (2, 'globex');
            INSERT INTO public.core_customuser (id, email, organization_id, current_organization_id) VALUES
                (1, 'a@acme.test', 1, NULL),
                (2, 'b@globex.test', 2, NULL);
            """);
        await Execute(Tables("public") + $"""
            INSERT INTO public.decisions (id, slug, title, status, project_id, project_seq)
            VALUES (900, 'legacy-public', 'Legacy public row', 'DRAFT', {SharedProject}, 1);
            """);
        foreach (var (schema, org) in new[] { ("org_acme", 1), ("org_globex", 2) })
        {
            await Execute($"CREATE SCHEMA {schema};" + Tables(schema) + $"""
                INSERT INTO {schema}.core_project (id, name, organization_id) VALUES ({SharedProject}, '{schema}', {org});
                INSERT INTO {schema}.core_projectmember (user_id, project_id, role) VALUES ({org}, {SharedProject}, 'owner');
                """);
        }
        await Execute($"""
            INSERT INTO org_globex.decisions (id, slug, title, status, project_id, project_seq)
            VALUES (500, '{GlobexSlug}', 'Globex secret', 'DRAFT', {SharedProject}, 1);
            """);
    }

    public async Task DisposeAsync()
    {
        await dataSource.DisposeAsync();
        await postgres.DisposeAsync();
    }

    [Fact]
    public async Task Created_decision_lands_in_callers_org_schema()
    {
        var created = Repository(AcmeUser).Create(SharedProject, AcmeUser, Draft("Acme plan"));

        Assert.Equal(1, await Count("org_acme", created.Slug));
        Assert.Equal(0, await Count("org_globex", created.Slug));
        Assert.Equal(0, await Count("public", created.Slug));
    }

    [Fact]
    public void Same_project_id_lists_only_the_callers_org()
    {
        Repository(AcmeUser).Create(SharedProject, AcmeUser, Draft("Acme plan"));

        var acme = Repository(AcmeUser).List(SharedProject, null, 50, 0).Items.Select(item => item.Title).ToList();
        var globex = Repository(GlobexUser).List(SharedProject, null, 50, 0).Items.Select(item => item.Title).ToList();

        Assert.Equal(["Acme plan"], acme);
        Assert.Equal(["Globex secret"], globex);
    }

    [Fact]
    public async Task User_cannot_list_read_edit_or_link_another_orgs_decisions()
    {
        var repository = Repository(AcmeUser);
        var own = repository.Create(SharedProject, AcmeUser, Draft("Acme plan"));

        Assert.DoesNotContain(repository.List(null, null, 50, 0).Items, item => item.Title == "Globex secret");
        Assert.Null(repository.Get(GlobexSlug));
        Assert.Null(repository.UpdateDraft(GlobexSlug, AcmeUser, new UpdateDecisionDraftRequest(
            "hijacked", null, null, null, null, null, null, null, null, null, null)));
        Assert.Null(repository.UpdateConnections(own.Slug, AcmeUser, new DecisionConnectionsUpdateRequest([500], null)));
        Assert.Equal(0, await Scalar("SELECT count(*) FROM org_acme.decision_edges"));
        Assert.Equal(0, await Scalar("SELECT count(*) FROM org_globex.decision_edges"));
        Assert.Equal("Globex secret", await Scalar<string>($"SELECT title FROM org_globex.decisions WHERE slug = '{GlobexSlug}'"));
    }

    [Fact]
    public async Task Member_access_resolves_role_and_org_from_callers_schema()
    {
        var access = await Access(AcmeUser).AuthorizeAsync(
            Request(AcmeUser), SharedProject, DecisionAccessAction.Edit, CancellationToken.None);

        Assert.NotNull(access);
        Assert.Equal("owner", access.Role);
        Assert.Equal(1, access.OrganizationId);
    }

    [Fact]
    public async Task Superuser_access_resolves_org_from_callers_schema()
    {
        await Execute($"UPDATE public.core_customuser SET is_superuser = true WHERE id = {GlobexUser};");

        var access = await Access(GlobexUser).AuthorizeAsync(
            Request(GlobexUser), SharedProject, DecisionAccessAction.Edit, CancellationToken.None);

        Assert.NotNull(access);
        Assert.Equal(2, access.OrganizationId);
    }

    private PostgresDecisionRepository Repository(int userId) => new(Connections(userId));

    private JwtDecisionAccessService Access(int userId) => new(Connections(userId), Validator());

    private TenantConnectionFactory Connections(int userId) =>
        new(dataSource, Validator(), new HttpContextAccessor { HttpContext = Request(userId).HttpContext });

    private static HttpRequest Request(int userId)
    {
        var context = new DefaultHttpContext();
        context.Request.Headers.Authorization = "Bearer " + DjangoJwtValidator.CreateSignedTokenForTests(SigningKey, userId);
        return context.Request;
    }

    private static DjangoJwtValidator Validator() =>
        new(new ConfigurationManager { ["SECRET_KEY"] = SigningKey });

    private static CreateDecisionDraftRequest Draft(string title) =>
        new(title, null, null, null, null, null, false, false, null, null, null, null, null);

    private async Task<long> Count(string schema, string slug)
    {
        await using var command = dataSource.CreateCommand($"SELECT count(*) FROM {schema}.decisions WHERE slug = @slug");
        command.Parameters.AddWithValue("slug", slug);
        return (long)(await command.ExecuteScalarAsync())!;
    }

    private async Task<long> Scalar(string sql) => await Scalar<long>(sql);

    private async Task<T> Scalar<T>(string sql)
    {
        await using var command = dataSource.CreateCommand(sql);
        return (T)(await command.ExecuteScalarAsync())!;
    }

    private async Task Execute(string sql)
    {
        await using var command = dataSource.CreateCommand(sql);
        await command.ExecuteNonQueryAsync();
    }

    private static string Tables(string s) => $"""
        CREATE TABLE {s}.core_project (
            id bigint PRIMARY KEY, name varchar NOT NULL, organization_id bigint, is_deleted boolean NOT NULL DEFAULT false);
        CREATE TABLE {s}.core_projectmember (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id bigint NOT NULL, project_id bigint NOT NULL,
            role varchar NOT NULL, is_active boolean NOT NULL DEFAULT true,
            is_deleted boolean NOT NULL DEFAULT false);
        CREATE TABLE {s}.decisions (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, slug varchar NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
            is_deleted boolean NOT NULL DEFAULT false, title varchar, topic varchar NOT NULL DEFAULT 'general',
            context_summary text, reasoning text, risk_level varchar, confidence integer, status varchar NOT NULL,
            requires_approval boolean NOT NULL DEFAULT false, planned_decision_date timestamptz, committed_at timestamptz,
            approved_at timestamptz, is_reference_case boolean NOT NULL DEFAULT false,
            created_by_agent boolean NOT NULL DEFAULT false, agent_session_id uuid, is_pre_draft boolean NOT NULL DEFAULT false,
            project_id bigint, project_seq integer NOT NULL, author_id bigint, last_edited_by_id bigint,
            committed_by_id bigint, approved_by_id bigint);
        CREATE TABLE {s}.decision_options (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
            is_deleted boolean NOT NULL DEFAULT false, decision_id bigint NOT NULL, text text NOT NULL,
            is_selected boolean NOT NULL DEFAULT false, "order" integer NOT NULL DEFAULT 0);
        CREATE TABLE {s}.decision_signals (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
            is_deleted boolean NOT NULL DEFAULT false, decision_id bigint NOT NULL, author_id bigint, metric varchar,
            movement varchar, period varchar, comparison varchar NOT NULL, scope_type varchar, scope_value varchar,
            delta_value numeric, delta_unit varchar, display_text text NOT NULL, display_text_override text);
        CREATE TABLE {s}.decision_edges (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
            is_deleted boolean NOT NULL DEFAULT false, edge_type varchar NOT NULL, from_decision_id bigint NOT NULL,
            to_decision_id bigint NOT NULL, created_by_id bigint, UNIQUE (from_decision_id, to_decision_id));
        CREATE TABLE {s}.decision_reviews (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, decision_id bigint NOT NULL,
            is_deleted boolean NOT NULL DEFAULT false);
        CREATE TABLE {s}.meetings_meetingdecisionorigin (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, meeting_id bigint NOT NULL, decision_id bigint NOT NULL);
        """;
}
