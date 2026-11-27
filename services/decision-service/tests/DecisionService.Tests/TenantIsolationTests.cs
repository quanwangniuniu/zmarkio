using DecisionService.Decisions;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Options;
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
        var created = await Repository(AcmeUser).CreateAsync(SharedProject, AcmeUser, Draft("Acme plan"), CancellationToken.None);

        Assert.Equal(1, await Count("org_acme", created.Slug));
        Assert.Equal(0, await Count("org_globex", created.Slug));
        Assert.Equal(0, await Count("public", created.Slug));
    }

    [Fact]
    public async Task Same_project_id_lists_only_the_callers_org()
    {
        await Repository(AcmeUser).CreateAsync(SharedProject, AcmeUser, Draft("Acme plan"), CancellationToken.None);

        var acme = (await Repository(AcmeUser).ListAsync(SharedProject, null, 50, 0, CancellationToken.None)).Items.Select(item => item.Title).ToList();
        var globex = (await Repository(GlobexUser).ListAsync(SharedProject, null, 50, 0, CancellationToken.None)).Items.Select(item => item.Title).ToList();

        Assert.Equal(["Acme plan"], acme);
        Assert.Equal(["Globex secret"], globex);
    }

    [Fact]
    public async Task User_cannot_list_read_edit_or_link_another_orgs_decisions()
    {
        var repository = Repository(AcmeUser);
        var own = await repository.CreateAsync(SharedProject, AcmeUser, Draft("Acme plan"), CancellationToken.None);

        Assert.DoesNotContain((await repository.ListAsync(null, null, 50, 0, CancellationToken.None)).Items, item => item.Title == "Globex secret");
        Assert.Null(await repository.GetAsync(GlobexSlug, CancellationToken.None));
        Assert.Null(await repository.UpdateDraftAsync(GlobexSlug, AcmeUser, new UpdateDecisionDraftRequest(
            "hijacked", null, null, null, null, null, null, null, null, null, null), CancellationToken.None));
        Assert.Null(await repository.UpdateConnectionsAsync(own.Slug, AcmeUser, false, new DecisionConnectionsUpdateRequest([500], null), CancellationToken.None));
        await Execute($"UPDATE public.core_customuser SET is_superuser = true WHERE id = {AcmeUser}");
        var superuser = await Access(AcmeUser).AuthorizeAsync(
            Request(AcmeUser), SharedProject, DecisionAccessAction.Edit, CancellationToken.None);
        Assert.True(superuser is { Allowed: true, IsSuperuser: true });
        Assert.Null(await repository.UpdateConnectionsAsync(
            own.Slug, AcmeUser, superuser!.IsSuperuser, new DecisionConnectionsUpdateRequest([500], null), CancellationToken.None));
        Assert.Equal(0, await Scalar("SELECT count(*) FROM org_acme.decision_edges"));
        Assert.Equal(0, await Scalar("SELECT count(*) FROM org_globex.decision_edges"));
        Assert.Equal("Globex secret", await Scalar<string>($"SELECT title FROM org_globex.decisions WHERE slug = '{GlobexSlug}'"));
    }

    [Theory]
    [InlineData(false, 103, StatusCodes.Status401Unauthorized, true, false)]
    [InlineData(true, 103, StatusCodes.Status403Forbidden, true, false)]
    [InlineData(true, 104, StatusCodes.Status403Forbidden, true, false)]
    [InlineData(true, null, StatusCodes.Status403Forbidden, true, false)]
    [InlineData(true, 103, StatusCodes.Status403Forbidden, false, false)]
    [InlineData(true, 103, StatusCodes.Status403Forbidden, true, true)]
    public async Task Connections_require_source_project_edit_access(
        bool authenticated, int? targetId, int expectedStatus, bool sourceActive, bool sourceDeleted)
    {
        // Anonymous requests resolve public; a signed-in user resolves org_acme.
        var schema = authenticated ? "org_acme" : "public";
        await SeedConnectionsAsync(schema);
        if (!sourceActive || sourceDeleted)
        {
            await Execute($"""
                UPDATE org_acme.core_projectmember
                SET role = 'owner', is_active = {sourceActive}, is_deleted = {sourceDeleted}
                WHERE user_id = {AcmeUser} AND project_id = 20
                """);
        }

        await AssertConnectionUpdateRejectedAsync(schema, authenticated, "source",
            new DecisionConnectionsUpdateRequest(targetId is int id ? [id] : [], null), expectedStatus);
    }

    [Theory]
    [InlineData(true, 1, false)]
    [InlineData(false, 2, false)]
    [InlineData(false, 2, true)]
    public async Task Connections_reject_deleted_or_other_organization_targets_in_same_schema(
        bool targetDeleted, int targetOrganization, bool isSuperuser)
    {
        await SeedConnectionsAsync("org_acme");
        await Execute($"""
            UPDATE org_acme.core_projectmember SET role = 'owner' WHERE user_id = {AcmeUser} AND project_id = 20;
            UPDATE org_acme.core_project SET is_deleted = {targetDeleted}, organization_id = {targetOrganization} WHERE id = 21;
            UPDATE public.core_customuser SET is_superuser = {isSuperuser} WHERE id = {AcmeUser};
            """);

        await AssertConnectionUpdateRejectedAsync("org_acme", true, "source",
            new DecisionConnectionsUpdateRequest([104], null), StatusCodes.Status400BadRequest);
    }

    [Fact]
    public async Task Connections_reject_membership_revoked_after_source_authorization()
    {
        await SeedConnectionsAsync("org_acme");
        await Execute($"UPDATE org_acme.core_projectmember SET role = 'owner' WHERE user_id = {AcmeUser} AND project_id = 20");

        await AssertConnectionUpdateRejectedAsync("org_acme", true, "source",
            new DecisionConnectionsUpdateRequest([104], null), StatusCodes.Status400BadRequest,
            afterAuthorization: () => Execute($"""
                UPDATE org_acme.core_projectmember SET is_active = false
                WHERE user_id = {AcmeUser} AND project_id = 21
                """));
    }

    [Theory]
    [InlineData("stale-source")]
    [InlineData("2147483647")]
    public async Task Connections_return_not_found_for_unknown_source_lookup(string lookup)
    {
        await SeedConnectionsAsync("org_acme");

        await AssertConnectionUpdateRejectedAsync("org_acme", true, lookup,
            new DecisionConnectionsUpdateRequest([103], null), StatusCodes.Status404NotFound);
    }

    private Task SeedConnectionsAsync(string schema) => Execute($"""
        INSERT INTO {schema}.core_project (id, name, organization_id)
        VALUES (20, 'Source project', 1), (21, 'Target project', 1);
        INSERT INTO {schema}.core_projectmember (user_id, project_id, role)
        VALUES ({AcmeUser}, 20, 'viewer'), ({AcmeUser}, 21, 'owner');
        INSERT INTO {schema}.decisions (id, slug, title, status, project_id, project_seq)
        VALUES (101, 'source', 'Source', 'DRAFT', 20, 1),
               (102, 'original', 'Original connection', 'DRAFT', 20, 2),
               (103, 'local-target', 'Same project target', 'DRAFT', 20, 3),
               (104, 'remote-target', 'Other project target', 'DRAFT', 21, 1);
        INSERT INTO {schema}.decision_edges
            (created_at, updated_at, edge_type, from_decision_id, to_decision_id, created_by_id)
        VALUES (now(), now(), 'RELATED', 101, 102, {AcmeUser});
        """);

    private async Task AssertConnectionUpdateRejectedAsync(
        string schema, bool authenticated, string lookup, DecisionConnectionsUpdateRequest request,
        int expectedStatus, Func<Task>? afterAuthorization = null)
    {
        var context = authenticated ? Request(AcmeUser).HttpContext : new DefaultHttpContext();
        var validator = Validator();
        var connections = new TenantConnectionFactory(
            dataSource, validator, new HttpContextAccessor { HttpContext = context });
        IDecisionAccessService access = new JwtDecisionAccessService(connections, validator);
        if (afterAuthorization is not null)
        {
            access = new AfterAuthorization(access, afterAuthorization);
        }
        var controller = new DecisionsController(new PostgresDecisionRepository(connections), access)
        {
            ControllerContext = new ControllerContext { HttpContext = context },
        };
        var edgeSnapshot = $"SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id)::text FROM {schema}.decision_edges e";
        var before = await Scalar<string>(edgeSnapshot);
        Assert.NotNull(before);

        var result = await controller.UpdateConnections(lookup, request, CancellationToken.None);

        Assert.Equal(expectedStatus, Assert.IsAssignableFrom<ObjectResult>(result.Result).StatusCode);
        Assert.Equal(before, await Scalar<string>(edgeSnapshot));
    }

    private sealed class AfterAuthorization(IDecisionAccessService access, Func<Task> callback) : IDecisionAccessService
    {
        public async Task<DecisionAccessContext?> AuthorizeAsync(
            HttpRequest request, int? projectId, DecisionAccessAction action, CancellationToken cancellationToken)
        {
            var result = await access.AuthorizeAsync(request, projectId, action, cancellationToken);
            Assert.True(result is { Allowed: true });
            await callback();
            return result;
        }
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
        context.Request.Headers.Authorization =
            "Bearer " + JwtTestTokenFactory.CreateValidToken(userId);
        return context.Request;
    }

    private static DjangoJwtValidator Validator() =>
        new(Options.Create(new DjangoJwtOptions
        {
            SigningKey = SigningKey
        }));

    private static CreateDecisionDraftRequest Draft(string title) =>
        new(title, null, null, null, null, null, false, null, null, null, null);

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
