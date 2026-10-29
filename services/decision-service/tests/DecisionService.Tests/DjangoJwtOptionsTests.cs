using DecisionService.Decisions;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Options;
using Xunit;

namespace DecisionService.Tests;

public sealed class DjangoJwtOptionsTests
{
    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    public async Task Startup_rejects_missing_signing_key(string? signingKey)
    {
        using var host = new HostBuilder()
            .ConfigureServices(services =>
            {
                services.AddOptions<DjangoJwtOptions>()
                    .Configure(options => options.SigningKey = signingKey)
                    .Validate(
                        options => !string.IsNullOrWhiteSpace(options.SigningKey),
                        "Django JWT signing key or secret key is not configured."
                    )
                    .ValidateOnStart();
            })
            .Build();

        await Assert.ThrowsAsync<OptionsValidationException>(
            () => host.StartAsync());
    }
}