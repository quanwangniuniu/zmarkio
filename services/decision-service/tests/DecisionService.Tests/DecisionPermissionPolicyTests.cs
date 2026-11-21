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
    [InlineData(DecisionStatus.PREDRAFT, "signals")]
    [InlineData(DecisionStatus.DRAFT, "signals")]
    [InlineData(DecisionStatus.AWAITING_APPROVAL, null)]
    [InlineData(DecisionStatus.COMMITTED, null)]
    public void DraftOwnerOnlyField_applies_to_predraft_and_draft(DecisionStatus status, string? expected)
    {
        var request = new UpdateDecisionDraftRequest(
            null, null, null, null, "Rewritten", null, null, null,
            Array.Empty<DecisionSignalInput>(), Array.Empty<DecisionOptionInput>(), Array.Empty<int>());

        Assert.Equal(expected, DecisionPermissionPolicy.DraftOwnerOnlyField(Decision(status, 1), request));
    }

    [Fact]
    public void DraftOwnerOnlyField_ignores_unchanged_reasoning_and_shared_fields()
    {
        var request = new UpdateDecisionDraftRequest(
            "Renamed", null, null, null, "Original", "pricing", null, null, null, null, null);

        Assert.Null(DecisionPermissionPolicy.DraftOwnerOnlyField(Decision(DecisionStatus.DRAFT, 1), request));
    }

    [Theory]
    [InlineData(DecisionStatus.PREDRAFT, 1, "Copywriter", true)]
    [InlineData(DecisionStatus.DRAFT, 1, "Copywriter", true)]
    [InlineData(DecisionStatus.PREDRAFT, 2, "Approver", true)]
    [InlineData(DecisionStatus.PREDRAFT, 2, "Copywriter", false)]
    [InlineData(DecisionStatus.DRAFT, 2, "Copywriter", false)]
    [InlineData(DecisionStatus.DRAFT, null, "Copywriter", false)]
    [InlineData(DecisionStatus.DRAFT, null, "Approver", true)]
    [InlineData(DecisionStatus.AWAITING_APPROVAL, 1, "Copywriter", false)]
    public void CanEditDraftSignals_requires_a_draft_and_its_owner_or_an_approver(
        DecisionStatus status, int? createdBy, string role, bool expected)
    {
        var access = new DecisionAccessContext(true, 1, null, false, 12, 7, role, DecisionPermissionPolicy.RoleLevel(role));

        Assert.Equal(expected, DecisionPermissionPolicy.CanEditDraftSignals(Decision(status, createdBy), access));
    }

    private static DecisionDetailDto Decision(DecisionStatus status, int? createdBy)
    {
        return new DecisionDetailDto(
            1, "decision", 1, status, null, null, "Original", null, null, "general", "General",
            default, createdBy, default, createdBy, null, 12, null, false, false, null, null,
            Array.Empty<DecisionSignalDto>(), Array.Empty<DecisionOptionDto>());
    }
}
