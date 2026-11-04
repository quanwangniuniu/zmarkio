using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace DecisionService.Tests;

public static class JwtTestTokenFactory
{
    public const string SigningKey = "test-signing-key";

    public static string Create(
        Dictionary<string, object?>? claims = null,
        string headerJson = """{"alg":"HS256","typ":"JWT"}""",
        string? signingKey = null)
    {
        claims ??= DefaultClaims();

        return CreateRaw(
            headerJson,
            JsonSerializer.Serialize(claims),
            signingKey);
    }

    public static string CreateRaw(
        string headerJson,
        string payloadJson,
        string? signingKey = null)
    {
        var header = Base64UrlEncode(Encoding.UTF8.GetBytes(headerJson));
        var payload = Base64UrlEncode(Encoding.UTF8.GetBytes(payloadJson));

        var message = $"{header}.{payload}";

        using var hmac = new HMACSHA256(
            Encoding.UTF8.GetBytes(signingKey ?? SigningKey));

        var signature = Base64UrlEncode(
            hmac.ComputeHash(Encoding.ASCII.GetBytes(message)));

        return $"{message}.{signature}";
    }

    private static string Base64UrlEncode(byte[] input) =>
        Convert.ToBase64String(input)
            .TrimEnd('=')
            .Replace('+', '-')
            .Replace('/', '_');

    public static Dictionary<string, object?> DefaultClaims() =>
        new()
        {
            ["token_type"] = "access",
            ["exp"] = DateTimeOffset.UtcNow.AddHours(1).ToUnixTimeSeconds(),
            ["user_id"] = 42,
            ["auth_token_version"] = 7
        };

    public static string CreateWithClaim(string claimName, object? value)
    {
        var claims = DefaultClaims();
        claims[claimName] = value;

        return Create(claims);
    }

    public static string CreateWithoutClaim(string claimName)
    {
        var claims = DefaultClaims();
        claims.Remove(claimName);

        return Create(claims);
    }

    public static string CreateValidToken(
    int userId = 42,
    int authTokenVersion = 0)
    {
        var claims = DefaultClaims();

        claims["user_id"] = userId;
        claims["auth_token_version"] = authTokenVersion;

        return Create(claims);
    }
}