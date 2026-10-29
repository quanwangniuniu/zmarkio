using DecisionService.Decisions;
using Xunit;
using Microsoft.Extensions.Options;

namespace DecisionService.Tests;

public sealed class DjangoJwtValidatorTests
{

    [Fact]
    public void Validate_accepts_signed_access_token()
    {
        var token = JwtTestTokenFactory.Create();
        var principal = Validator().Validate(token);
        Assert.NotNull(principal);
        Assert.Equal(42, principal.UserId);
        Assert.Equal(7, principal.AuthTokenVersion);
    }

    [Theory]
    [InlineData(123)]
    [InlineData("hello")]
    [InlineData("refresh")]
    [InlineData(null)]
    public void Validate_rejects_invalid_token_type(object? tokenType)
    {
        var token = JwtTestTokenFactory.CreateWithClaim(
            "token_type", tokenType);

        Assert.Null(Validator().Validate(token));
    }

    [Theory]
    [InlineData("token_type")]
    [InlineData("exp")]
    [InlineData("user_id")]
    public void Validate_rejects_missing_required_claim(string claimName)
    {
        var token = JwtTestTokenFactory.CreateWithoutClaim(claimName);

        Assert.Null(Validator().Validate(token));
    }

    [Theory]
    [InlineData("hello")]
    [InlineData("1790000000")]
    [InlineData(20.5)]
    [InlineData(null)]
    [InlineData(true)]
    public void Validate_rejects_invalid_exp_type(object? exp)
    {
        var token = JwtTestTokenFactory.CreateWithClaim("exp", exp);

        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_expired_token()
    {
        var expiredTime = DateTimeOffset.UtcNow
            .AddMinutes(-5)
            .ToUnixTimeSeconds();

        var token = JwtTestTokenFactory.CreateWithClaim(
            "exp", expiredTime);

        Assert.Null(Validator().Validate(token));
    }

    [Theory]
    [InlineData("hello")]
    [InlineData("1790000000")]
    [InlineData(20.5)]
    [InlineData(null)]
    [InlineData(true)]
    public void Validate_rejects_invalid_nbf_type(object? nbf)
    {
        var token = JwtTestTokenFactory.CreateWithClaim("nbf", nbf);

        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_future_nbf()
    {
        var futureTime = DateTimeOffset.UtcNow
            .AddMinutes(5)
            .ToUnixTimeSeconds();

        var token = JwtTestTokenFactory.CreateWithClaim(
            "nbf", futureTime);

        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_accepts_past_nbf()
    {
        var pastTime = DateTimeOffset.UtcNow
            .AddMinutes(-5)
            .ToUnixTimeSeconds();

        var token = JwtTestTokenFactory.CreateWithClaim(
            "nbf", pastTime);

        var principal = Validator().Validate(token);

        Assert.NotNull(principal);
        Assert.Equal(42, principal.UserId);
        Assert.Equal(7, principal.AuthTokenVersion);
    }

    [Theory]
    [InlineData("hello")]
    [InlineData(13.5)]
    [InlineData(null)]
    [InlineData(true)]
    [InlineData("2147483648")]
    public void Validate_rejects_invalid_user_id(object? userId)
    {
        var token = JwtTestTokenFactory.CreateWithClaim(
            "user_id", userId);

        Assert.Null(Validator().Validate(token));
    }

    [Theory]
    [InlineData(" 42 ")]
    [InlineData("+42")]
    [InlineData("42.0")]
    public void Validate_rejects_noncanonical_user_id_string(string userId)
    {
        var token = JwtTestTokenFactory.CreateWithClaim("user_id", userId);

        Assert.Null(Validator().Validate(token));
    }

    [Theory]
    [InlineData(42)]
    [InlineData("42")]
    public void Validate_accepts_valid_user_id(object userId)
    {
        var token = JwtTestTokenFactory.CreateWithClaim(
            "user_id", userId);

        var principal = Validator().Validate(token);

        Assert.NotNull(principal);
        Assert.Equal(42, principal.UserId);
    }

    [Theory]
    [InlineData(" 7 ")]
    [InlineData("+7")]
    [InlineData("7.0")]
    public void Validate_rejects_noncanonical_auth_token_version_string(string version)
    {
        var token = JwtTestTokenFactory.CreateWithClaim(
            "auth_token_version", version);

        Assert.Null(Validator().Validate(token));
    }

    [Theory]
    [InlineData("hello")]
    [InlineData(13.5)]
    [InlineData(null)]
    [InlineData(true)]
    [InlineData("2147483648")]
    public void Validate_rejects_invalid_auth_token_version(object? version)
    {
        var token = JwtTestTokenFactory.CreateWithClaim(
            "auth_token_version", version);

        Assert.Null(Validator().Validate(token));
    }

    [Theory]
    [InlineData(7)]
    [InlineData("7")]
    public void Validate_accepts_valid_auth_token_version(object version)
    {
        var token = JwtTestTokenFactory.CreateWithClaim(
            "auth_token_version", version);

        var principal = Validator().Validate(token);

        Assert.NotNull(principal);
        Assert.Equal(7, principal.AuthTokenVersion);
    }

    [Fact]
    public void Validate_accepts_missing_auth_token_version()
    {
        var token = JwtTestTokenFactory.CreateWithoutClaim(
            "auth_token_version");

        var principal = Validator().Validate(token);

        Assert.NotNull(principal);
        Assert.Equal(0, principal.AuthTokenVersion);
    }

    [Theory]
    [InlineData("""{"typ":"JWT"}""")]
    [InlineData("""{"alg":7,"typ":"JWT"}""")]
    [InlineData("""{"alg":"hello","typ":"JWT"}""")]
    [InlineData(""""Hello"""")]
    public void Validate_rejects_invalid_header(string headerJson)
    {
        var token = JwtTestTokenFactory.Create(
            headerJson: headerJson);

        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_wrong_signing_key()
    {
        var token = JwtTestTokenFactory.Create(
            signingKey: "wrong-signing-key");

        Assert.Null(Validator().Validate(token));
    }

    [Theory]
    [InlineData("abc@Dfwe")]
    [InlineData("SGVsbG8")]
    [InlineData("")]
    public void Validate_rejects_invalid_signature(string signature)
    {
        var token = JwtTestTokenFactory.Create();

        var parts = token.Split('.');
        var modifiedToken = $"{parts[0]}.{parts[1]}.{signature}";

        Assert.Null(Validator().Validate(modifiedToken));
    }

    [Fact]
    public void Validate_rejects_tampered_payload()
    {
        var token = JwtTestTokenFactory.Create();

        var parts = token.Split('.');
        var tamperedToken = $"{parts[0]}.{parts[1]}x.{parts[2]}";

        Assert.Null(Validator().Validate(tamperedToken));
    }

    [Theory]
    [InlineData("""{"exp":not_valid_json}""")]
    [InlineData("not json")]
    [InlineData("[]")]
    [InlineData("null")]
    public void Validate_rejects_invalid_payload_json(string payloadJson)
    {
        var token = JwtTestTokenFactory.CreateRaw(
            """{"alg":"HS256","typ":"JWT"}""",
            payloadJson);

        Assert.Null(Validator().Validate(token));
    }

    [Theory]
    [InlineData("")]
    [InlineData("abc")]
    [InlineData("abc.def")]
    [InlineData("abc.def.ghi.jkl")]
    public void Validate_rejects_invalid_token_structure(string token)
    {
        Assert.Null(Validator().Validate(token));
    }

    private static DjangoJwtValidator Validator()
    {
        var options = Options.Create(new DjangoJwtOptions
    {
        SigningKey = JwtTestTokenFactory.SigningKey
    });

    return new DjangoJwtValidator(options);
    }
}
