using System.Collections;
using System.Reflection;
using DecisionService.Decisions;
using Xunit;

namespace DecisionService.Tests;

public sealed class InMemoryDecisionApprovalTests
{
    [Fact]
    public async Task Approval_preserves_submission_snapshot_and_records_audit()
    {
        var repository = new InMemoryDecisionRepository();
        var ct = CancellationToken.None;

        var request = new CreateDecisionDraftRequest(
            Title: "High-risk approval audit",
            ContextSummary: "Campaign launch",
            RiskLevel: DecisionRiskLevel.HIGH,
            ConfidenceScore: 80,
            Reasoning: "Review required",
            Topic: "campaign",
            IsReferenceCase: false,
            CreatedByAgent: false,
            AgentSessionId: null,
            PlannedDecisionDate: null,
            Signals: null,
            Options: null,
            ParentDecisionIds: null
        );

        var decision = await repository.CreateAsync(10, 1, request, ct);

        var submitted = await repository.TransitionAsync(
            decision.Slug, 1, DecisionTransitionAction.Commit, ct);

        Assert.NotNull(submitted);
        Assert.Equal(DecisionStatus.AWAITING_APPROVAL, submitted.Status);

        var pending = StoredDecision(repository, decision.Id);
        Assert.Equal(1, Value(pending, "CommittedBy"));
        Assert.Equal(true, Value(pending, "RequiresApproval"));
        Assert.Equal(submitted.CommittedAt, Value(pending, "CommittedAt"));
        Assert.Null(Value(pending, "ApprovedAt"));
        Assert.Null(Value(pending, "ApprovedBy"));

        var originalSnapshot = Assert.Single(Items(pending, "CommitRecords"));
        Assert.Equal(decision.Id, Value(originalSnapshot, "DecisionId"));
        Assert.Equal(1, Value(originalSnapshot, "CommittedBy"));
        Assert.Equal(
            submitted.CommittedAt,
            Value(originalSnapshot, "CommittedAt"));
        Assert.NotNull(Value(originalSnapshot, "ValidationSnapshot"));

        var submissionHistory = Assert.Single(
            Items(pending, "StateTransitions"));
        Assert.Equal(
            DecisionStatus.DRAFT,
            Value(submissionHistory, "FromStatus"));
        Assert.Equal(
            DecisionStatus.AWAITING_APPROVAL,
            Value(submissionHistory, "ToStatus"));
        Assert.Equal(1, Value(submissionHistory, "TriggeredBy"));
        Assert.Equal(
            submitted.CommittedAt,
            Value(submissionHistory, "Timestamp"));

        var rejectedCommit = await repository.TransitionAsync(
            decision.Slug, 1, DecisionTransitionAction.Commit, ct);

        Assert.Null(rejectedCommit);
        var afterRejectedCommit = StoredDecision(repository, decision.Id);
        Assert.Single(Items(afterRejectedCommit, "StateTransitions"));
        Assert.Same(
            originalSnapshot,
            Assert.Single(Items(afterRejectedCommit, "CommitRecords")));

        var approved = await repository.TransitionAsync(
            decision.Slug, 2, DecisionTransitionAction.Approve, ct);

        Assert.NotNull(approved);
        Assert.Equal(DecisionStatus.COMMITTED, approved.Status);

        var completed = StoredDecision(repository, decision.Id);
        Assert.Equal(1, Value(completed, "CommittedBy"));
        Assert.Equal(submitted.CommittedAt, Value(completed, "CommittedAt"));
        Assert.Equal(true, Value(completed, "RequiresApproval"));
        Assert.Equal(2, Value(completed, "ApprovedBy"));

        var approvedAt = Assert.IsType<DateTimeOffset>(
            Value(completed, "ApprovedAt"));
        Assert.True(approvedAt >= submitted.CommittedAt!.Value);

        Assert.Same(
            originalSnapshot,
            Assert.Single(Items(completed, "CommitRecords")));

        var history = Items(completed, "StateTransitions");
        Assert.Equal(2, history.Length);
        Assert.Same(submissionHistory, history[0]);
        Assert.Equal(
            DecisionStatus.AWAITING_APPROVAL,
            Value(history[1], "FromStatus"));
        Assert.Equal(
            DecisionStatus.COMMITTED,
            Value(history[1], "ToStatus"));
        Assert.Equal(2, Value(history[1], "TriggeredBy"));
        Assert.Equal(approvedAt, Value(history[1], "Timestamp"));

        var rejectedApproval = await repository.TransitionAsync(
            decision.Slug, 2, DecisionTransitionAction.Approve, ct);

        Assert.Null(rejectedApproval);
        var afterRejectedApproval = StoredDecision(repository, decision.Id);
        Assert.Same(completed, afterRejectedApproval);
    }

    private static object StoredDecision(
        InMemoryDecisionRepository repository,
        int id)
    {
        var field = typeof(InMemoryDecisionRepository).GetField(
            "_decisions",
            BindingFlags.Instance | BindingFlags.NonPublic);

        Assert.NotNull(field);
        var decisions = Assert.IsAssignableFrom<IDictionary>(
            field.GetValue(repository));

        var decision = decisions[id];
        Assert.NotNull(decision);
        return decision;
    }

    private static object? Value(object record, string name)
    {
        var property = record.GetType().GetProperty(name);
        Assert.NotNull(property);
        return property.GetValue(record);
    }

    private static object[] Items(object record, string name)
    {
        return Assert.IsAssignableFrom<IEnumerable>(Value(record, name))
            .Cast<object>()
            .ToArray();
    }
}