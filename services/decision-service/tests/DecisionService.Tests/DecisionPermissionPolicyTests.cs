using DecisionService.Decisions;
using Xunit;

namespace DecisionService.Tests;

public sealed class DecisionPermissionPolicyTests
{
    [Theory]
    [InlineData("owner", DecisionAccessAction.View, true)]
    [InlineData("owner", DecisionAccessAction.Edit, true)]
    [InlineData("owner", DecisionAccessAction.Approve, true)]
    [InlineData("Organization Admin", DecisionAccessAction.Approve, true)]
    [InlineData("Data Analyst", DecisionAccessAction.Approve, true)]
    [InlineData("Senior Media Buyer", DecisionAccessAction.Approve, false)]
    [InlineData("Copywriter", DecisionAccessAction.Edit, true)]
    [InlineData("viewer", DecisionAccessAction.View, true)]
    [InlineData("viewer", DecisionAccessAction.Edit, false)]
    [InlineData("unknown role", DecisionAccessAction.View, true)]
    [InlineData("unknown role", DecisionAccessAction.Edit, false)]
    public void IsAllowed_applies_decision_role_thresholds(string role, DecisionAccessAction action, bool expected)
    {
        Assert.Equal(expected, DecisionPermissionPolicy.IsAllowed(role, action));
    }

    [Fact]
    public void RoleLevel_trims_role_names()
    {
        Assert.Equal(2, DecisionPermissionPolicy.RoleLevel(" Organization Admin "));
    }

    [Theory]
    [InlineData(DecisionStatus.PREDRAFT, "options")]
    [InlineData(DecisionStatus.DRAFT, "options")]
    [InlineData(DecisionStatus.AWAITING_APPROVAL, null)]
    [InlineData(DecisionStatus.COMMITTED, null)]
    public void DraftOwnerOnlyField_applies_to_predraft_and_draft(DecisionStatus status, string? expected)
    {
        var decision = new DecisionDetailDto(
            1, "decision", 1, status, null, null, null, null, null, "general", "General",
            default, 1, default, 1, null, 12, null, false, false, null, null,
            Array.Empty<DecisionSignalDto>(), Array.Empty<DecisionOptionDto>());
        var request = new UpdateDecisionDraftRequest(
            null, null, null, null, null, null, null, null, null,
            new[] { new DecisionOptionInput("Ship", true, 0) }, null);

        Assert.Equal(expected, DecisionPermissionPolicy.DraftOwnerOnlyField(decision, request));
    }
}
