import base64
import hashlib
import logging
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .crypto import encrypt_token
from .linear_graphql import issue_create, issue_update
from .models import LinearCredential

logger = logging.getLogger(__name__)

LINEAR_AUTH_URL = "https://linear.app/oauth/authorize"
LINEAR_TOKEN_URL = "https://api.linear.app/oauth/token"
# After successful OAuth, send the user to Linear (product requirement; token is already saved).
LINEAR_APP_AFTER_LOGIN = "https://linear.app/"


class LinearTokenExchangeError(Exception):
    """Linear POST /oauth/token returned an error (body parsed when JSON)."""

    def __init__(self, message: str, status_code: int | None = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _token_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                return str(
                    err.get("error_description")
                    or err.get("message")
                    or err.get("error")
                    or payload.get("error_description")
                    or response.text
                    or ""
                ).strip()[:600]
            return str(
                payload.get("error_description") or err or response.text or ""
            ).strip()[:600]
    except ValueError:
        # Linear (or a proxy in front of it) can return a non-JSON error body, e.g.
        # an HTML 502 page; fall back to the raw text. The caller logs the result.
        pass
    return (response.text or "").strip()[:600]


def _raise_token_error(response: requests.Response) -> None:
    msg = _token_error_message(response) or f"HTTP {response.status_code}"
    logger.warning("Linear token exchange HTTP %s: %s", response.status_code, msg)
    raise LinearTokenExchangeError(msg, response.status_code)


def _parse_token_response(response: requests.Response) -> dict:
    if not response.ok:
        _raise_token_error(response)
    try:
        data = response.json()
    except ValueError as exc:
        snippet = (response.text or "")[:300]
        raise LinearTokenExchangeError(f"Linear token response was not JSON: {snippet}") from exc
    if not isinstance(data, dict):
        raise LinearTokenExchangeError("Linear token response was not a JSON object")
    return data


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for OAuth PKCE (S256). Required by many Linear OAuth apps."""
    code_verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge


def linear_oauth_env_missing() -> list[str]:
    missing: list[str] = []
    if not (getattr(settings, "LINEAR_CLIENT_ID", "") or "").strip():
        missing.append("LINEAR_CLIENT_ID")
    if not (getattr(settings, "LINEAR_CLIENT_SECRET", "") or "").strip():
        missing.append("LINEAR_CLIENT_SECRET")
    if not (getattr(settings, "LINEAR_REDIRECT_URI", "") or "").strip():
        missing.append("LINEAR_REDIRECT_URI")
    return missing


def build_authorization_url(state: str, code_challenge: str) -> str:
    params = {
        "client_id": settings.LINEAR_CLIENT_ID,
        "redirect_uri": settings.LINEAR_REDIRECT_URI,
        "response_type": "code",
        "state": state,
        # issues:create is required for issueCreate in many Linear OAuth apps (write alone is not enough).
        "scope": "read,write,issues:create",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{LINEAR_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str, code_verifier: str) -> dict:
    """
    Exchange auth code per https://linear.app/developers/oauth-2-0-authentication
    (PKCE + application/x-www-form-urlencoded body).

    Try client_secret in the form body first (most common); if that fails with client-auth
    style errors, retry with HTTP Basic and no secret in body (Linear docs allow both).
    """
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    base_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.LINEAR_REDIRECT_URI,
        "client_id": settings.LINEAR_CLIENT_ID,
        "code_verifier": code_verifier,
    }
    try:
        if settings.LINEAR_CLIENT_SECRET:
            data_form = {**base_data, "client_secret": settings.LINEAR_CLIENT_SECRET}
            r_form = requests.post(
                LINEAR_TOKEN_URL,
                headers=headers,
                data=data_form,
                timeout=30,
            )
            if r_form.ok:
                return _parse_token_response(r_form)
            err_form = _token_error_message(r_form)
            retry_basic = any(
                x in err_form.lower()
                for x in ("invalid_client", "unauthorized", "invalid_request", "unsupported")
            )
            if not retry_basic:
                _raise_token_error(r_form)
            r_basic = requests.post(
                LINEAR_TOKEN_URL,
                headers=headers,
                auth=(settings.LINEAR_CLIENT_ID, settings.LINEAR_CLIENT_SECRET),
                data=base_data,
                timeout=30,
            )
            if r_basic.ok:
                return _parse_token_response(r_basic)
            logger.warning(
                "Linear token exchange: form attempt said %s; basic attempt HTTP %s: %s",
                err_form[:200],
                r_basic.status_code,
                _token_error_message(r_basic)[:200],
            )
            _raise_token_error(r_basic)

        r = requests.post(LINEAR_TOKEN_URL, headers=headers, data=base_data, timeout=30)
        return _parse_token_response(r)
    except LinearTokenExchangeError:
        raise
    except requests.RequestException as exc:
        logger.warning("Linear token exchange request failed: %s", exc)
        raise LinearTokenExchangeError(
            f"Could not reach Linear token endpoint: {exc}",
            status_code=None,
        ) from exc


def _organization_id_for_linear_credential(user) -> int | None:
    """Prefer user.organization; else active project's organization (CustomUser.organization may be unset)."""
    oid = getattr(user, "organization_id", None)
    if oid:
        return oid
    ap_id = getattr(user, "active_project_id", None)
    if not ap_id:
        return None
    from core.models import Project

    return (
        Project.objects.filter(pk=ap_id).values_list("organization_id", flat=True).first()
    )


def _organization_name_for_credential(org_id: int | None) -> str:
    if not org_id:
        return ""
    from core.models import Organization

    return (
        Organization.objects.filter(pk=org_id).values_list("name", flat=True).first() or ""
    )


def save_token_for_user(user, token_payload: dict) -> LinearCredential:
    access = token_payload.get("access_token") or token_payload.get("accessToken")
    if not access:
        raise ValueError(
            "Linear token response missing access_token; keys: "
            + ",".join(sorted(token_payload.keys()))[:200]
        )
    expires_at = None
    if token_payload.get("expires_in") is not None:
        try:
            expires_at = timezone.now() + timedelta(seconds=int(token_payload["expires_in"]))
        except (TypeError, ValueError):
            expires_at = None
    enc = encrypt_token(access) or ""
    org_id = _organization_id_for_linear_credential(user)
    org_name = _organization_name_for_credential(org_id)
    ws_key = str(org_id) if org_id else ""

    # Avoid update_or_create omitting legacy NOT NULL columns in some DB/driver paths.
    try:
        credential = LinearCredential.objects.get(user=user)
    except LinearCredential.DoesNotExist:
        credential = LinearCredential(user=user)

    credential.encrypted_access_token = enc
    credential.token_expires_at = expires_at
    credential.linear_workspace_key = ws_key
    credential.organization_name = org_name
    credential.default_team_id = ""
    credential.sync_enabled = True
    credential.save()
    return credential


def get_linear_access_token(user) -> str | None:
    cred = LinearCredential.objects.filter(user=user).first()
    if not cred:
        return None
    return cred.get_access_token()


def push_task_to_linear(*, user, task, team_id: str | None) -> dict:
    """
    Create a new Linear issue from a task, or update an existing linked issue.

    ``team_id`` is required when ``task.linear_issue_id`` is empty.
    """
    token = get_linear_access_token(user)
    if not token:
        raise ValueError("Linear is not connected.")

    title = (task.summary or "").strip() or "Untitled"
    description = (task.description or "").strip()

    linear_id = (getattr(task, "linear_issue_id", None) or "").strip()
    if linear_id:
        issue_update(
            access_token=token,
            issue_id=linear_id,
            title=title,
            description=description,
        )
        return {"action": "updated", "linear_issue_id": linear_id, "task_id": task.id}

    team = (team_id or "").strip()
    if not team:
        raise ValueError("team_id is required when the task is not linked to a Linear issue.")

    new_id = issue_create(
        access_token=token,
        team_id=team,
        title=title,
        description=description,
    )
    with transaction.atomic():
        task.linear_issue_id = new_id
        task.save(update_fields=["linear_issue_id"])
    return {"action": "created", "linear_issue_id": new_id, "task_id": task.id}
