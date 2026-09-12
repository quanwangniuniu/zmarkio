using DecisionService.Decisions;
using Microsoft.Extensions.Configuration;
using Xunit;

namespace DecisionService.Tests;

public sealed class DjangoJwtValidatorTests
{
    private const string SigningKey = "test-signing-key";

    [Fact]
    public void Validate_accepts_signed_access_token()
    {
        var token = DjangoJwtValidator.CreateSignedTokenForTests(SigningKey, userId: 42, authTokenVersion: 7);
        var principal = Validator().Validate(token);

        Assert.NotNull(principal);
        Assert.Equal(42, principal.UserId);
        Assert.Equal(7, principal.AuthTokenVersion);
    }

    [Fact]
    public void Validate_rejects_tampered_token()
    {
        var token = DjangoJwtValidator.CreateSignedTokenForTests(SigningKey, userId: 42);
        var parts = token.Split('.');
        var tampered = $"{parts[0]}.{parts[1]}x.{parts[2]}";

        Assert.Null(Validator().Validate(tampered));
    }

    [Fact]
    public void Validate_rejects_expired_token()
    {
        var token = DjangoJwtValidator.CreateSignedTokenForTests(
            SigningKey,
            userId: 42,
            expiresAt: DateTimeOffset.UtcNow.AddMinutes(-1)
        );

        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_refresh_token()
    {
        var token = DjangoJwtValidator.CreateSignedTokenForTests(SigningKey, userId: 42, tokenType: "refresh");

        Assert.Null(Validator().Validate(token));
    }

    private static DjangoJwtValidator Validator()
    {
        var configuration = new ConfigurationManager
        {
            ["SECRET_KEY"] = SigningKey,
        };
        return new DjangoJwtValidator(configuration);
    }
}
