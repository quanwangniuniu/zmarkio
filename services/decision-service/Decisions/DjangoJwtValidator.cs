using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace DecisionService.Decisions;

public sealed record DecisionJwtPrincipal(int UserId, int AuthTokenVersion);

public sealed class DjangoJwtValidator(IConfiguration configuration)
{
    public DecisionJwtPrincipal? Validate(string token)
    {
        var parts = token.Split('.');
        if (parts.Length != 3)
        {
            return null;
        }

        var header = ReadJson(parts[0]);
        var payload = ReadJson(parts[1]);
        if (header is null || payload is null)
        {
            return null;
        }

        if (!header.RootElement.TryGetProperty("alg", out var alg) || alg.GetString() != "HS256")
        {
            return null;
        }

        var signingKey = configuration["DJANGO_JWT_SIGNING_KEY"]
            ?? configuration["SECRET_KEY"]
            ?? "django-insecure-change-me";
        var expected = HmacSha256($"{parts[0]}.{parts[1]}", signingKey);
        var actual = Base64UrlDecode(parts[2]);
        if (!CryptographicOperations.FixedTimeEquals(expected, actual))
        {
            return null;
        }

        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        if (payload.RootElement.TryGetProperty("exp", out var exp) && now >= exp.GetInt64())
        {
            return null;
        }
        if (payload.RootElement.TryGetProperty("nbf", out var nbf) && now < nbf.GetInt64())
        {
            return null;
        }
        if (
            payload.RootElement.TryGetProperty("token_type", out var tokenType)
            && !string.Equals(tokenType.GetString(), "access", StringComparison.Ordinal)
        )
        {
            return null;
        }

        if (!payload.RootElement.TryGetProperty("user_id", out var userIdElement))
        {
            return null;
        }

        int userId;
        try
        {
            userId = userIdElement.ValueKind == JsonValueKind.String
                ? int.Parse(userIdElement.GetString()!)
                : userIdElement.GetInt32();
        }
        catch (FormatException)
        {
            return null;
        }

        var authTokenVersion = payload.RootElement.TryGetProperty("auth_token_version", out var versionElement)
            ? versionElement.GetInt32()
            : 0;

        return new DecisionJwtPrincipal(userId, authTokenVersion);
    }

    public static string CreateSignedTokenForTests(
        string signingKey,
        int userId,
        int authTokenVersion = 0,
        DateTimeOffset? expiresAt = null,
        string tokenType = "access"
    )
    {
        var header = Base64UrlEncode(Encoding.UTF8.GetBytes("""{"alg":"HS256","typ":"JWT"}"""));
        var exp = (expiresAt ?? DateTimeOffset.UtcNow.AddHours(1)).ToUnixTimeSeconds();
        var payload = Base64UrlEncode(Encoding.UTF8.GetBytes($$"""
        {"token_type":"{{tokenType}}","exp":{{exp}},"user_id":{{userId}},"auth_token_version":{{authTokenVersion}}}
        """));
        var signature = Base64UrlEncode(HmacSha256($"{header}.{payload}", signingKey));
        return $"{header}.{payload}.{signature}";
    }

    private static JsonDocument? ReadJson(string base64Url)
    {
        try
        {
            return JsonDocument.Parse(Base64UrlDecode(base64Url));
        }
        catch (JsonException)
        {
            return null;
        }
        catch (FormatException)
        {
            return null;
        }
    }

    private static byte[] HmacSha256(string payload, string signingKey)
    {
        using var hmac = new HMACSHA256(Encoding.UTF8.GetBytes(signingKey));
        return hmac.ComputeHash(Encoding.ASCII.GetBytes(payload));
    }

    private static byte[] Base64UrlDecode(string input)
    {
        var padded = input.Replace('-', '+').Replace('_', '/');
        padded = padded.PadRight(padded.Length + ((4 - padded.Length % 4) % 4), '=');
        return Convert.FromBase64String(padded);
    }

    private static string Base64UrlEncode(byte[] input)
    {
        return Convert.ToBase64String(input)
            .TrimEnd('=')
            .Replace('+', '-')
            .Replace('/', '_');
    }
}
