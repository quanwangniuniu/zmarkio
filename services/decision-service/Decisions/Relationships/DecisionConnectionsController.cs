using Microsoft.AspNetCore.Mvc;

namespace DecisionService.Decisions.Relationships;

[ApiController]
[Route("api/decisions")]
public sealed class DecisionConnectionsController(
    IDecisionRepository repository, IDecisionAccessService accessService, IDecisionConnectionsOperations operations) : ControllerBase
{
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
        var connections = await operations.GetConnectionsAsync(lookup, cancellationToken);
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
        var connections = await operations.UpdateConnectionsAsync(lookup, access.UserId, access.IsSuperuser, request, cancellationToken);
        return connections is null ? BadRequest(new { detail = "Connections could not be updated." }) : Ok(connections);
    }

    private int? ProjectId()
    {
        var rawValue = Request.Headers.TryGetValue("x-project-id", out var headerValue)
            ? headerValue.ToString()
            : Request.Query["project_id"].ToString();

        return int.TryParse(rawValue, out var projectId) ? projectId : null;
    }
}
