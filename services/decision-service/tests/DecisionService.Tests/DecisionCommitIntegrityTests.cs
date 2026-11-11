using DecisionService.Decisions;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Options;
using Npgsql;
using System.Text.Json;
using Testcontainers.PostgreSql;
using Xunit;


namespace DecisionService.Tests;

public sealed class DecisionCommitIntegrityTests : IAsyncLifetime
{
    private const string SigningKey = "test-signing-key";
    private const int TestUser = 1;
    private const int TestProject = 10;

    private readonly PostgreSqlContainer postgres =
        new PostgreSqlBuilder("postgres:16-alpine").Build();

    private NpgsqlDataSource dataSource = null!;

    public async Task InitializeAsync()
    {
        await postgres.StartAsync();
        dataSource = NpgsqlDataSource.Create(postgres.GetConnectionString());

        await Execute("""
            CREATE TABLE public.core_organization (
                id bigint PRIMARY KEY,
                slug varchar NOT NULL
            );

            CREATE TABLE public.core_customuser (
                id bigint PRIMARY KEY,
                email varchar NOT NULL,
                is_superuser boolean NOT NULL DEFAULT false,
                is_active boolean NOT NULL DEFAULT true,
                auth_token_version integer NOT NULL DEFAULT 0,
                organization_id bigint,
                current_organization_id bigint
            );

            INSERT INTO public.core_organization
            VALUES (1, 'acme');

            INSERT INTO public.core_customuser (
                id, email, organization_id, current_organization_id
            )
            VALUES (1, 'tester@acme.test', 1, NULL);
            """);

        await Execute("""
            CREATE SCHEMA org_acme;
            """);

        await Execute(Tables("org_acme"));

        await Execute("""
            INSERT INTO org_acme.core_project (
                id, name, organization_id
            )
            VALUES (10, 'Test Project', 1);

            INSERT INTO org_acme.core_projectmember (
                user_id, project_id, role
            )
            VALUES (1, 10, 'owner');
            """);
    }

    public async Task DisposeAsync()
    {
        await dataSource.DisposeAsync();
        await postgres.DisposeAsync();
    }

    [Theory]
    [InlineData(DecisionStatus.COMMITTED)]
    [InlineData(DecisionStatus.REVIEWED)]
    [InlineData(DecisionStatus.ARCHIVED)]
    public void Protected_status_cannot_replace_options_or_signals(
        DecisionStatus status)
    {
        var repository = Repository(TestUser);

        var decision = repository.Create(
            TestProject,
            TestUser,
            Draft("Test protected status")
        );

        repository.UpdateDraft(
            decision.Slug,
            TestUser,
            new UpdateDecisionDraftRequest(
                null, null, null, null, null, null, null, null,
                new[]
                {
                    new DecisionSignalInput(
                        "CTR", "increased", "last 7 days",
                        "vs_previous_period", "campaign", "all",
                        12.5m, "%", null)
                },
                new[]
                {
                    new DecisionOptionInput("Original option", true, 1)
                },
                null
            )
        );

        var committed = repository.Transition(
            decision.Slug,
            TestUser,
            DecisionStatus.COMMITTED
        );

        Assert.NotNull(committed);

        if (status != DecisionStatus.COMMITTED)
        {
            var transitioned = repository.Transition(
                decision.Slug,
                TestUser,
                status
            );

            Assert.NotNull(transitioned);
        }

        Assert.Throws<DecisionValidationException>(() =>
            repository.UpdateDraft(
                decision.Slug,
                TestUser,
                new UpdateDecisionDraftRequest(
                    null, null, null, null, null, null, null, null,
                    null,
                    new[]
                    {
                        new DecisionOptionInput("Changed option", true, 1)
                    },
                    null
                )
            )
        );

        Assert.Throws<DecisionValidationException>(() =>
            repository.UpdateDraft(
                decision.Slug,
                TestUser,
                new UpdateDecisionDraftRequest(
                    null, null, null, null, null, null, null, null,
                    new[]
                    {
                        new DecisionSignalInput(
                            "Revenue", "increased", "last 30 days",
                            "vs_previous_period", "campaign", "all",
                            20m, "%", null)
                    },
                    null,
                    null
                )
            )
        );

        var updated = repository.Get(decision.Slug);

        Assert.NotNull(updated);
        Assert.Equal(status, updated.Status);

        Assert.Equal(
            "Original option",
            Assert.Single(updated.Options).Text
        );

        Assert.Equal(
            "CTR",
            Assert.Single(updated.Signals).Metric
        );
    }

    [Fact]
    public async Task Committing_draft_creates_commit_record_with_snapshot()
    {
        var repository = Repository(TestUser);

        var decision = repository.Create(
            TestProject,
            TestUser,
            Draft("Test commit snapshot")
        );

        repository.UpdateDraft(
            decision.Slug,
            TestUser,
            new UpdateDecisionDraftRequest(
                null,
                "Decision context",
                DecisionRiskLevel.MEDIUM,
                80,
                "Decision reasoning",
                null, null, null,
                new[]
                {
                    new DecisionSignalInput(
                        "CTR", "increased", "last 7 days",
                        "vs_previous_period", "campaign", "all",
                        12.5m, "%", null)
                },
                new[]
                {
                    new DecisionOptionInput("Option A", true, 1),
                    new DecisionOptionInput("Option B", false, 2)
                },
                null
            )
        );

        var beforeCommit = repository.Get(decision.Slug);
        Assert.NotNull(beforeCommit);

        var selectedOptionId = Assert.Single(
            beforeCommit.Options, o => o.IsSelected
        ).Id;

        var committed = repository.Transition(
            decision.Slug,
            TestUser,
            DecisionStatus.COMMITTED
        );

        Assert.NotNull(committed);
        Assert.Equal(DecisionStatus.COMMITTED, committed.Status);

        var recordCount = await Scalar<long>($"""
            SELECT COUNT(*)
            FROM org_acme.decision_commit_records
            WHERE decision_id = {decision.Id}
            """);

        Assert.Equal(1L, recordCount);

        var snapshotJson = await Scalar<string>($"""
            SELECT validation_snapshot::text
            FROM org_acme.decision_commit_records
            WHERE decision_id = {decision.Id}
            """);

        using var document = JsonDocument.Parse(snapshotJson);
        var snapshot = document.RootElement;

        Assert.True(
            snapshot.GetProperty("required_fields_present").GetBoolean()
        );

        Assert.Equal(
            selectedOptionId,
            snapshot.GetProperty("selected_option_id").GetInt32()
        );

        Assert.Equal(
            "MEDIUM",
            snapshot.GetProperty("risk_level").GetString()
        );

        Assert.Equal(
            80,
            snapshot.GetProperty("confidence").GetInt32()
        );

        Assert.True(
            snapshot.GetProperty("context_summary_present").GetBoolean()
        );

        Assert.True(
            snapshot.GetProperty("reasoning_present").GetBoolean()
        );

        Assert.Equal(
            1,
            snapshot.GetProperty("signals_count").GetInt32()
        );

        Assert.Equal(
            2,
            snapshot.GetProperty("options_count").GetInt32()
        );

        Assert.Equal(
            1,
            snapshot.GetProperty("selected_options_count").GetInt32()
        );

        Assert.True(
            DateTimeOffset.TryParse(
                snapshot.GetProperty("timestamp").GetString(),
                out _
            )
        );
    }

    [Fact]
    public async Task Failed_commit_record_insert_rolls_back_transition()
    {
        var repository = Repository(TestUser);

        var decision = repository.Create(
            TestProject,
            TestUser,
            Draft("Test transaction rollback")
        );

        await Execute("""
            ALTER TABLE org_acme.decision_commit_records
            ADD CONSTRAINT reject_snapshot_for_test
            CHECK (validation_snapshot IS NULL);
            """);

        var exception = Assert.Throws<PostgresException>(() =>
            repository.Transition(
                decision.Slug,
                TestUser,
                DecisionStatus.COMMITTED
            )
        );

        Assert.Equal("23514", exception.SqlState);
        Assert.Equal("reject_snapshot_for_test", exception.ConstraintName);

        var status = await Scalar<string>($"""
            SELECT status
            FROM org_acme.decisions
            WHERE id = {decision.Id}
            """);

        Assert.Equal("DRAFT", status);

        var committedAtIsNull = await Scalar<bool>($"""
            SELECT committed_at IS NULL
            FROM org_acme.decisions
            WHERE id = {decision.Id}
            """);

        Assert.True(committedAtIsNull);

        var commitRecordCount = await Scalar<long>($"""
            SELECT COUNT(*)
            FROM org_acme.decision_commit_records
            WHERE decision_id = {decision.Id}
            """);

        Assert.Equal(0L, commitRecordCount);

        var transitionCount = await Scalar<long>($"""
            SELECT COUNT(*)
            FROM org_acme.decision_state_transitions
            WHERE decision_id = {decision.Id}
            """);

        Assert.Equal(0L, transitionCount);
    }

    [Fact]
    public async Task Editing_allowed_fields_in_committed_decision_does_not_change_commit_snapshot()
    {
        var repository = Repository(TestUser);

        var decision = repository.Create(
            TestProject,
            TestUser,
            Draft("Test snapshot immutability")
        );

        var committed = repository.Transition(
            decision.Slug,
            TestUser,
            DecisionStatus.COMMITTED
        );

        Assert.NotNull(committed);
        Assert.Equal(DecisionStatus.COMMITTED, committed.Status);

        var originalSnapshot = await Scalar<string>($"""
            SELECT validation_snapshot::text
            FROM org_acme.decision_commit_records
            WHERE decision_id = {decision.Id}
            """);

        var updated = repository.UpdateDraft(
            decision.Slug,
            TestUser,
            new UpdateDecisionDraftRequest(
                null, null, null, null, null,
                "strategy", 
                true, 
                null, null, null, null
            )
        );

        Assert.NotNull(updated);
        Assert.Equal("strategy", updated.Topic);
        Assert.True(updated.IsReferenceCase);

        Assert.Equal(DecisionStatus.COMMITTED, updated.Status);

        var snapshotAfterEdit = await Scalar<string>($"""
            SELECT validation_snapshot::text
            FROM org_acme.decision_commit_records
            WHERE decision_id = {decision.Id}
            """);

        Assert.Equal(originalSnapshot, snapshotAfterEdit);

        var recordCount = await Scalar<long>($"""
            SELECT COUNT(*)
            FROM org_acme.decision_commit_records
            WHERE decision_id = {decision.Id}
            """);

        Assert.Equal(1L, recordCount);
    }

    [Fact]
    public async Task Patching_committed_protected_field_returns_400()
    {
        var repository = Repository(TestUser);

        var decision = repository.Create(
            TestProject,
            TestUser,
            Draft("Original title")
        );

        var committed = repository.Transition(
            decision.Slug,
            TestUser,
            DecisionStatus.COMMITTED
        );

        Assert.NotNull(committed);

        var controller = new DecisionsController(
            repository,
            new JwtDecisionAccessService(
                Connections(TestUser),
                Validator()
            )
        );

        controller.ControllerContext = new ControllerContext
        {
            HttpContext = Request(TestUser).HttpContext
        };

        var request = new UpdateDecisionDraftRequest(
            "Changed title",
            null, null, null, null, null, null, null,
            null, null, null
        );

        var result = await controller.PatchCommitted(
            decision.Slug,
            request,
            CancellationToken.None
        );

        var badRequest = Assert.IsType<BadRequestObjectResult>(
            result.Result
        );

        Assert.Equal(400, badRequest.StatusCode);

        var errors = Assert.IsType<Dictionary<string, string[]>>(
            badRequest.Value
        );

        Assert.Equal(
            "Cannot modify title because this decision is COMMITTED.",
            Assert.Single(errors["title"])
        );

        var unchanged = repository.Get(decision.Slug);

        Assert.NotNull(unchanged);
        Assert.Equal("Original title", unchanged.Title);
        Assert.Equal(DecisionStatus.COMMITTED, unchanged.Status);
    }

    private PostgresDecisionRepository Repository(int userId) => new(Connections(userId));
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
    
    private JwtDecisionAccessService Access(int userId) => new(Connections(userId), Validator());

    private static CreateDecisionDraftRequest Draft(string title) =>
        new(title, null, null, null, null, null, false, false, null, null, null, null, null);

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
        CREATE TABLE {s}.decision_state_transitions (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            is_deleted boolean NOT NULL,
            from_status varchar(20) NOT NULL,
            to_status varchar(20) NOT NULL,
            transition_method varchar(50) NOT NULL,
            metadata jsonb,
            "timestamp" timestamptz NOT NULL,
            note text,
            decision_id bigint NOT NULL,
            triggered_by_id bigint
        );
        CREATE TABLE {s}.decision_commit_records (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            is_deleted boolean NOT NULL,
            committed_at timestamptz NOT NULL,
            validation_snapshot jsonb,
            committed_by_id bigint,
            decision_id bigint NOT NULL UNIQUE
        );
        """;
}