import csv
import io
import json
import zipfile
from unittest.mock import patch

import pytest
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from psycopg2 import sql
from rest_framework.test import APIClient

from agent.models import AgentSession
from chat.models import Chat, ChatParticipant, ChatType
from core.models import AuditEvent, DataExportRequest, Organization, Project, ProjectMember
from core.services import privacy_export
from core.services.privacy_export import assemble_data_export_zip, build_personal_data_payload
from core.services.tenant import slug_to_schema_name
from core.tenant_context import tenant_schema_context
from decision.models import Decision
from meetings.models import Meeting, MeetingTypeDefinition, ParticipantLink
from task.models import Task


VARIATION_TABLE = "ad_copy_variation_adcopyvariation"
VARIATION_SECTION = "ad_copy_variation.adcopyvariation"


def _create_variation_table(schema):
    # Independent legacy shape: also works after the old Django app is removed.
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("""
            CREATE TABLE IF NOT EXISTS {} (
                id bigint PRIMARY KEY, slug varchar(255) UNIQUE NOT NULL,
                created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
                is_deleted boolean NOT NULL, project_id bigint, creative_id bigint,
                source_mode varchar(32) NOT NULL, source_ref text NOT NULL,
                hook text NOT NULL, headline text NOT NULL, description text NOT NULL,
                cta varchar(64) NOT NULL, instruction text NOT NULL,
                model_name varchar(64) NOT NULL, prompt_version varchar(32) NOT NULL,
                batch_id uuid, batch_position integer, status varchar(20) NOT NULL,
                created_by_id bigint
            )
        """).format(sql.Identifier(schema, VARIATION_TABLE)))


def _insert_variation(schema, creator, row_id=91001, *, created_at=None, project_id=None, is_deleted=False):
    created_at = created_at or timezone.now()
    values = {
        "id": row_id, "slug": f"export-variation-{row_id}",
        "created_at": created_at, "updated_at": created_at, "is_deleted": is_deleted,
        "project_id": project_id, "creative_id": None,
        "source_mode": "custom", "source_ref": "",
        "hook": f"{schema} hook", "headline": f"{schema} headline",
        "description": f"{schema} description", "cta": "Learn more",
        "instruction": "Export fixture", "model_name": "qwen3:4b",
        "prompt_version": "v1", "batch_id": None, "batch_position": None,
        "status": "draft", "created_by_id": creator.id,
    }
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(schema, VARIATION_TABLE),
                sql.SQL(", ").join(map(sql.Identifier, values)),
                sql.SQL(", ").join(sql.Placeholder() for _ in values),
            ),
            list(values.values()),
        )
    return values


@pytest.fixture
def tenant_variation(user):
    schema = slug_to_schema_name(user.organization.slug)
    _create_variation_table(schema)
    row = _insert_variation(schema, user)
    return {"id": row["id"], "hook": row["hook"], "source_schema": schema}


@pytest.fixture
def selected_tenant_project(user):
    with tenant_schema_context(slug_to_schema_name(user.organization.slug)):
        project = Project.objects.create(
            name="Tenant export project", organization=user.organization, owner=user,
        )
    user.active_project_id = project.id
    user.current_organization_id = user.organization_id
    user.save(update_fields=["active_project", "current_organization"])
    return project


@pytest.mark.django_db
def test_variation_export_combines_sources_and_applies_one_limit(user, user2, project, monkeypatch):
    long_org = Organization.objects.create(
        name="Long export organization", slug="export-long-" + "x" * 70,
    )
    tenant_projects = {}
    for org in (user.organization, long_org):
        schema = slug_to_schema_name(org.slug)[:63]
        with tenant_schema_context(schema):
            tenant_projects[schema] = Project.objects.create(
                id=project.id, name=f"Project in {schema}", organization=org, owner=user,
            )
    sources = {"public": project, **tenant_projects}
    created_at = timezone.now()
    expected = []
    for schema, source_project in sources.items():
        _create_variation_table(schema)
        for creator, row_id in ((user, 91001), (user2, 91002)):
            row = _insert_variation(
                schema, creator, row_id, created_at=created_at, project_id=source_project.id,
            )
            if creator == user:
                expected.append((schema, row))
    first_tenant = sorted(tenant_projects)[0]
    for row_id, timestamp in ((91003, created_at), (91004, created_at + timezone.timedelta(seconds=1))):
        expected.append((first_tenant, _insert_variation(
            first_tenant, user, row_id, created_at=timestamp, project_id=project.id,
            is_deleted=(row_id == 91003),
        )))
    expected.sort(key=lambda item: (-item[1]["created_at"].timestamp(), item[0], item[1]["id"]))

    section = build_personal_data_payload(user)["sections"][VARIATION_SECTION]
    assert "error" not in section
    assert section["count"] == section["total_count"] == len(expected)
    assert section["truncated"] is False
    assert [(row["source_schema"], row["id"]) for row in section["rows"]] == [
        (schema, row["id"]) for schema, row in expected
    ]
    for actual, (schema, row) in zip(section["rows"], expected):
        for field in row.keys() - {"project_id", "creative_id", "created_by_id"}:
            assert actual[field] == row[field]
        assert actual["created_by"] == {"id": user.id, "email": user.email, "is_subject": True}
        assert actual["project"] == {"id": project.id, "display": sources[schema].name}
        assert actual["creative"] == {"id": None, "display": None}

    monkeypatch.setattr(privacy_export, "EXPORT_ROW_LIMIT", 2)
    limited = build_personal_data_payload(user)["sections"][VARIATION_SECTION]
    assert limited["count"] == limited["row_limit"] == 2
    assert limited["total_count"] == len(expected)
    assert limited["truncated"] is True
    assert limited["rows"] == section["rows"][:2]


@pytest.mark.django_db
def test_variation_export_skips_missing_schemas_and_variation_tables(user):
    # The fixture organization has a schema but no Studio table; bypass provisioning for another.
    _create_variation_table("public")
    _insert_variation("public", user)
    Organization.objects.bulk_create([
        Organization(name="Missing export schema", slug="export-missing-schema"),
    ])

    section = build_personal_data_payload(user)["sections"][VARIATION_SECTION]

    assert [row["source_schema"] for row in section["rows"]] == ["public"]


@pytest.mark.django_db
def test_variation_export_reports_a_broken_source_without_database_details(user, caplog):
    _create_variation_table("public")
    _insert_variation("public", user)
    schema = slug_to_schema_name(user.organization.slug)
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE TABLE {} (id bigint PRIMARY KEY)").format(
            sql.Identifier(schema, VARIATION_TABLE),
        ))

    # No test-owned savepoint around the call: the collector must recover itself.
    section = build_personal_data_payload(user)["sections"][VARIATION_SECTION]

    assert section == {"error": "Variation export failed", "rows": []}
    assert "does not exist" in caplog.text
    assert Organization.objects.filter(pk=user.organization_id).exists()


@pytest.mark.django_db
def test_personal_data_export_includes_user_rows_across_apps(user, project):
    ProjectMember.objects.get_or_create(user=user, project=project, defaults={"role": "owner"})
    task = Task.objects.create(
        summary="GDPR export task",
        project=project,
        owner=user,
        current_approver=user,
        created_by=user,
        type="execution",
    )
    chat = Chat.objects.create(project=project, type=ChatType.GROUP, name="GDPR Chat", created_by=user)
    ChatParticipant.objects.create(chat=chat, user=user)
    Decision.objects.create(project=project, title="GDPR Decision", author=user, project_seq=1)
    meeting_type = MeetingTypeDefinition.objects.create(project=project, slug="sync", label="Sync")
    meeting = Meeting.objects.create(
        project=project,
        type_definition=meeting_type,
        title="GDPR Meeting",
        objective="Review export scope",
    )
    ParticipantLink.objects.create(meeting=meeting, user=user, role="Owner")

    payload = build_personal_data_payload(user)
    sections = payload["sections"]

    assert "core.customuser" in sections
    assert "core.projectmember" in sections
    assert any(row["id"] == task.id for row in sections["task.task"]["rows"])
    assert any(row["id"] == chat.id for row in sections["chat.chat"]["rows"])
    assert "chat.chatparticipant" in sections
    assert "decision.decision" in sections
    assert "meetings.participantlink" in sections
    assert payload["manifest"]["section_count"] >= 6


@pytest.mark.django_db
def test_personal_data_export_preserves_unscoped_project_ids(user, selected_tenant_project):
    Project.objects.create(
        id=selected_tenant_project.id, name="Unrelated public project",
        organization=user.organization, owner=user,
    )
    AgentSession.objects.create(user=user, project_id=selected_tenant_project.id)

    payload = build_personal_data_payload(user)
    expected_project = {"id": selected_tenant_project.id, "display": None}

    assert payload["account"]["active_project"] == expected_project
    assert payload["sections"]["core.customuser"]["rows"][0]["active_project"] == expected_project
    assert payload["sections"]["agent.agentsession"]["rows"][0]["project"] == expected_project
    user.refresh_from_db()
    assert user.active_project_id == selected_tenant_project.id


@pytest.mark.django_db
def test_data_export_zip_and_signed_download_expire_after_24_hours(
    user, tenant_variation, selected_tenant_project, django_capture_on_commit_callbacks,
):
    client = APIClient()
    client.force_authenticate(user=user)
    assert not Project.objects.filter(pk=selected_tenant_project.id).exists()
    with patch("core.views.assemble_personal_data_export.delay") as enqueue_export:
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(reverse("privacy-export-requests"), {"export_format": "json"}, format="json")
    assert response.status_code == 202
    export_request = DataExportRequest.objects.get(pk=response.data["id"])
    enqueue_export.assert_called_once_with(str(export_request.id))
    assemble_data_export_zip(export_request)

    export_request.refresh_from_db()
    assert export_request.status == DataExportRequest.Status.READY
    assert export_request.expires_at is not None
    assert 23.9 <= (export_request.expires_at - timezone.now()).total_seconds() / 3600 <= 24.1

    with zipfile.ZipFile(export_request.file.path) as archive:
        assert "manifest.json" in archive.namelist()
        assert "account.json" in archive.namelist()
        expected_project = {"id": selected_tenant_project.id, "display": None}
        assert json.loads(archive.read("account.json"))["active_project"] == expected_project
        account_section = json.loads(archive.read("data/core.customuser.json"))
        assert [(row["id"], row["active_project"]) for row in account_section["rows"]] == [(user.id, expected_project)]
        section = json.loads(archive.read(f"data/{VARIATION_SECTION}.json"))
        assert "error" not in section
        assert [{key: row[key] for key in tenant_variation} for row in section["rows"]] == [tenant_variation]

    detail_url = reverse("privacy-export-detail", kwargs={"export_id": export_request.id})
    detail_response = client.get(detail_url)
    assert detail_response.status_code == 200
    assert detail_response.data["download_url"]

    download_client = APIClient()
    with django_capture_on_commit_callbacks(execute=True):
        download_response = download_client.get(detail_response.data["download_url"])
        assert download_response.status_code == 200

        replay_response = download_client.get(detail_response.data["download_url"])
        assert replay_response.status_code == 403

        detail_response = client.get(detail_url)
        export_request.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        export_request.save(update_fields=["expires_at", "updated_at"])
        expired_response = download_client.get(detail_response.data["download_url"])
        assert expired_response.status_code == 403

    events = AuditEvent.objects.filter(
        target_type="data_export_request", target_id=str(export_request.id),
    ).order_by("event_type")
    assert list(events.values_list("event_type", "actor_id", "organization_id", "project_id")) == [
        ("privacy.data_export.downloaded", user.id, user.organization_id, None),
        ("privacy.data_export.requested", user.id, user.organization_id, None),
    ]


@pytest.mark.django_db
def test_data_export_zip_can_be_generated_as_csv(user, tenant_variation):
    export_request = DataExportRequest.objects.create(
        user=user,
        export_format=DataExportRequest.ExportFormat.CSV,
    )
    assemble_data_export_zip(export_request)

    export_request.refresh_from_db()
    assert export_request.status == DataExportRequest.Status.READY
    assert export_request.metadata["export_format"] == "csv"

    with zipfile.ZipFile(export_request.file.path) as archive:
        names = archive.namelist()
        assert "manifest.csv" in names
        assert "account.csv" in names
        assert "manifest.json" not in names
        assert any(name.endswith(".csv") for name in names)
        rows = list(csv.DictReader(io.StringIO(archive.read(f"data/{VARIATION_SECTION}.csv").decode())))
        assert [{key: row[key] for key in tenant_variation} for row in rows] == [
            {key: str(value) for key, value in tenant_variation.items()}
        ]
