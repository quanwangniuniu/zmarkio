using System.Text.RegularExpressions;
namespace DecisionService.Decisions;

internal static class DecisionDraftValidation
{
    public static void ValidateConfidenceScore(int? confidenceScore)
    {
        if (confidenceScore is < 1 or > 5)
        {
            throw new DecisionValidationException(
                "confidenceScore",
                "Confidence score must be between 1 and 5.");
        }
    }

    public static void ValidateDecisionTitle(string? title)
    {
        if (title is not null && title.EnumerateRunes().Count() > 255)
        {
            throw new DecisionValidationException(
                "title",
                "Title must be 255 characters or fewer.");
        }
    }

    public static string NormalizeTopic(string? topic)
    {
        return string.IsNullOrWhiteSpace(topic)
            ? "other"
            : topic.Trim().ToLowerInvariant();
    }

    public static void ValidateTopic(string? topic)
    {
        var normalizedTopic = NormalizeTopic(topic);

        if (!Regex.IsMatch(normalizedTopic, @"^[a-z0-9_-]+$"))
        {
            throw new DecisionValidationException(
                "topic",
                "Topic may only contain lowercase letters, digits, underscores, and hyphens.");
        }

        if (normalizedTopic.Length > 64)
        {
            throw new DecisionValidationException(
                "topic",
                "Topic must be 64 characters or fewer.");
        }
    }
}
