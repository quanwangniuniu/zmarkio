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
}
