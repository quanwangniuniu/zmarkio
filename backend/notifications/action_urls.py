"""Frontend route paths stored on Notification.action_url."""


def overview_action_url() -> str:
    return "/overview"


def task_action_url(task_key) -> str:
    """`task_key` should be the task slug (URLs are slug-only)."""
    return f"/tasks/{task_key}"


def meeting_action_url(meeting_key, project_id: int) -> str:
    """`meeting_key` should be the meeting slug (URLs are slug-only)."""
    return f"/meetings/{meeting_key}?project_id={project_id}"


def decision_action_url(decision_key, project_id: int) -> str:
    """`decision_key` should be the decision slug (URLs are slug-only)."""
    return f"/decisions/{decision_key}?project_id={project_id}"


def campaign_action_url(campaign_key) -> str:
    """`campaign_key` should be the campaign slug (URLs are slug-only)."""
    return f"/campaigns/{campaign_key}"
