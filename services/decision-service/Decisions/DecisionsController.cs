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
        return Ok(repository.List(projectId, status, boundedPageSize, boundedPageToken));
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
        return Ok(repository.GetGraph(projectId.Value, allProjects, access.UserId, access.IsSuperuser));
    }

    [HttpGet("{lookup:regex(^(?!graph$).+)}")]
    public async Task<ActionResult<DecisionDetailDto>> Retrieve([FromRoute] string lookup, CancellationToken cancellationToken)
    {
        var decision = repository.Get(lookup);
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

        var label = repository.UpsertTopicLabel(projectId.Value, topic, title);
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

        return repository.DeleteTopicLabel(projectId.Value, topic) switch
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
        var existing = repository.Get(lookup);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        var decision = repository.UpdateDraft(lookup, access.UserId, request);
        return decision is null ? BadRequest(new { detail = "Decision cannot be updated in its current state." }) : Ok(decision);
    }

    [HttpPost("{lookup}/commit")]
    public Task<ActionResult<DecisionActionResponse>> Commit([FromRoute] string lookup, [FromBody] DecisionActionRequest? _ = null, CancellationToken cancellationToken = default)
    {
        return Transition(lookup, DecisionStatus.COMMITTED, "Decision committed", DecisionAccessAction.Edit, cancellationToken);
    }

    [HttpPost("{lookup}/approve")]
    public Task<ActionResult<DecisionActionResponse>> Approve([FromRoute] string lookup, [FromBody] DecisionActionRequest? _ = null, CancellationToken cancellationToken = default)
    {
        return Transition(lookup, DecisionStatus.COMMITTED, "Decision approved", DecisionAccessAction.Approve, cancellationToken);
    }

    [HttpPost("{lookup}/mark_reviewed")]
    public Task<ActionResult<DecisionActionResponse>> MarkReviewed([FromRoute] string lookup, [FromBody] DecisionActionRequest? _ = null, CancellationToken cancellationToken = default)
    {
        return Transition(lookup, DecisionStatus.REVIEWED, "Decision marked reviewed", DecisionAccessAction.Edit, cancellationToken);
    }

    [HttpPost("{lookup}/archive")]
    public Task<ActionResult<DecisionActionResponse>> Archive([FromRoute] string lookup, [FromBody] DecisionActionRequest? _ = null, CancellationToken cancellationToken = default)
    {
        return Transition(lookup, DecisionStatus.ARCHIVED, "Decision archived", DecisionAccessAction.Edit, cancellationToken);
    }

    [HttpDelete("{lookup}")]
    public async Task<IActionResult> Delete([FromRoute] string lookup, CancellationToken cancellationToken)
    {
        var existing = repository.Get(lookup);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        return repository.Delete(lookup, access.UserId) ? NoContent() : NotFound(new { detail = "Not found." });
    }

    [HttpGet("{lookup}/signals")]
    public async Task<ActionResult<DecisionSignalListResponse>> ListSignals([FromRoute] string lookup, CancellationToken cancellationToken)
    {
        var existing = repository.Get(lookup);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.View, cancellationToken);
        return access is { Allowed: true }
            ? Ok(repository.ListSignals(lookup))
            : DecisionAccessResponses.UnauthorizedOrForbidden(access);
    }

    [HttpPost("{lookup}/signals")]
    public async Task<ActionResult<DecisionSignalDto>> CreateSignal([FromRoute] string lookup, [FromBody] DecisionSignalInput input, CancellationToken cancellationToken)
    {
        var existing = repository.Get(lookup);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        if (!CanEditDraftSignals(existing, access))
        {
            return StatusCode(StatusCodes.Status403Forbidden, new { detail = "Signals can only be changed by the draft owner." });
        }
        var signal = repository.CreateSignal(lookup, access.UserId, input);
        return signal is null ? BadRequest(new { detail = "Signal could not be created." }) : Created($"/api/decisions/{lookup}/signals/{signal.Id}", signal);
    }

    [HttpPatch("{lookup}/signals/{signalId:int}")]
    public async Task<ActionResult<DecisionSignalDto>> UpdateSignal([FromRoute] string lookup, [FromRoute] int signalId, [FromBody] DecisionSignalInput input, CancellationToken cancellationToken)
    {
        var existing = repository.Get(lookup);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        if (!CanEditDraftSignals(existing, access))
        {
            return StatusCode(StatusCodes.Status403Forbidden, new { detail = "Signals can only be changed by the draft owner." });
        }
        var signal = repository.UpdateSignal(lookup, signalId, input);
        return signal is null ? NotFound(new { detail = "Not found." }) : Ok(signal);
    }

    [HttpDelete("{lookup}/signals/{signalId:int}")]
    public async Task<IActionResult> DeleteSignal([FromRoute] string lookup, [FromRoute] int signalId, CancellationToken cancellationToken)
    {
        var existing = repository.Get(lookup);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        if (!CanEditDraftSignals(existing, access))
        {
            return StatusCode(StatusCodes.Status403Forbidden, new { detail = "Signals can only be changed by the draft owner." });
        }
        return repository.DeleteSignal(lookup, signalId) ? NoContent() : NotFound(new { detail = "Not found." });
    }

    [HttpGet("{lookup}/connections")]
    public async Task<ActionResult<DecisionConnectionsResponse>> GetConnections([FromRoute] string lookup, CancellationToken cancellationToken)
    {
        var existing = repository.Get(lookup);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.View, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        var connections = repository.GetConnections(lookup);
        return connections is null ? NotFound(new { detail = "Not found." }) : Ok(connections);
    }

    [HttpPut("{lookup}/connections")]
    public async Task<ActionResult<DecisionConnectionsResponse>> UpdateConnections([FromRoute] string lookup, [FromBody] DecisionConnectionsUpdateRequest request, CancellationToken cancellationToken)
    {
        var existing = repository.Get(lookup);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        var connections = repository.UpdateConnections(lookup, access.UserId, request);
        return connections is null ? BadRequest(new { detail = "Connections could not be updated." }) : Ok(connections);
    }

    [HttpGet("{lookup}/reviews")]
    public async Task<ActionResult<IReadOnlyList<DecisionReviewDto>>> ListReviews([FromRoute] string lookup, CancellationToken cancellationToken)
    {
        var existing = repository.Get(lookup);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.View, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        var reviews = repository.ListReviews(lookup);
        return reviews is null ? NotFound(new { detail = "Not found." }) : Ok(reviews);
    }

    [HttpPost("{lookup}/reviews")]
    public async Task<ActionResult<DecisionActionResponse>> CreateReview([FromRoute] string lookup, [FromBody] DecisionReviewInput input, CancellationToken cancellationToken)
    {
        var existing = repository.Get(lookup);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), DecisionAccessAction.Approve, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        var decision = repository.CreateReview(lookup, access.UserId, input);
        return decision is null
            ? BadRequest(new { detail = "Decision cannot be reviewed in its current state." })
            : Created($"/api/decisions/{lookup}/reviews", ActionResponse("Decision reviewed", decision));
    }

    private async Task<ActionResult<DecisionActionResponse>> Transition(
        string lookup,
        DecisionStatus status,
        string detail,
        DecisionAccessAction action,
        CancellationToken cancellationToken
    )
    {
        var existing = repository.Get(lookup);
        if (existing is null)
        {
            return NotFound(new { detail = "Not found." });
        }
        var access = await accessService.AuthorizeAsync(Request, existing.ProjectId ?? ProjectId(), action, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }
        var decision = repository.Transition(lookup, access.UserId, status);
        return decision is null
            ? BadRequest(new { detail = $"Decision cannot transition to {status}." })
            : Ok(ActionResponse(detail, decision));
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

    private static bool CanEditDraftSignals(DecisionDetailDto decision, DecisionAccessContext access)
    {
        return decision.Status == DecisionStatus.DRAFT
            && decision.CreatedBy.HasValue
            && decision.CreatedBy.Value == access.UserId;
    }
}
