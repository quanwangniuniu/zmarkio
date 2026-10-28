using DecisionService.Decisions;
using Microsoft.Extensions.Configuration;
using Xunit;
using System.Text;
using System.Security.Cryptography;

namespace DecisionService.Tests;

public sealed class DjangoJwtValidatorTests
{
    private const string SigningKey = "test-signing-key";

    private static string Base64UrlEncode(byte[] input)
    {
        return Convert.ToBase64String(input)
            .TrimEnd('=')
            .Replace('+', '-')
            .Replace('/', '_');
    }

    private static byte[] HmacSha256(string msg, string signingKey)
    {
        using var hmac = new HMACSHA256(Encoding.UTF8.GetBytes(signingKey));
        return hmac.ComputeHash(Encoding.ASCII.GetBytes(msg));
    }

    private static string CreateCustomToken(string headerJson, string payloadJson)
    {
        string header = Base64UrlEncode(Encoding.UTF8.GetBytes(headerJson));
        string payload = Base64UrlEncode(Encoding.UTF8.GetBytes(payloadJson));
        string message = $"{header}.{payload}";
        var signature = Base64UrlEncode(HmacSha256(message, SigningKey));
        return $"{message}.{signature}";
    }

    [Fact]
    public void Validate_accepts_signed_access_token()
    {
        var token = CreateCustomToken("""{"alg":"HS256", "typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);
        var principal = Validator().Validate(token);
        Assert.NotNull(principal);
        Assert.Equal(42, principal.UserId);
        Assert.Equal(7, principal.AuthTokenVersion);
    }

    [Fact]
    public void Validate_rejects_tampered_token()
    {
        var token = CreateCustomToken("""{"alg":"HS256", "typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);
        var parts = token.Split('.');
        var tampered = $"{parts[0]}.{parts[1]}x.{parts[2]}";
        Assert.Null(Validator().Validate(tampered));
    }

    [Fact]
    public void Validate_rejects_header_not_json_object_token()
    {
        var token = CreateCustomToken("\"Hello\"", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);
 
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_header_not_contain_alg()
    {
        var token = CreateCustomToken("""{"typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);

        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_header_alg_is_not_string()
    {
        var token = CreateCustomToken("""{"alg":7,"typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);

        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_header_alg_is_not_correct_string()
    {
        var token = CreateCustomToken("""{"alg":"hello","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);

        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_malformat_signature()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);
        var parts = token.Split('.');
        var malformatSignature = "abc@Dfwe";
        token = $"{parts[0]}.{parts[1]}.{malformatSignature}";
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_wrong_signature()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);
        var parts = token.Split('.');
        var wrongSignature = "SGVsbG8";
        token = $"{parts[0]}.{parts[1]}.{wrongSignature}";
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_payload_not_contain_exp()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_exp_is_not_number()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp": "hello",
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_exp_not_long_int()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp": {{DateTimeOffset.UtcNow.ToUnixTimeSeconds()}} + 20.5,
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_exp_less_than_now()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp": {{DateTimeOffset.UtcNow.AddMinutes(-1).ToUnixTimeSeconds()}},
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_nbf_not_number()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7,
                        "nbf": "hello"
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_nbf_not_long_int()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7,
                        "nbf": {{DateTimeOffset.UtcNow.ToUnixTimeSeconds()}} + 0.5
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_nbf_bigger_than_now()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type":"access", 
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7,
                        "nbf": {{DateTimeOffset.UtcNow.AddMinutes(5).ToUnixTimeSeconds()}}
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_no_token_type()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_token_type_not_string()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type": 123,
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_token_type_not_access()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type": "hello",
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id":42, 
                        "auth_token_version":7
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }
    
    [Fact]
    public void Validate_rejects_no_user_id()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type": "access",
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "auth_token_version":7
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_user_id_not_int()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type": "access",
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id": "hello",
                        "auth_token_version":7
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_accepts_user_id_numeric_string()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type": "access",
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id": "42",
                        "auth_token_version": 7
                    }
                    """);
        var principal = Validator().Validate(token);
        Assert.NotNull(principal);
        Assert.Equal(42, principal.UserId);
        Assert.Equal(7, principal.AuthTokenVersion);
    }

    [Fact]
    public void Validate_rejects_auth_token_version_not_number()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type": "access",
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id": 42,
                        "auth_token_version": "hello"
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_rejects_auth_token_version_not_32_int()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type": "access",
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id": 42,
                        "auth_token_version": 13.5
                    }
                    """);
        Assert.Null(Validator().Validate(token));
    }

    [Fact]
    public void Validate_accepts_auth_token_version_numeric_string()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type": "access",
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id": 42,
                        "auth_token_version": "7"
                    }
                    """);
        var principal = Validator().Validate(token);
        Assert.NotNull(principal);
        Assert.Equal(42, principal.UserId);
        Assert.Equal(7, principal.AuthTokenVersion);
    }

    [Fact]
    public void Validate_accepts_no_auth_token_version()
    {
        var token = CreateCustomToken("""{"alg":"HS256","typ":"JWT"}""", 
                    $$"""
                    {
                        "token_type": "access",
                        "exp":{{DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds()}}, 
                        "user_id": 42
                    }
                    """);
        var principal = Validator().Validate(token);
        Assert.NotNull(principal);
        Assert.Equal(42, principal.UserId);
        Assert.Equal(0, principal.AuthTokenVersion);
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
