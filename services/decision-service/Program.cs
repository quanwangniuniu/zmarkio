using DecisionService.Decisions;
using Microsoft.AspNetCore.Diagnostics.HealthChecks;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Npgsql;
using System.Text.Json.Serialization;
using System.Threading.RateLimiting;

var builder = WebApplication.CreateBuilder(args);
// MED-553: reject request bodies over 256 KB with 413 before they reach the controllers.
builder.WebHost.ConfigureKestrel(options => options.Limits.MaxRequestBodySize = 256 * 1024);

builder.Services
    .AddControllers()
    .AddJsonOptions(options =>
    {
        options.JsonSerializerOptions.Converters.Add(new JsonStringEnumConverter());
    });
var healthChecks = builder.Services.AddHealthChecks();

// MED-553: per-user token buckets for the Decision API (writes stricter than reads).
// /health and /ready are not limited, so a flood cannot make the service look down.
builder.Services.AddRateLimiter(options =>
{
    options.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
    options.OnRejected = (context, _) =>
    {
        if (context.Lease.TryGetMetadata(MetadataName.RetryAfter, out var retryAfter))
        {
            context.HttpContext.Response.Headers.RetryAfter = ((int)Math.Ceiling(retryAfter.TotalSeconds)).ToString();
        }
        return ValueTask.CompletedTask;
    };
    options.GlobalLimiter = PartitionedRateLimiter.Create<HttpContext, string>(context =>
    {
        if (!context.Request.Path.StartsWithSegments("/api/decisions"))
        {
            return RateLimitPartition.GetNoLimiter("not-limited");
        }
        var token = context.Request.BearerToken();
        var userId = token is null
            ? null
            : context.RequestServices.GetRequiredService<DjangoJwtValidator>().Validate(token)?.UserId;
        var isRead = HttpMethods.IsGet(context.Request.Method);
        return RateLimitPartition.GetTokenBucketLimiter(
            $"{(isRead ? "read" : "write")}:{userId?.ToString() ?? "anonymous"}",
            _ => new TokenBucketRateLimiterOptions
            {
                TokenLimit = isRead ? 100 : 20,
                TokensPerPeriod = isRead ? 20 : 5,
                ReplenishmentPeriod = TimeSpan.FromSeconds(1),
            });
    });
});

builder.Services.AddOptions<DjangoJwtOptions>()
    .Configure(options =>
    {
        options.SigningKey =
            builder.Configuration["DJANGO_JWT_SIGNING_KEY"]
            ?? builder.Configuration["SECRET_KEY"];
    })
    .Validate(
        options => !string.IsNullOrWhiteSpace(options.SigningKey),
        "Django JWT signing key or secret key is not configured."
    )
    .ValidateOnStart();

builder.Services.AddSingleton<DjangoJwtValidator>();
builder.Services.AddScoped<IDecisionAccessService, JwtDecisionAccessService>();

var storage = builder.Configuration["DECISION_SERVICE_STORAGE"] ?? "in-memory";
if (string.Equals(storage, "postgres", StringComparison.OrdinalIgnoreCase))
{
    var connectionString = builder.Configuration.GetConnectionString("DecisionDb")
        ?? builder.Configuration["DECISION_DB_CONNECTION_STRING"];
    if (string.IsNullOrWhiteSpace(connectionString))
    {
        throw new InvalidOperationException("Decision service Postgres storage requires ConnectionStrings:DecisionDb.");
    }

    var dataSource = NpgsqlDataSource.Create(connectionString);
    builder.Services.AddSingleton(dataSource);
    healthChecks.AddAsyncCheck("postgres", async ct =>
    {
        await using var command = dataSource.CreateCommand("SELECT 1");
        await command.ExecuteScalarAsync(ct);
        return HealthCheckResult.Healthy();
    }, tags: ["ready"], timeout: TimeSpan.FromSeconds(3));
    builder.Services.AddHttpContextAccessor();
    builder.Services.AddScoped<TenantConnectionFactory>();
    builder.Services.AddScoped<IDecisionRepository, PostgresDecisionRepository>();
}
else
{
    builder.Services.AddSingleton<IDecisionRepository, InMemoryDecisionRepository>();
}

var app = builder.Build();

app.MapHealthChecks("/health", new HealthCheckOptions { Predicate = _ => false });
app.MapHealthChecks("/ready", new HealthCheckOptions { Predicate = check => check.Tags.Contains("ready") });
app.UseRateLimiter();
app.MapControllers();

app.Run();

public partial class Program;
