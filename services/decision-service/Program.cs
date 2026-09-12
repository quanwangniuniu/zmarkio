using DecisionService.Decisions;
using Npgsql;
using System.Text.Json.Serialization;

var builder = WebApplication.CreateBuilder(args);

builder.Services
    .AddControllers()
    .AddJsonOptions(options =>
    {
        options.JsonSerializerOptions.Converters.Add(new JsonStringEnumConverter());
    });
builder.Services.AddHealthChecks();
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

    builder.Services.AddSingleton(NpgsqlDataSource.Create(connectionString));
    builder.Services.AddScoped<IDecisionRepository, PostgresDecisionRepository>();
}
else
{
    builder.Services.AddSingleton<IDecisionRepository, InMemoryDecisionRepository>();
}

var app = builder.Build();

app.MapHealthChecks("/health");
app.MapControllers();

app.Run();

public partial class Program;
