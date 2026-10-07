using Microsoft.AspNetCore.Mvc;

namespace DecisionService.Decisions;

[ApiController]
[Route("api/decisions/drafts")]
public sealed class DecisionDraftsController(IDecisionRepository repository, IDecisionAccessService accessService) : ControllerBase
{
    [HttpPost]
    public async Task<ActionResult<DecisionDetailDto>> Create([FromBody] CreateDecisionDraftRequest request, CancellationToken cancellationToken)
    {
        var projectId = ProjectId();
        if (projectId is null)
        {
            return BadRequest(new { project_id = "Project context is required." });
        }

        var access = await accessService.AuthorizeAsync(Request, projectId, DecisionAccessAction.Edit, cancellationToken);
        if (access is not { Allowed: true })
        {
            return DecisionAccessResponses.UnauthorizedOrForbidden(access);
        }

        try
        {
            var decision = repository.Create(projectId, access.UserId, request);
            return Created($"/api/decisions/drafts/{decision.Id}", decision);
        }
        catch (DecisionValidationException ex)
        {
            return BadRequest(new Dictionary<string, string[]> { [ex.Field] = new[] { ex.Message } });
        }
    }

    [HttpGet("{lookup}")]
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

    [HttpPatch("{lookup}")]
    public async Task<ActionResult<DecisionDetailDto>> Patch([FromRoute] string lookup, [FromBody] UpdateDecisionDraftRequest request, CancellationToken cancellationToken)
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
        if (request.OriginMeetingId is not null)
        {
            return BadRequest(new Dictionary<string, string[]>
            {
                ["origin_meeting_id"] = new[] { "Meeting origin can only be set when creating a decision." },
            });
        }

        var decision = repository.UpdateDraft(lookup, access.UserId, request);
        return decision is null ? BadRequest(new { detail = "Draft decision cannot be updated." }) : Ok(decision);
    }

    private int? ProjectId()
    {
        var rawValue = Request.Headers.TryGetValue("x-project-id", out var headerValue)
            ? headerValue.ToString()
            : Request.Query["project_id"].ToString();

        return int.TryParse(rawValue, out var projectId) ? projectId : null;
    }

}
