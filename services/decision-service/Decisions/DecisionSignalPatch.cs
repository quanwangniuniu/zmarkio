using System.Globalization;
using System.Text.Json;

namespace DecisionService.Decisions;

public sealed class DecisionSignalPatch
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    // These codes match the Signal choices in Django's decision model.
    private static readonly Dictionary<string, Dictionary<string, string>> Labels = new(StringComparer.OrdinalIgnoreCase)
    {
        ["metric"] = new()
        {
            ["ROAS"] = "ROAS", ["CPA"] = "CPA", ["CONVERSION_RATE"] = "Conversion rate",
            ["REVENUE"] = "Revenue", ["PURCHASES"] = "Purchases", ["CTR"] = "CTR",
            ["CLICKS"] = "Clicks", ["IMPRESSIONS"] = "Impressions", ["CPC"] = "CPC",
            ["CPM"] = "CPM", ["AD_SPEND"] = "Ad spend", ["AOV"] = "AOV",
        },
        ["movement"] = new()
        {
            ["SHARP_INCREASE"] = "Sharp increase", ["MODERATE_INCREASE"] = "Moderate increase",
            ["SLIGHT_INCREASE"] = "Slight increase", ["NO_SIGNIFICANT_CHANGE"] = "No significant change",
            ["SLIGHT_DECREASE"] = "Slight decrease", ["MODERATE_DECREASE"] = "Moderate decrease",
            ["SHARP_DECREASE"] = "Sharp decrease", ["VOLATILE"] = "Volatile",
            ["UNEXPECTED_SPIKE"] = "Unexpected spike", ["UNEXPECTED_DROP"] = "Unexpected drop",
        },
        ["period"] = new()
        {
            ["LAST_24_HOURS"] = "Last 24 hours", ["LAST_3_DAYS"] = "Last 3 days",
            ["LAST_7_DAYS"] = "Last 7 days", ["LAST_14_DAYS"] = "Last 14 days",
            ["LAST_30_DAYS"] = "Last 30 days",
        },
        ["comparison"] = new()
        {
            ["NONE"] = "", ["PREVIOUS_PERIOD"] = "vs previous period",
            ["SAME_PERIOD_LAST_WEEK"] = "vs same period last week", ["SINCE_LAUNCH"] = "vs since launch",
        },
    };

    private readonly DecisionSignalInput input;
    private readonly HashSet<string> fields;

    private DecisionSignalPatch(DecisionSignalInput input, HashSet<string> fields)
    {
        this.input = input;
        this.fields = fields;
    }

    public static bool TryParse(JsonElement body, out DecisionSignalPatch? patch, out string? error)
    {
        patch = null;
        error = "Expected a signal object with correctly typed fields.";
        if (body.ValueKind != JsonValueKind.Object)
        {
            return false;
        }

        DecisionSignalInput input;
        try
        {
            input = body.Deserialize<DecisionSignalInput>(JsonOptions)!;
        }
        catch (JsonException)
        {
            return false;
        }

        var fields = body.EnumerateObject().Select(property => property.Name)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        foreach (var (field, value) in new (string, string?)[]
        {
            ("metric", input.Metric), ("movement", input.Movement),
            ("period", input.Period), ("comparison", input.Comparison),
        })
        {
            if (value is not null && !Labels[field].ContainsKey(value))
            {
                error = $"Invalid {field} choice.";
                return false;
            }
        }

        patch = new DecisionSignalPatch(input, fields);
        error = null;
        return true;
    }

    public DecisionSignalDto Apply(DecisionSignalDto existing)
    {
        var updated = existing with
        {
            UpdatedAt = DateTimeOffset.UtcNow,
            Metric = Merge("metric", input.Metric, existing.Metric),
            Movement = Merge("movement", input.Movement, existing.Movement),
            Period = Merge("period", input.Period, existing.Period),
            Comparison = Merge("comparison", input.Comparison, existing.Comparison) ?? "NONE",
            ScopeType = Merge("scopeType", input.ScopeType, existing.ScopeType),
            ScopeValue = Merge("scopeValue", input.ScopeValue, existing.ScopeValue),
            DeltaValue = Merge("deltaValue", input.DeltaValue, existing.DeltaValue),
            DeltaUnit = Merge("deltaUnit", input.DeltaUnit, existing.DeltaUnit),
            DisplayTextOverride = Merge("displayTextOverride",
                string.IsNullOrWhiteSpace(input.DisplayTextOverride) ? null : input.DisplayTextOverride,
                existing.DisplayTextOverride),
        };

        var scope = string.IsNullOrWhiteSpace(updated.ScopeType)
            ? updated.ScopeValue
            : string.IsNullOrWhiteSpace(updated.ScopeValue)
                ? updated.ScopeType
                : $"{updated.ScopeType}: {updated.ScopeValue}";
        var delta = updated.DeltaValue is { } value && !string.IsNullOrWhiteSpace(updated.DeltaUnit)
            ? $"{value.ToString("0.##", CultureInfo.InvariantCulture)} {updated.DeltaUnit}"
            : null;
        var parts = new[]
        {
            Label("metric", updated.Metric), Label("movement", updated.Movement), Label("period", updated.Period),
            Label("comparison", updated.Comparison), scope, delta,
        };
        var displayText = string.IsNullOrWhiteSpace(updated.DisplayTextOverride)
            ? string.Join(" · ", parts.Where(part => !string.IsNullOrWhiteSpace(part)))
            : updated.DisplayTextOverride;
        return updated with { DisplayText = displayText };
    }

    private T Merge<T>(string field, T supplied, T existing) => fields.Contains(field) ? supplied : existing;

    private static string? Label(string field, string? value) =>
        value is not null && Labels[field].TryGetValue(value, out var label) ? label : value;
}
