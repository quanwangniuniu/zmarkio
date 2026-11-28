using Microsoft.AspNetCore.Mvc;

namespace DecisionService.Decisions;

[ApiController]
[Route("api/decisions")]
public sealed class DecisionsController(IDecisionRepository repository, IDecisionAccessService accessService) : ControllerBase
{
    [HttpGet]
    public async Task<ActionResult<DecisionListResponse>> List(
        [FromQuery] int? projectId,
        [FromQuery] DecisionStatus? status,
        [FromQuery] int pageSize = 20,
        [FromQuery] int pageToken = 0,
        CancellationToken cancellationToken = default
    )
    {
        projectId ??= ProjectId();
        var access = await accessService.AuthorizeAsync(Request, projectId, DecisionAccessAction.View, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        projectId = access.ProjectId ?? projectId;
        var boundedPageSize = Math.Clamp(pageSize, 1, 100);
        var boundedPageToken = Math.Max(pageToken, 0);
        return Ok(await repository.ListAsync(projectId, status, boundedPageSize, boundedPageToken, cancellationToken));
    }

    [HttpGet("graph")]
    public async Task<ActionResult<DecisionGraphResponse>> Graph(
        [FromQuery] int? projectId,
        [FromQuery] string? scope,
        CancellationToken cancellationToken = default
    )
    {
        projectId ??= ProjectId();
        if (projectId is null)
        {
            return BadRequest(new { project_id = "Project context is required." });
        }

        var access = await accessService.AuthorizeAsync(Request, projectId, DecisionAccessAction.View, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }

        var allProjects = string.Equals(scope, "all_projects", StringComparison.OrdinalIgnoreCase);
        return Ok(await repository.GetGraphAsync(projectId.Value, allProjects, access.UserId, access.IsSuperuser, cancellationToken));
    }

    [HttpGet("{lookup:regex(^(?!graph$).+)}")]
    public async Task<ActionResult<DecisionDetailDto>> Retrieve([FromRoute] string lookup, CancellationToken cancellationToken)
    {
        var decision = await repository.GetAsync(lookup, cancellationToken);
        if (decision is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, decision.ProjectId ?? ProjectId(), DecisionAccessAction.View, cancellationToken);
        return access is { Allowed: true } ? Ok(decision) : DecisionAccessResponses.UnauthorizedOrForbidden(access);
    }

    [HttpPost("topic-labels/{topic}")]
    [HttpPatch("topic-labels/{topic}")]
    public async Task<ActionResult<DecisionTopicLabelResponse>> UpsertTopicLabel(
        [FromRoute] string topic,
        [FromBody] DecisionTopicLabelRequest request,
        [FromQuery] int? projectId,
        CancellationToken cancellationToken
    )
    {
        projectId ??= ProjectId();
        if (projectId is null)
        {
            return BadRequest(new { project_id = "Project context is required." });
        }

        var title = request.Title?.Trim();
        if (string.IsNullOrWhiteSpace(title))
        {
            return BadRequest(new { title = "Title is required." });
        }
        if (title.Length > 80)
        {
            return BadRequest(new { title = "Title must be 80 characters or fewer." });
        }

        var access = await accessService.AuthorizeAsync(Request, projectId, DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }

        try{
            DecisionDraftValidation.ValidateTopic(topic);
        }
        catch (DecisionValidationException ex)
        {
            return BadRequest(new Dictionary<string, string[]>
            {
                [ex.Field] = new[] { ex.Message },
            });
        }
        var label = await repository.UpsertTopicLabelAsync(projectId.Value, topic, title, cancellationToken);
        return label is null ? BadRequest(new { detail = "Topic label could not be saved." }) : Ok(label);
    }

    [HttpDelete("topic-labels/{topic}")]
    public async Task<IActionResult> DeleteTopicLabel([FromRoute] string topic, [FromQuery] int? projectId, CancellationToken cancellationToken)
    {
        projectId ??= ProjectId();
        if (projectId is null)
        {
            return BadRequest(new { project_id = "Project context is required." });
        }

        var access = await accessService.AuthorizeAsync(Request, projectId, DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }

        try{
            DecisionDraftValidation.ValidateTopic(topic);
        }
        catch (DecisionValidationException ex)
        {
            return BadRequest(new Dictionary<string, string[]>
            {
                [ex.Field] = new[] { ex.Message },
            });
        }
        return await repository.DeleteTopicLabelAsync(projectId.Value, topic, cancellationToken) switch
        {
            TopicLabelDeleteResult.Deleted or TopicLabelDeleteResult.NotFound => NoContent(),
            TopicLabelDeleteResult.NotEmpty => BadRequest(new { detail = "Only empty topics can be deleted." }),
            _ => BadRequest(new { detail = "Topic label could not be deleted." })
        };
    }

    [HttpPatch("{lookup}")]
    public async Task<ActionResult<DecisionDetailDto>> PatchCommitted(
        [FromRoute] string lookup,
        [FromBody] UpdateDecisionDraftRequest request,
        CancellationToken cancellationToken
    )
    {
        var existing = await repository.GetAsync(lookup, cancellationToken);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        if (DecisionAccessResponses.DraftOwnerRequired(existing, request, access) is { } forbidden)
        {
            return forbidden;
        }

        try
        {
            DecisionDraftValidation.ValidateTopic(request.Topic);
            if (existing.Status is DecisionStatus.PREDRAFT
                or DecisionStatus.DRAFT
                or DecisionStatus.AWAITING_APPROVAL)
            {
                DecisionDraftValidation.ValidateConfidenceScore(request.ConfidenceScore);
                DecisionDraftValidation.ValidateDecisionTitle(request.Title);
                DecisionDraftValidation.ValidateOptions(request.Options);
            }
            var decision = await repository.UpdateDraftAsync(lookup, access.UserId, request, cancellationToken);
            return decision is null ? BadRequest(new { detail = "Decision cannot be updated in its current state." }) : Ok(decision);
        }
        catch (DecisionValidationException ex)
        {
            return BadRequest(new Dictionary<string, string[]> { [ex.Field] = new[] { ex.Message } });
        }

    }

    [HttpPost("{lookup}/commit")]
    public Task<ActionResult<DecisionActionResponse>> Commit([FromRoute] string lookup, [FromBody] DecisionActionRequest? _ = null, CancellationToken cancellationToken = default)
    {
        return Transition(lookup, DecisionTransitionAction.Commit, "Decision committed", DecisionAccessAction.Edit, cancellationToken);
    }

    [HttpPost("{lookup}/approve")]
    public Task<ActionResult<DecisionActionResponse>> Approve([FromRoute] string lookup, [FromBody] DecisionActionRequest? _ = null, CancellationToken cancellationToken = default)
    {
        return Transition(lookup, DecisionTransitionAction.Approve, "Decision approved", DecisionAccessAction.Approve, cancellationToken);
    }

    [HttpPost("{lookup}/mark_reviewed")]
    public Task<ActionResult<DecisionActionResponse>> MarkReviewed([FromRoute] string lookup, [FromBody] DecisionActionRequest? _ = null, CancellationToken cancellationToken = default)
    {
        return Transition(lookup, DecisionTransitionAction.MarkReviewed, "Decision marked reviewed", DecisionAccessAction.Edit, cancellationToken);
    }

    [HttpPost("{lookup}/archive")]
    public Task<ActionResult<DecisionActionResponse>> Archive([FromRoute] string lookup, [FromBody] DecisionActionRequest? _ = null, CancellationToken cancellationToken = default)
    {
        return Transition(lookup, DecisionTransitionAction.Archive, "Decision archived", DecisionAccessAction.Edit, cancellationToken);
    }

    [HttpDelete("{lookup}")]
    public async Task<IActionResult> Delete([FromRoute] string lookup, CancellationToken cancellationToken)
    {
        var existing = await repository.GetAsync(lookup, cancellationToken);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        return await repository.DeleteAsync(lookup, access.UserId, cancellationToken) ? NoContent() : NotFound(new { detail = "Not found." });
    }

    [HttpGet("{lookup}/signals")]
    public async Task<ActionResult<DecisionSignalListResponse>> ListSignals([FromRoute] string lookup, CancellationToken cancellationToken)
    {
        var existing = await repository.GetAsync(lookup, cancellationToken);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.View, cancellationToken);
        return access is { Allowed: true }
            ? Ok(await repository.ListSignalsAsync(lookup, cancellationToken))
            : DecisionAccessResponses.UnauthorizedOrForbidden(access);
    }

    [HttpPost("{lookup}/signals")]
    public async Task<ActionResult<DecisionSignalDto>> CreateSignal([FromRoute] string lookup, [FromBody] DecisionSignalInput input, CancellationToken cancellationToken)
    {
        var existing = await repository.GetAsync(lookup, cancellationToken);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        if (!DecisionPermissionPolicy.CanEditDraftSignals(existing, access))
        {
            return StatusCode(StatusCodes.Status403Forbidden, new { detail = "Signals can only be changed by the draft owner." });
        }
        try
        {
            var signal = await repository.CreateSignalAsync(lookup, access.UserId, input, cancellationToken);
            return signal is null ? BadRequest(new { detail = "Signal could not be created." }) : Created($"/api/decisions/{lookup}/signals/{signal.Id}", signal);
        }
        catch (DecisionValidationException ex)
        {
            return BadRequest(new Dictionary<string, string[]> { [ex.Field] = new[] { ex.Message } });
        }
    }

    [HttpPatch("{lookup}/signals/{signalId:int}")]
    public async Task<ActionResult<DecisionSignalDto>> UpdateSignal([FromRoute] string lookup, [FromRoute] int signalId, [FromBody] DecisionSignalInput input, CancellationToken cancellationToken)
    {
        var existing = await repository.GetAsync(lookup, cancellationToken);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        if (!DecisionPermissionPolicy.CanEditDraftSignals(existing, access))
        {
            return StatusCode(StatusCodes.Status403Forbidden, new { detail = "Signals can only be changed by the draft owner." });
        }
        try
        {
            var signal = await repository.UpdateSignalAsync(lookup, signalId, input, cancellationToken);
            return signal is null ? NotFound(new { detail = "Not found." }) : Ok(signal);
        }
        catch (DecisionValidationException ex)
        {
            return BadRequest(new Dictionary<string, string[]> { [ex.Field] = new[] { ex.Message } });
        }
    }

    [HttpDelete("{lookup}/signals/{signalId:int}")]
    public async Task<IActionResult> DeleteSignal([FromRoute] string lookup, [FromRoute] int signalId, CancellationToken cancellationToken)
    {
        var existing = await repository.GetAsync(lookup, cancellationToken);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        if (!DecisionPermissionPolicy.CanEditDraftSignals(existing, access))
        {
            return StatusCode(StatusCodes.Status403Forbidden, new { detail = "Signals can only be changed by the draft owner." });
        }
        return await repository.DeleteSignalAsync(lookup, signalId, cancellationToken) ? NoContent() : NotFound(new { detail = "Not found." });
    }

    [HttpGet("{lookup}/connections")]
    public async Task<ActionResult<DecisionConnectionsResponse>> GetConnections([FromRoute] string lookup, CancellationToken cancellationToken)
    {
        var existing = await repository.GetAsync(lookup, cancellationToken);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.View, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        var connections = await repository.GetConnectionsAsync(lookup, cancellationToken);
        return connections is null ? NotFound(new { detail = "Not found." }) : Ok(connections);
    }

    [HttpPut("{lookup}/connections")]
    public async Task<ActionResult<DecisionConnectionsResponse>> UpdateConnections([FromRoute] string lookup, [FromBody] DecisionConnectionsUpdateRequest request, CancellationToken cancellationToken)
    {
        var existing = await repository.GetAsync(lookup, cancellationToken);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        var connections = await repository.UpdateConnectionsAsync(lookup, access.UserId, request, cancellationToken);
        return connections is null ? BadRequest(new { detail = "Connections could not be updated." }) : Ok(connections);
    }

    [HttpGet("{lookup}/reviews")]
    public async Task<ActionResult<IReadOnlyList<DecisionReviewDto>>> ListReviews([FromRoute] string lookup, CancellationToken cancellationToken)
    {
        var existing = await repository.GetAsync(lookup, cancellationToken);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.View, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        var reviews = await repository.ListReviewsAsync(lookup, cancellationToken);
        return reviews is null ? NotFound(new { detail = "Not found." }) : Ok(reviews);
    }

    [HttpPost("{lookup}/reviews")]
    public async Task<ActionResult<DecisionActionResponse>> CreateReview([FromRoute] string lookup, [FromBody] DecisionReviewInput input, CancellationToken cancellationToken)
    {
        var existing = await repository.GetAsync(lookup, cancellationToken);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Approve, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        var decision = await repository.CreateReviewAsync(lookup, access.UserId, input, cancellationToken);
        return decision is null
            ? BadRequest(new { detail = "Decision cannot be reviewed in its current state." })
            : Created($"/api/decisions/{lookup}/reviews", ActionResponse("Decision reviewed", decision));
    }

    private async Task<ActionResult<DecisionActionResponse>> Transition(
        string lookup,
        DecisionTransitionAction transitionAction,
        string detail,
        DecisionAccessAction action,
        CancellationToken cancellationToken
    )
    {
        var existing = await repository.GetAsync(lookup, cancellationToken);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), action, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        try
        {
            var decision = await repository.TransitionAsync(
                lookup,
                access.UserId,
                transitionAction,
                cancellationToken
            );

            return decision is null
                ? BadRequest(new
                {
                    detail = $"Decision cannot perform {transitionAction} in its current state."
                })
                : Ok(ActionResponse(detail, decision));
        }
        catch (DecisionSelfApprovalException ex)
        {
            return StatusCode(StatusCodes.Status403Forbidden, new { detail = ex.Message });
        }
        catch (DecisionTransitionConflictException ex)
        {
            return Conflict(new { detail = ex.Message });
        }
        catch (DecisionValidationException ex)
        {
            return BadRequest(
                new Dictionary<string, string[]>
                {
                    [ex.Field] = new[] { ex.Message }
                }
            );
        }
    }

    private int? ProjectId()
    {
        var rawValue = Request.Headers.TryGetValue("x-project-id", out var headerValue)
            ? headerValue.ToString()
            : Request.Query["project_id"].ToString();

        return int.TryParse(rawValue, out var projectId) ? projectId : null;
    }

    private static DecisionActionResponse ActionResponse(string detail, DecisionDetailDto decision)
    {
        return new DecisionActionResponse(detail, decision.Status, null, decision);
    }
}
