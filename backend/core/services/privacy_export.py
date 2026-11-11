import csv
import io
import json
import logging
import os
import secrets
import tempfile
import zipfile
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.files import File
from django.db import connection, models, transaction
from django.urls import reverse
from django.utils import timezone
from psycopg2 import sql

from core.models import DataExportRequest, Organization, Project
from core.services.tenant import slug_to_schema_name
from meta_ads.models import MetaAdCreative

logger = logging.getLogger(__name__)

EXPORT_DOWNLOAD_TTL = timedelta(hours=24)
EXPORT_SIGNING_SALT = "core.privacy-export-download"
EXPORT_ROW_LIMIT = 10000
PERSONAL_DATA_EXPORT_MODEL_ALLOWLIST = {
    "access_control.moduleapprover",
    "access_control.userrole",
    "agent.agentworkflowdefinition",
    "agent.agentworkflowtemplate",
    "agent.dataschematemplate",
    "agent.importedcsvfile",
    "agent.agentsession",
    "alerting.alerttask",
    "asset.asset",
    "asset.assetcomment",
    "asset.assetstatetransition",
    "asset.assetversion",
    "asset.assetversionstatetransition",
    "asset.reviewassignment",
    "behavioral_tracking.focussession",
    "budget_approval.budgetrequest",
    "calendars.calendar",
    "calendars.calendarsettings",
    "calendars.calendarsubscription",
    "calendars.calendarshare",
    "calendars.event",
    "calendars.eventattendee",
    "calendars.eventcategory",
    "calendars.eventreminder",
    "calendars.notification",
    "campaign.campaign",
    "campaign.campaignattachment",
    "campaign.campaignnotificationpreference",
    "campaign.campaignstatushistory",
    "campaign.campaigntemplate",
    "campaign.performancecheckin",
    "campaign.performancesnapshot",
    "chat.chat",
    "chat.chatparticipant",
    "chat.chatstar",
    "chat.message",
    "chat.messageattachment",
    "chat.messagemention",
    "chat.messagereaction",
    "chat.messagereminder",
    "chat.messagestatus",
    "chat.pinnedmessage",
    "chat.savedmessage",
    "chat.scheduledmessage",
    "chat.threadreadstatus",
    "comments.comment",
    "comments.commentattachment",
    "comments.commentmention",
    "core.customuser",
    "core.dataexportrequest",
    "core.organizationactivityevent",
    "core.organizationinvitation",
    "core.organizationinvitationuse",
    "core.organizationmembership",
    "core.project",
    "core.projectinvitation",
    "core.projectmember",
    "core.teammember",
    "csm.csminvitation",
    "csm.csmnotification",
    "csm.customeruser",
    "csm.queueagent",
    "csm.quickreplytemplate",
    "csm.quickreplytemplatehistory",
    "csm.ticket",
    "csm.ticketform",
    "csm.ticketformsubmission",
    "customer.customer",
    "customer.customerinternalnote",
    "customer.customerinternalnoteauditlog",
    "decision.commitrecord",
    "decision.decision",
    "decision.decisionedge",
    "decision.decisionstatetransition",
    "decision.review",
    "decision.signal",
    "experiment.experiment",
    "experiment.experimentprogressupdate",
    "experience_group.experiencegroup",
    "facebook_integration.facebookconnection",
    "facebook_meta.adcreative",
    "facebook_meta.adcreativephotodata",
    "facebook_meta.adcreativevideodata",
    "google_ads.ad",
    "google_ads.adpreview",
    "google_ads.customeraccount",
    "google_calendar_integration.googlecalendarconnection",
    "google_docs_integration.googledocsconnection",
    "klaviyo.emaildraft",
    "klaviyo.klaviyoimage",
    "linear_integration.linearcredential",
    "mailchimp.campaign",
    "mailchimp.campaigncomment",
    "mailchimp.template",
    "meetings.meetingauditlog",
    "meetings.meetingdecisionorigin",
    "meetings.meetingdocument",
    "meetings.meetingtemplate",
    "meetings.participantlink",
    "metric_upload.metricfile",
    "miro.boardaccess",
    "notion_editor.draft",
    "notion_editor.draftrevision",
    "notion_editor.mediafile",
    "notion_editor.notionconnection",
    "notifications.notification",
    "notifications.usernotificationpreference",
    "optimization.optimizationexperiment",
    "optimization.rollbackhistory",
    "optimization.scalingaction",
    "policy.platformpolicyupdate",
    "retrospective.insight",
    "retrospective.retrospectivetask",
    "spreadsheet.patternjob",
    "spreadsheet.sheetstructureoperation",
    "spreadsheet.workflowpattern",
    "stripe_meta.llmcalllog",
    "stripe_meta.payment",
    "stripe_meta.usagedaily",
    "task.approvalchainstep",
    "task.approvalrecord",
    "task.task",
    "task.taskattachment",
    "task.taskcomment",
    "task.taskfieldhistory",
    "task.taskpin",
    "tiktok.addraft",
    "tiktok.adgroup",
    "tiktok.tiktokcreative",
    "tracking.trackingevent",
    "tracking.trackingsession",
    "user_preferences.notificationsettings",
    "user_preferences.slackintegration",
    "user_preferences.userpreferences",
    "workflows.workflow",
    "workflows.workflowversion",
    "zoom_integration.zoomcredential",
    "zoom_integration.zoommeetingdata",
}


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=_json_default, sort_keys=True)
    return _json_default(value)


def _field_value(instance, field, subject_user=None):
    if field.related_model is Project and not field.db_constraint:
        # A project ID alone cannot identify its tenant schema.
        return {"id": getattr(instance, field.attname), "display": None}

    value = getattr(instance, field.name)
    if isinstance(field, models.FileField):
        if not value:
            return None
        payload = {"name": value.name}
        try:
            payload["url"] = value.url
        except ValueError:
            pass
        return payload
    if isinstance(field, models.ForeignKey):
        related = getattr(instance, field.name, None)
        if subject_user is not None and getattr(field.remote_field, "model", None) is get_user_model():
            related_id = getattr(related, "pk", None)
            if related_id == subject_user.id:
                return {
                    "id": subject_user.id,
                    "email": subject_user.email,
                    "is_subject": True,
                }
            return {
                "id": None,
                "display": "redacted_non_subject_user",
                "is_subject": False,
            }
        return {
            "id": getattr(related, "pk", value),
            "display": str(related) if related is not None else None,
        }
    return value


def _serialize_instance(instance, subject_user=None):
    payload = {}
    for field in instance._meta.fields:
        payload[field.name] = _field_value(instance, field, subject_user=subject_user)

    for field in instance._meta.many_to_many:
        manager = getattr(instance, field.name)
        if subject_user is not None and getattr(field.remote_field, "model", None) is get_user_model():
            payload[field.name] = [
                {"id": subject_user.id, "email": subject_user.email, "is_subject": True}
                for related in manager.filter(pk=subject_user.pk)
            ]
        else:
            payload[field.name] = [
                {"id": related.pk, "display": str(related)}
                for related in manager.all()
            ]
    return payload


def _query_user_related_rows(user):
    User = get_user_model()
    sections = {}

    for model in apps.get_models():
        if model._meta.proxy or model._meta.auto_created:
            continue

        label = model._meta.label_lower
        if label != User._meta.label_lower and label not in PERSONAL_DATA_EXPORT_MODEL_ALLOWLIST:
            continue

        try:
            if model is User:
                queryset = model.objects.filter(pk=user.pk)
            else:
                filters = models.Q()
                for field in model._meta.fields:
                    remote_model = getattr(field.remote_field, "model", None)
                    if remote_model is User:
                        filters |= models.Q(**{field.name: user})

                for field in model._meta.many_to_many:
                    remote_model = getattr(field.remote_field, "model", None)
                    if remote_model is User:
                        filters |= models.Q(**{f"{field.name}__id": user.id})

                if not filters:
                    continue
                queryset = model.objects.filter(filters).distinct()

            total_count = queryset.count()
            rows = [
                _serialize_instance(instance, subject_user=user)
                for instance in queryset[:EXPORT_ROW_LIMIT]
            ]
        except Exception as exc:  # pragma: no cover - defensive for unusual model managers
            sections[label] = {"error": str(exc), "rows": []}
            continue

        if rows:
            sections[label] = {
                "rows": rows,
                "count": len(rows),
                "total_count": total_count,
                "truncated": total_count > EXPORT_ROW_LIMIT,
                "row_limit": EXPORT_ROW_LIMIT,
            }

    return sections


def _query_variation_rows(user):
    table_name = "ad_copy_variation_adcopyvariation"
    # Fully qualified tables select sources without changing the worker's search_path.
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT slug FROM {}").format(
                sql.Identifier("public", Organization._meta.db_table)
            )
        )
        # Match PostgreSQL's 63-byte truncation and the Studio runner; names are ASCII.
        candidates = ["public", *(slug_to_schema_name(slug)[:63] for (slug,) in cursor.fetchall())]
        # information_schema filters by privileges and could hide real permission faults.
        cursor.execute(
            """
            SELECT n.nspname
            FROM pg_catalog.pg_namespace AS n
            JOIN pg_catalog.pg_class AS c ON c.relnamespace = n.oid
            WHERE n.nspname = ANY(%s::text[]) AND c.relname = %s
            """,
            [candidates, table_name],
        )
        schemas = [row[0] for row in cursor.fetchall()]
        if not schemas:
            return None

        source_query = sql.SQL("""
            SELECT v.id, v.slug, v.created_at, v.updated_at, v.is_deleted,
                   v.project_id, v.creative_id, v.source_mode, v.source_ref,
                   v.hook, v.headline, v.description, v.cta, v.instruction,
                   v.model_name, v.prompt_version, v.batch_id, v.batch_position,
                   v.status, v.created_by_id, p.name AS project_display,
                   c.meta_creative_id AS creative_ref, c.name AS creative_name,
                   %s::text AS source_schema
            FROM {} AS v
            LEFT JOIN {} AS p ON p.id = v.project_id
            LEFT JOIN {} AS c ON c.id = v.creative_id
            WHERE v.created_by_id = %s
        """)
        queries = []
        parameters = []
        for schema in schemas:
            queries.append(source_query.format(
                sql.Identifier(schema, table_name),
                sql.Identifier(schema, Project._meta.db_table),
                sql.Identifier("public", MetaAdCreative._meta.db_table),
            ))
            parameters.extend([schema, user.id])
        # Count all matching rows before the global limit.
        # COLLATE "C" sorts schema names byte-wise, independent of the database locale.
        cursor.execute(
            sql.SQL("""
                SELECT *, COUNT(*) OVER () AS total_count
                FROM ({}) AS variations
                ORDER BY created_at DESC, source_schema COLLATE "C", id
                LIMIT %s
            """).format(sql.SQL(" UNION ALL ").join(queries)),
            [*parameters, EXPORT_ROW_LIMIT],
        )
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    if not rows:
        return None
    total_count = rows[0]["total_count"]
    for row in rows:
        row.pop("total_count")
        row["project"] = {"id": row.pop("project_id"), "display": row.pop("project_display")}
        creative_id = row.pop("creative_id")
        creative_ref = row.pop("creative_ref")
        creative_name = row.pop("creative_name")
        creative_display = None
        if creative_id is not None:
            # Only populate the two fields __str__ reads; no save or extra query.
            creative_display = str(MetaAdCreative(meta_creative_id=creative_ref, name=creative_name))
        row["creative"] = {"id": creative_id, "display": creative_display}
        row["created_by"] = {"id": row.pop("created_by_id"), "email": user.email, "is_subject": True}
    return {
        "rows": rows,
        "count": len(rows),
        "total_count": total_count,
        "truncated": total_count > EXPORT_ROW_LIMIT,
        "row_limit": EXPORT_ROW_LIMIT,
    }


def build_personal_data_payload(user):
    sections = _query_user_related_rows(user)
    try:
        variation_section = _query_variation_rows(user)
    except Exception:
        # Catch after the collector's atomic block has restored a usable connection.
        # Sources span every organization; keep schema details out of this user's export.
        logger.exception("Variation export failed for user %s", user.id)
        variation_section = {"error": "Variation export failed", "rows": []}
    if variation_section is not None:
        sections["ad_copy_variation.adcopyvariation"] = variation_section
    generated_at = timezone.now()

    return {
        "manifest": {
            "format": "marketing-simplified-personal-data-export/v1",
            "generated_at": generated_at,
            "subject_user_id": user.id,
            "subject_email": user.email,
            "included_sections": sorted(sections.keys()),
            "section_count": len(sections),
        },
        "account": _serialize_instance(user, subject_user=user),
        "sections": sections,
    }


def _write_csv(archive, filename, rows):
    output = io.StringIO()
    fieldnames = sorted({key for row in rows for key in row.keys()})
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in fieldnames})
    archive.writestr(filename, output.getvalue())


def _write_json_export(archive, payload):
    archive.writestr(
        "manifest.json",
        json.dumps(payload["manifest"], default=_json_default, indent=2, sort_keys=True),
    )
    archive.writestr(
        "account.json",
        json.dumps(payload["account"], default=_json_default, indent=2, sort_keys=True),
    )
    for label, section in payload["sections"].items():
        archive.writestr(
            f"data/{label}.json",
            json.dumps(section, default=_json_default, indent=2, sort_keys=True),
        )


def _write_csv_export(archive, payload):
    manifest = {
        key: _csv_value(value)
        for key, value in payload["manifest"].items()
    }
    _write_csv(archive, "manifest.csv", [manifest])
    _write_csv(archive, "account.csv", [payload["account"]])
    for label, section in payload["sections"].items():
        rows = section.get("rows") or []
        if rows:
            _write_csv(archive, f"data/{label}.csv", rows)
        elif section.get("error"):
            _write_csv(archive, f"data/{label}.errors.csv", [{"error": section["error"]}])


def assemble_data_export_zip(export_request):
    user = export_request.user
    payload = build_personal_data_payload(user)
    export_format = export_request.export_format or DataExportRequest.ExportFormat.JSON
    payload["manifest"]["export_format"] = export_format

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        temp_path = tmp.name

    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if export_format == DataExportRequest.ExportFormat.CSV:
                _write_csv_export(archive, payload)
            else:
                _write_json_export(archive, payload)

        expires_at = timezone.now() + EXPORT_DOWNLOAD_TTL
        filename = f"personal-data-export-{user.id}-{export_request.id}-{export_format}.zip"
        with open(temp_path, "rb") as fh:
            export_request.file.save(filename, File(fh), save=False)
        export_request.status = DataExportRequest.Status.READY
        export_request.completed_at = timezone.now()
        export_request.expires_at = expires_at
        export_request.failure_reason = ""
        export_request.metadata = {
            "export_format": export_format,
            "included_sections": payload["manifest"]["included_sections"],
            "section_count": payload["manifest"]["section_count"],
            "generated_at": payload["manifest"]["generated_at"].isoformat(),
        }
        export_request.save(
            update_fields=[
                "file",
                "status",
                "completed_at",
                "expires_at",
                "failure_reason",
                "metadata",
                "updated_at",
            ]
        )
        return export_request
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def mark_expired_exports():
    now = timezone.now()
    return DataExportRequest.objects.filter(
        status=DataExportRequest.Status.READY,
        expires_at__lt=now,
    ).update(status=DataExportRequest.Status.EXPIRED)


def create_download_token(export_request):
    nonce = secrets.token_urlsafe(24)
    metadata = dict(export_request.metadata or {})
    metadata["download_nonce"] = nonce
    metadata["download_token_created_at"] = timezone.now().isoformat()
    export_request.metadata = metadata
    export_request.save(update_fields=["metadata", "updated_at"])
    return signing.dumps(
        {
            "export_request_id": str(export_request.id),
            "user_id": export_request.user_id,
            "file": export_request.file.name,
            "nonce": nonce,
        },
        salt=EXPORT_SIGNING_SALT,
    )


def build_download_url(export_request, request=None):
    if export_request.status != DataExportRequest.Status.READY or not export_request.file:
        return None
    if export_request.expires_at and export_request.expires_at <= timezone.now():
        return None

    path = reverse("privacy-export-download", kwargs={"export_id": export_request.id})
    url = f"{path}?token={create_download_token(export_request)}"
    return request.build_absolute_uri(url) if request is not None else url


def verify_download_token(export_request, token):
    if not token:
        return False

    try:
        payload = signing.loads(
            token,
            salt=EXPORT_SIGNING_SALT,
            max_age=int(EXPORT_DOWNLOAD_TTL.total_seconds()),
        )
    except signing.BadSignature:
        return False

    metadata = dict(export_request.metadata or {})
    expected_nonce = metadata.get("download_nonce")
    verified = (
        payload.get("export_request_id") == str(export_request.id)
        and payload.get("user_id") == export_request.user_id
        and payload.get("file") == export_request.file.name
        and payload.get("nonce") == expected_nonce
    )
    if not verified:
        return False

    metadata.pop("download_nonce", None)
    metadata["download_nonce_used_at"] = timezone.now().isoformat()
    export_request.metadata = metadata
    export_request.save(update_fields=["metadata", "updated_at"])
    return True
