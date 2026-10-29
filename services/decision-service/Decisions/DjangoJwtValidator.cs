using Microsoft.Extensions.Options;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;


namespace DecisionService.Decisions;

public sealed record DecisionJwtPrincipal(int UserId, int AuthTokenVersion);

public sealed class DjangoJwtValidator
{
    private readonly string _signingKey;
    public DjangoJwtValidator(IOptions<DjangoJwtOptions> options)
    {
        _signingKey = options.Value.SigningKey
        ?? throw new InvalidOperationException(
            "Django JWT signing key is not configured.");
    }
    public DecisionJwtPrincipal? Validate(string token)
    {

        bool ValueKindString(JsonElement input) 
            => input.ValueKind == JsonValueKind.String; 
        
        bool ValueKindNumber(JsonElement input)
            => input.ValueKind == JsonValueKind.Number;

        var parts = token.Split('.');
        if (parts.Length != 3)
        {
            return null;
        }

        using var header = ReadJson(parts[0]);
        using var payload = ReadJson(parts[1]);
        if (header is null || payload is null)
        {
            return null;
        }
        if (header.RootElement.ValueKind != JsonValueKind.Object 
            || payload.RootElement.ValueKind != JsonValueKind.Object)
        {
            return null;
        }

        if (!header.RootElement.TryGetProperty("alg", out var alg) 
            || !ValueKindString(alg)
            || alg.GetString() != "HS256")
        {
            return null;
        }

        var expected = HmacSha256($"{parts[0]}.{parts[1]}", _signingKey);
        
        byte[] actual;
        try
        {
            actual = Base64UrlDecode(parts[2]);
        }
        catch (FormatException)
        {
            return null;
        }
        if (!CryptographicOperations.FixedTimeEquals(expected, actual))
        {
            return null;
        }

        // JWT NumericDate claims must be JSON numbers containing integer
        // Unix timestamps. Numeric strings are not accepted for exp or nbf.
        var now = DateTimeOffset.UtcNow.ToUnixTimeSeconds();
        if (!payload.RootElement.TryGetProperty("exp", out var exp) 
            || !ValueKindNumber(exp)
            || !exp.TryGetInt64(out var expiry)
            || now >= expiry)
        {
            return null;
        }
        if (payload.RootElement.TryGetProperty("nbf", out var nbf) 
            && (!ValueKindNumber(nbf) || !nbf.TryGetInt64(out var nbfInt) || now < nbfInt))
        {
            return null;
        }
        if (
            !payload.RootElement.TryGetProperty("token_type", out var tokenType)
            || !ValueKindString(tokenType)
            || tokenType.GetString() != "access"
        )
        {
            return null;
        }

        if (!payload.RootElement.TryGetProperty("user_id", out var userIdElement))
        {
            return null;
        }

        // Django identity claims support JSON integers and numeric strings.
        // Parse numeric strings using invariant culture and strict number
        // styles so their interpretation is independent of server locale.
        int userId;
        if (ValueKindString(userIdElement))
        {
            if (!int.TryParse(
                userIdElement.GetString(),
                NumberStyles.None,
                CultureInfo.InvariantCulture,
                out userId))
                {
                return null;
                }
        }
        else if (ValueKindNumber(userIdElement))
        {
            if (!userIdElement.TryGetInt32(out userId))
                return null;
        }
        else
        {
            return null;
        }

        int authTokenVersion = 0;
        if (payload.RootElement.TryGetProperty("auth_token_version", out var versionElement))
        {
            if (ValueKindNumber(versionElement))
            {
                if (!versionElement.TryGetInt32(out authTokenVersion))
                {
                    return null;
                }
            } 
            else if (ValueKindString(versionElement))
            {
                if (!int.TryParse(versionElement.GetString(),
                    NumberStyles.None,
                    CultureInfo.InvariantCulture,
                    out authTokenVersion))
                {
                    return null;
                }
            }
            else
            {
                return null;
            }  
        }
        

        return new DecisionJwtPrincipal(userId, authTokenVersion);
    }


    private static JsonDocument? ReadJson(string base64Url)
    {
        try
        {
            var raw = Base64UrlDecode(base64Url);
            return JsonDocument.Parse(raw);
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

    private static byte[] HmacSha256(string msg, string signingKey)
    {
        using var hmac = new HMACSHA256(Encoding.UTF8.GetBytes(signingKey));
        return hmac.ComputeHash(Encoding.ASCII.GetBytes(msg));
    }

    private static byte[] Base64UrlDecode(string input)
    {
        var padded = input.Replace('-', '+').Replace('_', '/');
        padded = padded.PadRight(padded.Length + ((4 - padded.Length % 4) % 4), '=');
        return Convert.FromBase64String(padded);
    }

}
