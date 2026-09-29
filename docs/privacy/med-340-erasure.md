# MED-340: user erasure cascade

The existing `DELETE /auth/me/delete/` endpoint requires the exact confirmation
phrase and returns `202 Accepted`. It immediately disables authentication,
invalidates JWTs, revokes registered Redis sessions, and queues the Celery
erasure worker. Repeated worker execution is safe.

The normative, field-level policy is `backend/core/erasure_policies.py`. It is
centralized so privacy and engineering reviewers can inspect every decision in
one place. A completeness test fails whenever an installed Django model adds a
direct user relation without declaring one of these actions:

| Action | Result |
| --- | --- |
| `delete` | Delete the user-private row and its stored files. |
| `anonymize` | Keep collaborative content and its FK; the account tombstone renders as `Deleted user`. |
| `retain` | Keep legal, audit, approval, consent, and billing evidence unchanged. Access remains restricted by the owning app. |
| `unlink` | Remove only the nullable or many-to-many user relationship. |

## Policy by app

| Apps | Policy |
| --- | --- |
| `access_control`, `core`, `miro` | Delete memberships and active access grants; retain audit/activity evidence; preserve projects and invitations with anonymized attribution. |
| `agent`, `behavioral_tracking`, `tracking`, `user_preferences` | Delete user-private sessions, uploads, behavioral data, preferences, and integrations; preserve shared templates/workflows with anonymized attribution. |
| `facebook_integration`, `google_calendar_integration`, `google_docs_integration`, `linear_integration`, `notion_editor`, `zoom_integration` | Delete user credentials/connections; preserve collaborative documents and meeting metadata with anonymized attribution. |
| `calendars`, `campaign`, `chat`, `comments`, `csm`, `notifications` | Delete private state such as subscriptions, reminders, read state, saved items and recipient notifications; preserve shared content and anonymize authorship. |
| `ad_copy_variation`, `alerting`, `asset`, `customer`, `decision`, `experience_group`, `experiment`, `facebook_meta`, `google_ads`, `klaviyo`, `mailchimp`, `meetings`, `metric_upload`, `optimization`, `retrospective`, `tiktok`, `workflows` | Preserve organization/collaborative records and attribute them to the anonymized tombstone. |
| `admin`, `audit`, `budget_approval`, `policy`, `spreadsheet`, `stripe_meta`, `task` | Retain audit, approval, policy-review, AI-consent and billing evidence; delete private task pins; preserve other collaborative rows with anonymized attribution. |

The worker applies the policy in the public schema and every organization
schema, then removes the avatar and identifying profile/authentication fields.
The retained user row uses unique internal values (`deleted_<id>` and
`deleted_<id>@removed.invalid`); supported UI serializers display
`Deleted user` and do not expose the tombstone email.

Retention duration and access controls for `retain` records remain those of the
owning audit/billing/approval subsystem. This erasure job does not shorten a
legal retention period or rewrite append-only audit payloads. Backup erasure is
out of scope for MED-340 and must follow the separate backup runbook.
