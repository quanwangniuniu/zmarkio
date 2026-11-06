using System.Net;
using System.Net.Http.Json;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using DecisionService.Decisions;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Xunit;

namespace DecisionService.Tests;

public sealed class DecisionApiTests
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        Converters = { new JsonStringEnumConverter() },
    };

    [Fact]
    public async Task List_requires_authentication()
    {
        await using var factory = new DecisionApiFactory(authenticated: false);
        using var client = factory.CreateClient();

        var response = await client.GetAsync("/api/decisions?project_id=1");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Draft_lookup_requires_authentication_regardless_of_whether_decision_exists()
    {
        await using var factory = new DecisionApiFactory(authenticated: true);
        using var client = factory.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", "test-token");
        client.DefaultRequestHeaders.Add("x-project-id", "12");

        var createResponse = await client.PostAsJsonAsync("/api/decisions/drafts", new CreateDecisionDraftRequest(
            "Authentication test decision",
            null,
            null,
            null,
            null,
            "general",
            false,
            false,
            null,
            null,
            null,
            null,
            null
        ));

        Assert.Equal(HttpStatusCode.Created, createResponse.StatusCode);

        var decision = await createResponse.Content.ReadFromJsonAsync<DecisionDetailDto>(JsonOptions);
        Assert.NotNull(decision);

        client.DefaultRequestHeaders.Authorization = null;

        var getCallsBeforeUnauthenticatedLookups = factory.RepositoryGetCallCount;

        var existingResponse = await client.GetAsync($"/api/decisions/drafts/{decision!.Slug}");
        var missingResponse = await client.GetAsync("/api/decisions/drafts/decision-does-not-exist");

        Assert.Equal(HttpStatusCode.Unauthorized, existingResponse.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, missingResponse.StatusCode);
        Assert.Equal(getCallsBeforeUnauthenticatedLookups, factory.RepositoryGetCallCount);
    }

    [Fact]
    public async Task Draft_lookup_returns_404_when_authenticated_user_lacks_access()
    {
        await using var factory = new DecisionApiFactory(authenticated: true);
        using var client = factory.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", "test-token");
        client.DefaultRequestHeaders.Add("x-project-id", "12");

        var createResponse = await client.PostAsJsonAsync("/api/decisions/drafts", new CreateDecisionDraftRequest(
            "Authorization test decision",
            null,
            null,
            null,
            null,
            "general",
            false,
            false,
            null,
            null,
            null,
            null,
            null
        ));

        Assert.Equal(HttpStatusCode.Created, createResponse.StatusCode);

        var decision = await createResponse.Content.ReadFromJsonAsync<DecisionDetailDto>(JsonOptions);
        Assert.NotNull(decision);

        var allowedResponse = await client.GetAsync($"/api/decisions/drafts/{decision!.Slug}");
        Assert.Equal(HttpStatusCode.OK, allowedResponse.StatusCode);

        factory.SetAccessAllowed(false);

        var deniedResponse = await client.GetAsync($"/api/decisions/drafts/{decision.Slug}");
        Assert.Equal(HttpStatusCode.NotFound, deniedResponse.StatusCode);
    }

    [Fact]
    public async Task Decision_lookup_returns_404_when_authenticated_user_lacks_access()
    {
        await using var factory = new DecisionApiFactory(authenticated: true);
        using var client = factory.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", "test-token");
        client.DefaultRequestHeaders.Add("x-project-id", "12");

        var createResponse = await client.PostAsJsonAsync("/api/decisions/drafts", new CreateDecisionDraftRequest(
            "Decision lookup authorization test",
            null,
            null,
            null,
            null,
            "general",
            false,
            false,
            null,
            null,
            null,
            null,
            null
        ));

        Assert.Equal(HttpStatusCode.Created, createResponse.StatusCode);

        var decision = await createResponse.Content.ReadFromJsonAsync<DecisionDetailDto>(JsonOptions);
        Assert.NotNull(decision);

        factory.SetAccessAllowed(false);

        var detailResponse = await client.GetAsync($"/api/decisions/{decision!.Slug}");
        var signalsResponse = await client.GetAsync($"/api/decisions/{decision.Slug}/signals");

        Assert.Equal(HttpStatusCode.NotFound, detailResponse.StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, signalsResponse.StatusCode);
    }

    [Fact]
    public async Task Decision_lookup_requires_authentication_before_repository_lookup()
    {
        await using var factory = new DecisionApiFactory(authenticated: true);
        using var client = factory.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", "test-token");
        client.DefaultRequestHeaders.Add("x-project-id", "12");

        var createResponse = await client.PostAsJsonAsync("/api/decisions/drafts", new CreateDecisionDraftRequest(
            "Decision lookup authentication test",
            null,
            null,
            null,
            null,
            "general",
            false,
            false,
            null,
            null,
            null,
            null,
            null
        ));

        Assert.Equal(HttpStatusCode.Created, createResponse.StatusCode);

        var decision = await createResponse.Content.ReadFromJsonAsync<DecisionDetailDto>(JsonOptions);
        Assert.NotNull(decision);

        client.DefaultRequestHeaders.Authorization = null;

        var getCallsBeforeLookups = factory.RepositoryGetCallCount;

        var detailResponse = await client.GetAsync($"/api/decisions/{decision!.Slug}");
        var signalsResponse = await client.GetAsync($"/api/decisions/{decision.Slug}/signals");

        Assert.Equal(HttpStatusCode.Unauthorized, detailResponse.StatusCode);
        Assert.Equal(HttpStatusCode.Unauthorized, signalsResponse.StatusCode);
        Assert.Equal(getCallsBeforeLookups, factory.RepositoryGetCallCount);
    }

    [Fact]
    public async Task Draft_create_and_list_round_trip_through_http_api()
    {
        await using var factory = new DecisionApiFactory(authenticated: true);
        using var client = factory.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", "test-token");
        client.DefaultRequestHeaders.Add("x-project-id", "12");

        var createResponse = await client.PostAsJsonAsync("/api/decisions/drafts", new CreateDecisionDraftRequest(
            "Test decision",
            "Move decision API to .NET",
            DecisionRiskLevel.MEDIUM,
            80,
            "Keeps frontend behavior stable while moving ownership.",
            "general",
            false,
            false,
            null,
            null,
            new[] { new DecisionSignalInput("CTR", "increased", "last 7 days", "vs_previous_period", "campaign", "all", 12.5m, "%", null) },
            new[] { new DecisionOptionInput("Ship migration", true, 1) },
            Array.Empty<int>()
        ));
        Assert.Equal(HttpStatusCode.Created, createResponse.StatusCode);
        Assert.StartsWith("/api/decisions/drafts/", createResponse.Headers.Location?.ToString());

        var listResponse = await client.GetFromJsonAsync<DecisionListResponse>("/api/decisions?project_id=12", JsonOptions);

        Assert.NotNull(listResponse);
        var item = Assert.Single(listResponse!.Items);
        Assert.Equal("Test decision", item.Title);
        Assert.Equal(12, item.ProjectId);

        var graphResponse = await client.GetFromJsonAsync<DecisionGraphResponse>("/api/decisions/graph?project_id=12", JsonOptions);

        Assert.NotNull(graphResponse);
        var node = Assert.Single(graphResponse!.Nodes);
        Assert.Equal(item.Id, node.Id);
        Assert.Equal("Test decision", node.Title);
    }

    [Fact]
    public async Task Draft_create_returns_origin_meeting_when_origin_meeting_id_is_supplied()
    {
        await using var factory = new DecisionApiFactory(authenticated: true);
        using var client = factory.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", "test-token");
        client.DefaultRequestHeaders.Add("x-project-id", "12");

        var createResponse = await client.PostAsJsonAsync("/api/decisions/drafts", new Dictionary<string, object?>
        {
            ["title"] = "Decision from meeting",
            ["origin_meeting_id"] = 99,
        });

        Assert.Equal(HttpStatusCode.Created, createResponse.StatusCode);
        var decision = await createResponse.Content.ReadFromJsonAsync<DecisionDetailDto>(JsonOptions);
        Assert.NotNull(decision);
        Assert.NotNull(decision!.OriginMeeting);
        Assert.Equal(99, decision.OriginMeeting!.Id);
    }

    [Fact]
    public async Task Draft_patch_rejects_origin_meeting_id()
    {
        await using var factory = new DecisionApiFactory(authenticated: true);
        using var client = factory.CreateClient();
        client.DefaultRequestHeaders.Authorization = new("Bearer", "test-token");
        client.DefaultRequestHeaders.Add("x-project-id", "12");

        var createResponse = await client.PostAsJsonAsync("/api/decisions/drafts", new CreateDecisionDraftRequest(
            "Existing decision",
            null,
            null,
            null,
            null,
            "general",
            false,
            false,
            null,
            null,
            null,
            null,
            null
        ));
        var decision = await createResponse.Content.ReadFromJsonAsync<DecisionDetailDto>(JsonOptions);

        var patchResponse = await client.PatchAsJsonAsync($"/api/decisions/drafts/{decision!.Slug}", new Dictionary<string, object?>
        {
            ["title"] = "Updated",
            ["origin_meeting_id"] = 100,
        });

        Assert.Equal(HttpStatusCode.BadRequest, patchResponse.StatusCode);
    }

    [Fact]
    public async Task Decision_without_project_rejects_spoofed_project_context()
    {
        await using var factory = new DecisionApiFactory(authenticated: true);
        using var client = factory.CreateClient();

        client.DefaultRequestHeaders.Authorization = new("Bearer", "test-token");

        var slug = factory.CreateDecisionWithoutProject();

        client.DefaultRequestHeaders.Add("x-project-id", "12");

        var headerResponse = await client.GetAsync($"/api/decisions/{slug}");

        Assert.Equal(HttpStatusCode.NotFound, headerResponse.StatusCode);
        Assert.Null(factory.LastAuthorizedProjectId);

        client.DefaultRequestHeaders.Remove("x-project-id");

        var queryResponse = await client.GetAsync($"/api/decisions/{slug}?project_id=12");

        Assert.Equal(HttpStatusCode.NotFound, queryResponse.StatusCode);
        Assert.Null(factory.LastAuthorizedProjectId);
    }

    private sealed class DecisionApiFactory : WebApplicationFactory<Program>
    {
        private readonly TestDecisionAccessService _accessService;
        private readonly IDecisionRepository _repository;
        private readonly TrackingDecisionRepositoryProxy _repositoryProxy;

        public DecisionApiFactory(bool authenticated)
        {
            _accessService = new TestDecisionAccessService(authenticated);

            _repository = DispatchProxy.Create<IDecisionRepository, TrackingDecisionRepositoryProxy>();
            _repositoryProxy = (TrackingDecisionRepositoryProxy)_repository;
            _repositoryProxy.Inner = new InMemoryDecisionRepository();
        }

        public int RepositoryGetCallCount => _repositoryProxy.GetCallCount;

        public int? LastAuthorizedProjectId => _accessService.LastAuthorizedProjectId;

        public string CreateDecisionWithoutProject()
        {
            var repository = (InMemoryDecisionRepository)_repositoryProxy.Inner;
            var decision = repository.Create(
                null,
                42,
                new CreateDecisionDraftRequest(
                    "Decision without project",
                    null,
                    null,
                    null,
                    null,
                    null,
                    false,
                    false,
                    null,
                    null,
                    null,
                    null,
                    null
                )
            );

            return decision.Slug;
        }

        public void SetAccessAllowed(bool allowed)
        {
            _accessService.Allowed = allowed;
        }

        protected override void ConfigureWebHost(IWebHostBuilder builder)
        {
            builder.ConfigureAppConfiguration((context, config) =>
            {
                config.AddInMemoryCollection(
                    new Dictionary<string, string?>
                    {
                        ["DJANGO_JWT_SIGNING_KEY"] = "test-signing-key"
                    });
            });

            builder.ConfigureServices(services =>
            {
                services.RemoveAll<IDecisionRepository>();
                services.RemoveAll<IDecisionAccessService>();
                services.AddSingleton<IDecisionRepository>(_repository);
                services.AddSingleton<IDecisionAccessService>(_accessService);
            });
        }
    }

    public class TrackingDecisionRepositoryProxy : DispatchProxy
    {
        public IDecisionRepository Inner { get; set; } = null!;

        public int GetCallCount { get; private set; }

        protected override object? Invoke(MethodInfo? targetMethod, object?[]? args)
        {
            if (targetMethod is null)
            {
                throw new InvalidOperationException("Repository method was not provided.");
            }

            if (targetMethod.Name == nameof(IDecisionRepository.Get))
            {
                GetCallCount++;
            }

            return targetMethod.Invoke(Inner, args);
        }
    }

    private sealed class TestDecisionAccessService(bool authenticated) : IDecisionAccessService
    {
        public bool Allowed { get; set; } = true;

        public int? LastAuthorizedProjectId { get; private set; }

        public Task<DecisionAuthenticatedUser?> AuthenticateAsync(
            HttpRequest request,
            CancellationToken cancellationToken)
        {
            if (!authenticated || string.IsNullOrWhiteSpace(request.Headers.Authorization))
            {
                return Task.FromResult<DecisionAuthenticatedUser?>(null);
            }

            return Task.FromResult<DecisionAuthenticatedUser?>(
                new DecisionAuthenticatedUser(42, "tester@example.com", false));
        }

        public Task<DecisionAccessContext?> AuthorizeAsync(
            DecisionAuthenticatedUser user,
            int? projectId,
            DecisionAccessAction action,
            CancellationToken cancellationToken)
        {
            LastAuthorizedProjectId = projectId;

            var allowed = Allowed && (user.IsSuperuser || projectId.HasValue);

            return Task.FromResult<DecisionAccessContext?>(
                new DecisionAccessContext(
                    allowed,
                    user.UserId,
                    user.Email,
                    user.IsSuperuser,
                    projectId,
                    7,
                    "Organization Admin",
                    2));
        }

        public async Task<DecisionAccessContext?> AuthorizeAsync(
            HttpRequest request,
            int? projectId,
            DecisionAccessAction action,
            CancellationToken cancellationToken)
        {
            var user = await AuthenticateAsync(request, cancellationToken);

            if (user is null)
            {
                return null;
            }

            return await AuthorizeAsync(user, projectId, action, cancellationToken);
        }
    }
}
