using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using DecisionService.Decisions;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
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

    private sealed class DecisionApiFactory(bool authenticated) : WebApplicationFactory<Program>
    {
        protected override void ConfigureWebHost(IWebHostBuilder builder)
        {
            builder.ConfigureServices(services =>
            {
                services.RemoveAll<IDecisionRepository>();
                services.RemoveAll<IDecisionAccessService>();
                services.AddSingleton<IDecisionRepository, InMemoryDecisionRepository>();
                services.AddSingleton<IDecisionAccessService>(new TestDecisionAccessService(authenticated));
            });
        }
    }

    private sealed class TestDecisionAccessService(bool authenticated) : IDecisionAccessService
    {
        public Task<DecisionAccessContext?> AuthorizeAsync(
            HttpRequest request,
            int? projectId,
            DecisionAccessAction action,
            CancellationToken cancellationToken
        )
        {
            if (!authenticated || string.IsNullOrWhiteSpace(request.Headers.Authorization))
            {
                return Task.FromResult<DecisionAccessContext?>(null);
            }

            return Task.FromResult<DecisionAccessContext?>(new DecisionAccessContext(
                true,
                42,
                "tester@example.com",
                false,
                projectId,
                7,
                "Organization Admin",
                2
            ));
        }
    }
}
