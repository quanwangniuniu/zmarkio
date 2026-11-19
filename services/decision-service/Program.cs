using DecisionService.Decisions;
using Microsoft.AspNetCore.Diagnostics.HealthChecks;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Npgsql;
using System.Text.Json.Serialization;

var builder = WebApplication.CreateBuilder(args);

builder.Services
    .AddControllers()
    .AddJsonOptions(options =>
    {
        options.JsonSerializerOptions.Converters.Add(new JsonStringEnumConverter());
    });
var healthChecks = builder.Services.AddHealthChecks();

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
app.MapControllers();

app.Run();

public partial class Program;
