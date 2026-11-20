namespace DecisionService.Decisions;

internal static class DecisionValidationSnapshot
{
        internal static object Build(DecisionDetailDto decision, DateTimeOffset now)
    {
        var selectedOptions = decision.Options.Where(option => option.IsSelected).ToList();
        var selectedOption = selectedOptions.FirstOrDefault();

        var contextSummaryPresent = !string.IsNullOrEmpty(decision.ContextSummary);
        var reasoningPresent = !string.IsNullOrEmpty(decision.Reasoning);
        var riskLevelPresent = decision.RiskLevel is not null;
        var confidencePresent = decision.ConfidenceScore is not null;

        var signalsCount = decision.Signals.Count;
        var optionsCount = decision.Options.Count;
        var selectedOptionCount = selectedOptions.Count;

        var requiredFieldPresent = contextSummaryPresent &&
            reasoningPresent &&
            riskLevelPresent &&
            confidencePresent &&
            signalsCount >= 1 &&
            optionsCount >= 2 &&
            selectedOptionCount == 1;

        return new
        {
            required_fields_present = requiredFieldPresent,
            selected_option_id = selectedOption?.Id,
            risk_level = decision.RiskLevel?.ToString(),
            confidence = decision.ConfidenceScore,
            context_summary_present = contextSummaryPresent,
            signals_count = signalsCount,
            options_count = optionsCount,
            selected_options_count = selectedOptionCount,
            reasoning_present = reasoningPresent,
            timestamp = now.ToString("o")
        };
    }

}