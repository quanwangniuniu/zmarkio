using System.Net;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using DecisionService.Decisions;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.DependencyInjection.Extensions;
using Xunit;

namespace DecisionService.Tests;

public sealed class DraftOwnershipTests : IDisposable
{
    private const int Owner = 1;
    private const int OtherEditor = 2;
    private const int Approver = 3;

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        Converters = { new JsonStringEnumConverter() },
    };

    private static readonly object Signal = new { metric = "CTR", movement = "DOWN", period = "LAST_7_DAYS" };

    private readonly DraftOwnershipFactory factory = new();

    public void Dispose() => factory.Dispose();

    public static TheoryData<string> OwnerOnlyPayloads => new()
    {
        """{"signals":[{"metric":"CPC","movement":"UP","period":"LAST_7_DAYS"}]}""",
        """{"options":[{"text":"Hijacked","isSelected":true,"order":0}]}""",
        """{"reasoning":"Rewritten by someone else"}""",
        """{"parentDecisionIds":[]}""",
    };

    [Theory]
    [MemberData(nameof(OwnerOnlyPayloads))]
    public async Task Patch_rejects_owner_only_fields_from_another_editor(string payload)
    {
        var draft = await CreateDraft();
        using var client = Client(OtherEditor, "Copywriter");

        var draftPatch = await client.PatchAsync($"/api/decisions/drafts/{draft.Slug}", Json(payload));
        var decisionPatch = await client.PatchAsync($"/api/decisions/{draft.Slug}", Json(payload));

        Assert.Equal(HttpStatusCode.Forbidden, draftPatch.StatusCode);
        Assert.Equal(HttpStatusCode.Forbidden, decisionPatch.StatusCode);
        await AssertUnchanged(draft);
    }

    [Fact]
    public async Task Signal_endpoints_reject_another_editor()
    {
        var draft = await CreateDraft();
        var signalId = Assert.Single(draft.Signals).Id;
        using var client = Client(OtherEditor, "Copywriter");

        var create = await client.PostAsJsonAsync($"/api/decisions/{draft.Slug}/signals", Signal);
        var update = await client.PatchAsJsonAsync($"/api/decisions/{draft.Slug}/signals/{signalId}", Signal);
        var delete = await client.DeleteAsync($"/api/decisions/{draft.Slug}/signals/{signalId}");

        Assert.Equal(HttpStatusCode.Forbidden, create.StatusCode);
        Assert.Equal(HttpStatusCode.Forbidden, update.StatusCode);
        Assert.Equal(HttpStatusCode.Forbidden, delete.StatusCode);
        await AssertUnchanged(draft);
    }

    [Fact]
    public async Task Connections_update_rejects_another_editor()
    {
        var draft = await CreateDraft();
        var connections = new { connectedDecisionIds = Array.Empty<int>() };
        using var otherEditor = Client(OtherEditor, "Copywriter");
        using var owner = Client(Owner, "Copywriter");

        var rejected = await otherEditor.PutAsJsonAsync($"/api/decisions/{draft.Slug}/connections", connections);
        var accepted = await owner.PutAsJsonAsync($"/api/decisions/{draft.Slug}/connections", connections);

        Assert.Equal(HttpStatusCode.Forbidden, rejected.StatusCode);
        Assert.Equal(HttpStatusCode.OK, accepted.StatusCode);
    }

    [Fact]
    public async Task Another_editor_can_still_change_shared_fields()
    {
        var draft = await CreateDraft();
        using var client = Client(OtherEditor, "Copywriter");

        var response = await client.PatchAsync(
            $"/api/decisions/drafts/{draft.Slug}",
            Json("""{"topic":"pricing","reasoning":"Original reasoning"}"""));

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var updated = await response.Content.ReadFromJsonAsync<DecisionDetailDto>(JsonOptions);
        Assert.Equal("pricing", updated!.Topic);
    }

    [Theory]
    [InlineData(Owner, "Copywriter")]
    [InlineData(Approver, "Approver")]
    public async Task Owner_and_approver_can_change_owner_only_fields(int userId, string role)
    {
        var draft = await CreateDraft();
        using var client = Client(userId, role);

        var draftPatch = await client.PatchAsync(
            $"/api/decisions/drafts/{draft.Slug}",
            Json("""{"options":[{"text":"Revised","isSelected":true,"order":0}],"reasoning":"Revised reasoning"}"""));
        var decisionPatch = await client.PatchAsync(
            $"/api/decisions/{draft.Slug}",
            Json("""{"signals":[{"metric":"CPC","movement":"UP","period":"LAST_7_DAYS"}]}"""));
        var signalCreate = await client.PostAsJsonAsync($"/api/decisions/{draft.Slug}/signals", Signal);

        Assert.Equal(HttpStatusCode.OK, draftPatch.StatusCode);
        Assert.Equal(HttpStatusCode.OK, decisionPatch.StatusCode);
        Assert.Equal(HttpStatusCode.Created, signalCreate.StatusCode);
        var current = await Get(draft.Slug);
        Assert.Equal("Revised", Assert.Single(current.Options).Text);
        Assert.Equal("Revised reasoning", current.Reasoning);
        Assert.Equal(2, current.Signals.Count);
    }

    private async Task<DecisionDetailDto> CreateDraft()
    {
        using var client = Client(Owner, "Copywriter");
        var response = await client.PostAsync("/api/decisions/drafts", Json(
            """
            {
              "title": "Owner draft",
              "reasoning": "Original reasoning",
              "signals": [{"metric":"CTR","movement":"DOWN","period":"LAST_7_DAYS"}],
              "options": [{"text":"Original","isSelected":true,"order":0}]
            }
            """));
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        return (await response.Content.ReadFromJsonAsync<DecisionDetailDto>(JsonOptions))!;
    }

    private async Task<DecisionDetailDto> Get(string slug)
    {
        using var client = Client(Owner, "Copywriter");
        return (await client.GetFromJsonAsync<DecisionDetailDto>($"/api/decisions/drafts/{slug}", JsonOptions))!;
    }

    private async Task AssertUnchanged(DecisionDetailDto draft)
    {
        var current = await Get(draft.Slug);
        Assert.Equal("Original reasoning", current.Reasoning);
        Assert.Equal("Original", Assert.Single(current.Options).Text);
        Assert.Equal("CTR", Assert.Single(current.Signals).Metric);
    }

    private HttpClient Client(int userId, string role)
    {
        var client = factory.CreateClient();
        client.DefaultRequestHeaders.Add("x-project-id", "12");
        client.DefaultRequestHeaders.Add("x-test-user", userId.ToString());
        client.DefaultRequestHeaders.Add("x-test-role", role);
        return client;
    }

    private static StringContent Json(string payload) => new(payload, System.Text.Encoding.UTF8, "application/json");

    private sealed class DraftOwnershipFactory : WebApplicationFactory<Program>
    {
        protected override void ConfigureWebHost(IWebHostBuilder builder)
        {
            builder.ConfigureAppConfiguration((_, config) =>
            {
                config.AddInMemoryCollection(new Dictionary<string, string?>
                {
                    ["DJANGO_JWT_SIGNING_KEY"] = "test-signing-key",
                });
            });

            builder.ConfigureServices(services =>
            {
                services.RemoveAll<IDecisionRepository>();
                services.RemoveAll<IDecisionAccessService>();
                services.AddSingleton<IDecisionRepository, InMemoryDecisionRepository>();
                services.AddSingleton<IDecisionAccessService, HeaderAccessService>();
            });
        }
    }

    private sealed class HeaderAccessService : IDecisionAccessService
    {
        public Task<DecisionAccessContext?> AuthorizeAsync(
            HttpRequest request,
            int? projectId,
            DecisionAccessAction action,
            CancellationToken cancellationToken
        )
        {
            var role = request.Headers["x-test-role"].ToString();
            return Task.FromResult<DecisionAccessContext?>(new DecisionAccessContext(
                DecisionPermissionPolicy.IsAllowed(role, action),
                int.Parse(request.Headers["x-test-user"].ToString()),
                null,
                false,
                projectId,
                7,
                role,
                DecisionPermissionPolicy.RoleLevel(role)
            ));
        }
    }
}
