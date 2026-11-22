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
            PlannedDecisionDate: null,
            Signals: null,
            Options: null,
            ParentDecisionIds: null
        );

        var decision = await repository.CreateAsync(10, 1, request, ct);

        Assert.Null(repository.GetAudit("missing-decision"));

        var initial = repository.GetAudit(decision.Slug);
        Assert.NotNull(initial);
        Assert.Empty(initial.StateTransitions);
        Assert.Empty(initial.CommitRecords);

        var submitted = await repository.TransitionAsync(
            decision.Slug, 1, DecisionTransitionAction.Commit, ct);

        Assert.NotNull(submitted);
        Assert.Equal(DecisionStatus.AWAITING_APPROVAL, submitted.Status);

        var pending = repository.GetAudit(decision.Slug);
        Assert.NotNull(pending);
        Assert.Equal(decision.Id, pending.DecisionId);
        Assert.Equal(DecisionStatus.AWAITING_APPROVAL, pending.Status);
        Assert.Equal(1, pending.CommittedBy);
        Assert.True(pending.RequiresApproval);
        Assert.Equal(submitted.CommittedAt, pending.CommittedAt);
        Assert.Null(pending.ApprovedAt);
        Assert.Null(pending.ApprovedBy);

        var originalSnapshot = Assert.Single(pending.CommitRecords);
        Assert.Equal(decision.Id, originalSnapshot.DecisionId);
        Assert.Equal(1, originalSnapshot.CommittedBy);
        Assert.Equal(
            submitted.CommittedAt,
            originalSnapshot.CommittedAt);
        Assert.NotNull(originalSnapshot.ValidationSnapshot);

        var submissionHistory = Assert.Single(pending.StateTransitions);
        Assert.Equal(decision.Id, submissionHistory.DecisionId);
        Assert.Equal(DecisionStatus.DRAFT, submissionHistory.FromStatus);
        Assert.Equal(
            DecisionStatus.AWAITING_APPROVAL,
            submissionHistory.ToStatus);
        Assert.Equal(1, submissionHistory.TriggeredBy);
        Assert.Equal(submitted.CommittedAt, submissionHistory.Timestamp);

        var rejectedCommit = await repository.TransitionAsync(
            decision.Slug, 1, DecisionTransitionAction.Commit, ct);

        Assert.Null(rejectedCommit);

        var afterRejectedCommit = repository.GetAudit(decision.Slug);
        Assert.NotNull(afterRejectedCommit);
        Assert.Equal(pending.Status, afterRejectedCommit.Status);
        Assert.Equal(pending.CommittedAt, afterRejectedCommit.CommittedAt);
        Assert.Equal(pending.CommittedBy, afterRejectedCommit.CommittedBy);
        Assert.True(afterRejectedCommit.RequiresApproval);
        Assert.Null(afterRejectedCommit.ApprovedAt);
        Assert.Null(afterRejectedCommit.ApprovedBy);
        Assert.Equal(
            submissionHistory,
            Assert.Single(afterRejectedCommit.StateTransitions));
        Assert.Equal(
            originalSnapshot,
            Assert.Single(afterRejectedCommit.CommitRecords));

        var approved = await repository.TransitionAsync(
            decision.Slug, 2, DecisionTransitionAction.Approve, ct);

        Assert.NotNull(approved);
        Assert.Equal(DecisionStatus.COMMITTED, approved.Status);

        var completed = repository.GetAudit(decision.Slug);
        Assert.NotNull(completed);
        Assert.Equal(DecisionStatus.COMMITTED, completed.Status);
        Assert.Equal(1, completed.CommittedBy);
        Assert.Equal(submitted.CommittedAt, completed.CommittedAt);
        Assert.True(completed.RequiresApproval);
        Assert.Equal(2, completed.ApprovedBy);
        Assert.True(completed.ApprovedAt.HasValue);
        Assert.True(submitted.CommittedAt.HasValue);
        Assert.True(
            completed.ApprovedAt.Value >= submitted.CommittedAt.Value);

        Assert.Equal(
            originalSnapshot,
            Assert.Single(completed.CommitRecords));

        Assert.Equal(2, completed.StateTransitions.Count);
        Assert.Equal(submissionHistory, completed.StateTransitions[0]);

        var approvalHistory = completed.StateTransitions[1];
        Assert.Equal(decision.Id, approvalHistory.DecisionId);
        Assert.Equal(
            DecisionStatus.AWAITING_APPROVAL,
            approvalHistory.FromStatus);
        Assert.Equal(DecisionStatus.COMMITTED, approvalHistory.ToStatus);
        Assert.Equal(2, approvalHistory.TriggeredBy);
        Assert.Equal(completed.ApprovedAt, approvalHistory.Timestamp);

        Assert.Empty(initial.StateTransitions);
        Assert.Empty(initial.CommitRecords);
        Assert.Single(pending.StateTransitions);
        Assert.Single(pending.CommitRecords);
        Assert.Null(pending.ApprovedAt);

        var rejectedApproval = await repository.TransitionAsync(
            decision.Slug, 2, DecisionTransitionAction.Approve, ct);

        Assert.Null(rejectedApproval);

        var afterRejectedApproval = repository.GetAudit(decision.Slug);
        Assert.NotNull(afterRejectedApproval);
        Assert.Equal(completed.Status, afterRejectedApproval.Status);
        Assert.Equal(completed.CommittedAt, afterRejectedApproval.CommittedAt);
        Assert.Equal(completed.CommittedBy, afterRejectedApproval.CommittedBy);
        Assert.Equal(completed.RequiresApproval, afterRejectedApproval.RequiresApproval);
        Assert.Equal(completed.ApprovedAt, afterRejectedApproval.ApprovedAt);
        Assert.Equal(completed.ApprovedBy, afterRejectedApproval.ApprovedBy);
        Assert.Equal(2, afterRejectedApproval.StateTransitions.Count);
        Assert.Equal(
            submissionHistory,
            afterRejectedApproval.StateTransitions[0]);
        Assert.Equal(
            approvalHistory,
            afterRejectedApproval.StateTransitions[1]);
        Assert.Equal(
            originalSnapshot,
            Assert.Single(afterRejectedApproval.CommitRecords));
    }
    [Fact]
    public async Task Stale_edit_cannot_overwrite_completed_approval()
    {
        using var timeout = new CancellationTokenSource(
            TimeSpan.FromSeconds(15));
        var ct = timeout.Token;
        var repository = new InMemoryDecisionRepository();

        var decision = await repository.CreateAsync(
            10,
            1,
            new CreateDecisionDraftRequest(
                Title: "Preserve completed approval",
                ContextSummary: null,
                RiskLevel: DecisionRiskLevel.HIGH,
                ConfidenceScore: null,
                Reasoning: null,
                Topic: null,
                IsReferenceCase: false,
                PlannedDecisionDate: null,
                Signals: null,
                Options: null,
                ParentDecisionIds: null
            ),
            ct);

        var submitted = await repository.TransitionAsync(
            decision.Slug, 1, DecisionTransitionAction.Commit, ct);

        Assert.NotNull(submitted);

        var pending = repository.GetAudit(decision.Slug);
        Assert.NotNull(pending);
        var originalSnapshot = Assert.Single(pending.CommitRecords);

        var editReachedGate = new TaskCompletionSource<bool>(
            TaskCreationOptions.RunContinuationsAsynchronously);
        var releaseEdit = new TaskCompletionSource<bool>(
            TaskCreationOptions.RunContinuationsAsynchronously);

        var options = new GatedOptions(
            new[] { new DecisionOptionInput("Stale option", true, 1) },
            editReachedGate,
            releaseEdit.Task,
            ct);

        var editRequest = new UpdateDecisionDraftRequest(
            Title: "Stale edit must not be saved",
            ContextSummary: null,
            RiskLevel: null,
            ConfidenceScore: null,
            Reasoning: null,
            Topic: null,
            IsReferenceCase: null,
            PlannedDecisionDate: null,
            Signals: null,
            Options: options,
            ParentDecisionIds: null
        );

        var editTask = Task.Run(
            () => repository.UpdateDraftAsync(
                decision.Slug, 1, editRequest, ct),
            ct);

        DecisionDetailDto? editResult;

        try
        {
            await editReachedGate.Task.WaitAsync(ct);

            var approved = await repository.TransitionAsync(
                decision.Slug, 2, DecisionTransitionAction.Approve, ct);

            Assert.NotNull(approved);
            Assert.Equal(DecisionStatus.COMMITTED, approved.Status);
        }
        finally
        {
            releaseEdit.TrySetResult(true);
            editResult = await editTask;
        }

        Assert.Null(editResult);

        var final = await repository.GetAsync(decision.Slug, ct);
        Assert.NotNull(final);
        Assert.Equal(DecisionStatus.COMMITTED, final.Status);
        Assert.Equal(decision.Title, final.Title);
        Assert.Empty(final.Options);

        var audit = repository.GetAudit(decision.Slug);
        Assert.NotNull(audit);
        Assert.Equal(DecisionStatus.COMMITTED, audit.Status);
        Assert.Equal(1, audit.CommittedBy);
        Assert.Equal(submitted.CommittedAt, audit.CommittedAt);
        Assert.True(audit.RequiresApproval);
        Assert.Equal(2, audit.ApprovedBy);
        Assert.NotNull(audit.ApprovedAt);

        Assert.Equal(2, audit.StateTransitions.Count);
        Assert.Equal(
            DecisionStatus.AWAITING_APPROVAL,
            audit.StateTransitions[1].FromStatus);
        Assert.Equal(
            DecisionStatus.COMMITTED,
            audit.StateTransitions[1].ToStatus);
        Assert.Equal(2, audit.StateTransitions[1].TriggeredBy);
        Assert.Equal(
            audit.ApprovedAt,
            audit.StateTransitions[1].Timestamp);

        Assert.Equal(
            originalSnapshot,
            Assert.Single(audit.CommitRecords));
    }
    private sealed class GatedOptions : IReadOnlyList<DecisionOptionInput>
    {
        private readonly IReadOnlyList<DecisionOptionInput> values;
        private readonly TaskCompletionSource<bool> reachedGate;
        private readonly Task release;
        private readonly CancellationToken cancellationToken;

        public GatedOptions(
            IReadOnlyList<DecisionOptionInput> values,
            TaskCompletionSource<bool> reachedGate,
            Task release,
            CancellationToken cancellationToken)
        {
            this.values = values;
            this.reachedGate = reachedGate;
            this.release = release;
            this.cancellationToken = cancellationToken;
        }

        public int Count => values.Count;

        public DecisionOptionInput this[int index] => values[index];

        public IEnumerator<DecisionOptionInput> GetEnumerator()
        {
            reachedGate.TrySetResult(true);
            release.WaitAsync(cancellationToken).GetAwaiter().GetResult();
            return values.GetEnumerator();
        }

        System.Collections.IEnumerator
            System.Collections.IEnumerable.GetEnumerator() => GetEnumerator();
    }
}