import json
import logging
import os

from django.conf import settings as django_settings
from django.db.models import Max, Q, OuterRef, Prefetch, Subquery
from django.http import StreamingHttpResponse
from django.utils.translation import activate as activate_language
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from stripe_meta.exceptions import QuotaError
from stripe_meta.services import check_quota_or_402, resolve_charging_org


class EventStreamRenderer(BaseRenderer):
    """Renderer that accepts text/event-stream for SSE endpoints."""
    media_type = 'text/event-stream'
    format = 'event-stream'
    charset = 'utf-8'

    def render(self, data, accepted_media_type=None, renderer_context=None):
        # Error responses (DRF Response) fall through here; serialize as JSON.
        if data is None:
            return b''
        return json.dumps(data).encode('utf-8')

from core.models import Project
from core.slug_mixins import resolve_project_pk, SlugLookupViewSetMixin, resolve_lookup_kwargs
from core.services.file_parser import parse_file_to_json, FileParseError
from spreadsheet.access import accessible_projects
from spreadsheet.providers import (
    SpreadsheetDataProvider,
    SpreadsheetAccessError,
    AiAnalysisDisabled,
)
from .models import (
    AgentSession, AgentMessage, AgentWorkflowDefinition,
    AgentWorkflowStep, AgentWorkflowRun, AgentStepExecution,
    AgentWorkflowTemplate, WorkflowTriggerLog,
)
from .serializers import (
    AgentSessionListSerializer,
    AgentSessionDetailSerializer,
    AgentMessageSerializer,
    ChatInputSerializer,
    UploadAnalyzeInputSerializer,
    AgentWorkflowDefinitionListSerializer,
    AgentWorkflowDefinitionDetailSerializer,
    AgentWorkflowStepSerializer,
    AgentStepExecutionSerializer,
    AgentWorkflowRunSerializer,
    StepReorderSerializer,
    AgentWorkflowTemplateSerializer,
    WorkflowTriggerLogSerializer,
    TriggerConfigUpdateSerializer,
)
from .generation_registry import GenerationValidationError, get_catalog, normalize_generation_outputs
from .services import AgentOrchestrator
from . import data_service

logger = logging.getLogger(__name__)


class EnglishResponseMixin:
    """Force English for DRF validation messages in all agent views."""
    def initial(self, request, *args, **kwargs):
        activate_language('en')
        super().initial(request, *args, **kwargs)


def _get_project_for_user(user, project_id):
    """Resolve a project by id if the user may access it.

    Uses ``spreadsheet.access.accessible_projects`` — the canonical policy
    (owner OR *active* membership OR org-admin), shared with the spreadsheet
    HTTP/WS paths. This is stricter than the old bare ``ProjectMember`` check,
    which ignored ``is_active``.
    """
    if project_id is None:
        return None
    try:
        return accessible_projects(user).filter(id=project_id, is_deleted=False).first()
    except (TypeError, ValueError):
        return None


def _get_user_project(request):
    """Get the active project for the current user, with an access check."""
    raw_project_id = request.headers.get('X-Project-Id') or request.query_params.get('project_id')
    if raw_project_id:
        pk = resolve_project_pk(raw_project_id)
        if pk is None:
            return None
        return accessible_projects(request.user).filter(
            id=pk, is_deleted=False
        ).first()
    try:
        active = request.user.active_project
    except Project.DoesNotExist:
        return None
    if active is None:
        return None
    return (
        active
        if accessible_projects(request.user).filter(id=active.id).exists()
        else None
    )


class AgentSessionViewSet(EnglishResponseMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = None
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_serializer_class(self):
        if self.action == 'list':
            return AgentSessionListSerializer
        return AgentSessionDetailSerializer

    def get_queryset(self):
        project = _get_user_project(self.request)
        qs = AgentSession.objects.filter(
            user=self.request.user,
            is_deleted=False,
        )
        if project:
            qs = qs.filter(project=project)
        search = (self.request.query_params.get('search') or '').strip()
        if search:
            qs = qs.filter(title__icontains=search)
        qs = qs.order_by('-is_pinned', '-created_at')
        if self.action == 'list':
            last_assistant_sub = AgentMessage.objects.filter(
                session_id=OuterRef('pk'),
                role='assistant',
                is_deleted=False,
            ).order_by('-created_at').values('created_at')[:1]
            qs = qs.annotate(_last_assistant_at=Subquery(last_assistant_sub))
            return qs[:50]
        if self.action == 'retrieve':
            qs = qs.prefetch_related(
                Prefetch(
                    'messages',
                    queryset=AgentMessage.objects.filter(is_deleted=False).order_by('created_at'),
                )
            )
        return qs

    def perform_create(self, serializer):
        project = None
        body_project_id = self.request.data.get('project_id')
        if body_project_id is not None:
            project = _get_project_for_user(self.request.user, body_project_id)
        if not project:
            project = _get_user_project(self.request)
        if not project:
            raise ValidationError(
                {'project_id': 'A valid project you are a member of is required.'}
            )
        serializer.save(user=self.request.user, project=project)

    def perform_destroy(self, instance):
        instance.is_deleted = True
        instance.save()

    @action(detail=True, methods=['post'], url_path='messages')
    def messages(self, request, pk=None):
        """
        Append a message to a session's transcript without invoking the agent
        pipeline. Used by flows that generate their own results out-of-band
        (e.g. NL pattern/pivot generation) so the sidebar history is preserved
        when the session is reopened.
        """
        session = self.get_object()
        role = request.data.get('role')
        content = request.data.get('content')
        if role not in {'user', 'assistant', 'system'}:
            return Response({'detail': 'Invalid role.'}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(content, str) or not content.strip():
            return Response({'detail': 'content is required.'}, status=status.HTTP_400_BAD_REQUEST)

        message_type = request.data.get('message_type') or 'text'
        valid_types = {c[0] for c in AgentMessage.MESSAGE_TYPE_CHOICES}
        if message_type not in valid_types:
            message_type = 'text'
        metadata = request.data.get('data')
        if not isinstance(metadata, dict):
            metadata = {}

        message = AgentMessage.objects.create(
            session=session,
            role=role,
            content=content,
            message_type=message_type,
            metadata=metadata,
        )
        serializer = AgentMessageSerializer(message)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ChatView(EnglishResponseMixin, APIView):
    permission_classes = [IsAuthenticated]
    renderer_classes = [EventStreamRenderer, JSONRenderer]

    def post(self, request, session_id):
        serializer = ChatInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            session = AgentSession.objects.get(
                id=session_id,
                user=request.user,
                is_deleted=False,
            )
        except AgentSession.DoesNotExist:
            return Response(
                {"detail": "Session not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        message_text = serializer.validated_data['message']
        spreadsheet_id = serializer.validated_data.get('spreadsheet_id')
        sheet_id = serializer.validated_data.get('sheet_id')
        csv_filename = serializer.validated_data.get('csv_filename')
        file_id = serializer.validated_data.get('file_id')
        action = serializer.validated_data.get('action')
        calendar_context = serializer.validated_data.get('calendar_context')
        draft_context = serializer.validated_data.get('draft_context')
        workflow_id = serializer.validated_data.get('workflow_id')
        column_mapping = serializer.validated_data.get('column_mapping')
        user_context = serializer.validated_data.get('user_context') or None
        approval_id = serializer.validated_data.get('approval_id')
        approval_decision = serializer.validated_data.get('approval_decision')
        approval_draft = serializer.validated_data.get('approval_draft')
        reviewed_anomalies = serializer.validated_data.get('reviewed_anomalies')

        # Pre-flight quota gate for plain user messages (no pipeline action):
        # hard-block Free orgs at the cap *before* persisting or streaming so that
        # ordinary chat — including chitchat that never reaches call_llm — surfaces
        # the upgrade modal. Action requests (create_tasks, generate_miro, …) are
        # exempt here: those that do call the LLM are still covered by the in-stream
        # QuotaError handler below; those that only persist drafts stay usable.
        # The probe size of 1 token just asks "is there any headroom left?" — Team
        # plans always pass (metered overage), only Free at the cap is blocked.
        if not action:
            try:
                charging_org = resolve_charging_org(session)
            except QuotaError as exc:  # orphan project (PROJECT_HAS_NO_ORG), fail-closed
                return Response(
                    {'code': exc.code, 'message': exc.message, **exc.payload},
                    status=status.HTTP_409_CONFLICT,
                )
            allowed, quota_payload = check_quota_or_402(charging_org, 1)
            if not allowed:
                return Response(quota_payload, status=status.HTTP_402_PAYMENT_REQUIRED)

        should_persist_user_message = action not in {
            'start_follow_up', 'cancel_follow_up',
            'create_decisions', 'create_tasks', 'generate_miro',
            'distribute_message', 'confirm_columns', 'confirm_anomalies',
            'resume_workflow', 'resolve_external_approval',
        }

        # Auto-generate title from first real user message
        if should_persist_user_message and not session.title and message_text:
            session.title = message_text[:100]
            session.save(update_fields=['title'])

        if should_persist_user_message:
            AgentMessage.objects.create(
                session=session,
                role='user',
                content=message_text,
                metadata={
                    'spreadsheet_id': spreadsheet_id,
                    'action': action,
                },
            )

        project = session.project
        orchestrator = AgentOrchestrator(
            user=request.user,
            project=project,
            session=session,
        )

        def event_stream():
            assistant_content_parts = []
            assistant_metadata = {}
            last_message_type = 'text'

            def _flush_message():
                """Save accumulated content as an assistant message and reset state."""
                nonlocal assistant_content_parts, assistant_metadata, last_message_type
                body = '\n'.join(p for p in assistant_content_parts if p)
                if body:
                    AgentMessage.objects.create(
                        session=session,
                        role='assistant',
                        content=body,
                        message_type=last_message_type,
                        metadata=assistant_metadata,
                    )
                assistant_content_parts = []
                assistant_metadata = {}
                last_message_type = 'text'

            try:
                for chunk in orchestrator.handle_message(
                    message_text,
                    spreadsheet_id=spreadsheet_id,
                    sheet_id=sheet_id,
                    csv_filename=csv_filename,
                    action=action,
                    file_id=file_id,
                    calendar_context=calendar_context,
                    draft_context=draft_context,
                    workflow_id=workflow_id,
                    column_mapping=column_mapping,
                    approval_id=approval_id,
                    approval_decision=approval_decision,
                    approval_draft=approval_draft,
                    user_context=user_context,
                    reviewed_anomalies=reviewed_anomalies,
                ):
                    chunk_type = chunk.get('type', 'text')
                    content = chunk.get('content', '')
                    data = chunk.get('data')

                    # Save calendar_invite as a separate message so it can be
                    # restored independently from the preceding calendar answer.
                    if chunk_type == 'calendar_invite':
                        _flush_message()
                        sse_data = json.dumps(chunk, default=str)
                        yield f"data: {sse_data}\n\n"
                        if content:
                            AgentMessage.objects.create(
                                session=session,
                                role='assistant',
                                content=content,
                                message_type='calendar_invite',
                                metadata={},
                            )
                        continue

                    if chunk_type == 'approval_request':
                        _flush_message()
                        sse_data = json.dumps(chunk, default=str)
                        yield f"data: {sse_data}\n\n"
                        AgentMessage.objects.create(
                            session=session,
                            role='assistant',
                            content=content or 'Approval required.',
                            message_type='approval_request',
                            metadata=data or {},
                        )
                        continue

                    if chunk_type == 'confirmation_request':
                        _flush_message()
                        sse_data = json.dumps(chunk, default=str)
                        yield f"data: {sse_data}\n\n"
                        if content:
                            AgentMessage.objects.create(
                                session=session,
                                role='assistant',
                                content=content,
                                message_type='confirmation_request',
                                metadata=data or {},
                            )
                        continue

                    if chunk_type == 'decision_draft':
                        _flush_message()
                        sse_data = json.dumps(chunk, default=str)
                        yield f"data: {sse_data}\n\n"
                        if content or data:
                            AgentMessage.objects.create(
                                session=session,
                                role='assistant',
                                content=content or 'Decision drafts created.',
                                message_type='decision_draft',
                                metadata=data or {},
                            )
                        continue

                    if chunk_type == 'spreadsheet_summary':
                        _flush_message()
                        sse_data = json.dumps(chunk, default=str)
                        yield f"data: {sse_data}\n\n"
                        if content:
                            AgentMessage.objects.create(
                                session=session,
                                role='assistant',
                                content=content,
                                message_type='text',
                                metadata={'kind': 'spreadsheet_summary', **(data or {})},
                            )
                        continue

                    if chunk_type == 'spreadsheet_anomalies':
                        _flush_message()
                        sse_data = json.dumps(chunk, default=str)
                        yield f"data: {sse_data}\n\n"
                        if content or data:
                            AgentMessage.objects.create(
                                session=session,
                                role='assistant',
                                content=content or 'Anomaly analysis complete.',
                                message_type='analysis',
                                metadata=data or {},
                            )
                        continue

                    # Miro status is persisted separately (_create_agent_status_message in the
                    # orchestrator). Do not merge its text into the prior assistant bubble or
                    # the final flush — that would duplicate the same line in chat history.
                    if chunk_type == 'miro_status':
                        _flush_message()
                        sse_data = json.dumps(chunk, default=str)
                        yield f"data: {sse_data}\n\n"
                        continue

                    # Skip internal signalling events from content accumulation.
                    # 'anomalies_confirmed' updates the existing analysis message
                    # in-place (see confirm_anomalies), so it must not be persisted
                    # as a second anomaly card here.
                    if chunk_type not in ('done', 'calendar_updated', 'anomalies_confirmed'):
                        if content:
                            assistant_content_parts.append(content)
                        last_message_type = chunk_type
                        if data:
                            assistant_metadata.update(data)

                    sse_data = json.dumps(chunk, default=str)
                    yield f"data: {sse_data}\n\n"

                    if chunk_type == 'done':
                        _flush_message()
            except QuotaError as exc:
                _flush_message()
                quota_payload = json.dumps(
                    {'code': exc.code, 'message': exc.message, **exc.payload}
                )
                yield f"data: {quota_payload}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
            except Exception:
                logger.exception("Error during agent SSE stream")
                _flush_message()
                error_payload = json.dumps({
                    "type": "error",
                    "content": "An internal error occurred. Please try again.",
                })
                yield f"data: {error_payload}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"

        response = StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream',
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response


class SpreadsheetListView(EnglishResponseMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        project = _get_user_project(request)
        if not project:
            return Response(
                {"detail": "No active project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = SpreadsheetDataProvider(request.user).list_project_spreadsheets(project)
        return Response(result)


class AiConsentView(EnglishResponseMixin, APIView):
    """Per-user, per-spreadsheet consent to send a spreadsheet's contents to an
    external AI service. One-time per spreadsheet (see
    :class:`spreadsheet.models.SpreadsheetAiConsent`).

    Target it by ``spreadsheet_id`` or by ``sheet_id`` (a sheet resolves to its
    spreadsheet) — callers that only hold a sheet id (pattern/pivot generation)
    use the latter.

    GET  ?spreadsheet_id=<id> | ?sheet_id=<id>  -> {"consented": bool, "consented_at": iso|null}
    POST {"spreadsheet_id": <id>} | {"sheet_id": <id>}  -> records consent (idempotent)
    """
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _int_param(request, *names):
        for name in names:
            raw = (
                request.data.get(name)
                or request.query_params.get(name)
                or request.headers.get('X-' + name.replace('_', '-').title())
            )
            if raw is not None:
                try:
                    return name, int(raw)
                except (TypeError, ValueError):
                    return name, None
        return None, None

    def _spreadsheet(self, request):
        name, pk = self._int_param(request, 'spreadsheet_id', 'sheet_id')
        if pk is None:
            return None
        if name == 'sheet_id':
            from spreadsheet.access import accessible_sheets

            sheet = (
                accessible_sheets(request.user)
                .filter(id=pk)
                .select_related('spreadsheet')
                .first()
            )
            return sheet.spreadsheet if sheet else None
        from spreadsheet.access import accessible_spreadsheets

        return accessible_spreadsheets(request.user).filter(id=pk).first()

    def get(self, request):
        from spreadsheet.models import SpreadsheetAiConsent

        spreadsheet = self._spreadsheet(request)
        if spreadsheet is None:
            return Response({"detail": "Spreadsheet not found."}, status=status.HTTP_404_NOT_FOUND)
        row = SpreadsheetAiConsent.objects.filter(
            user=request.user, spreadsheet=spreadsheet
        ).first()
        return Response({
            "consented": row is not None,
            "consented_at": row.created_at.isoformat() if row else None,
        })

    def post(self, request):
        from spreadsheet.models import SpreadsheetAiConsent
        from spreadsheet.providers import record_ai_consent

        spreadsheet = self._spreadsheet(request)
        if spreadsheet is None:
            return Response({"detail": "Spreadsheet not found."}, status=status.HTTP_404_NOT_FOUND)
        existed = SpreadsheetAiConsent.objects.filter(
            user=request.user, spreadsheet=spreadsheet
        ).exists()
        row = record_ai_consent(request.user, spreadsheet)
        if not existed:
            from core.services.audit_events import safe_emit_audit_event

            safe_emit_audit_event(
                event_type='agent.spreadsheet.ai_consent_granted',
                actor=request.user,
                organization=getattr(spreadsheet.project, 'organization', None),
                project=spreadsheet.project,
                target_type='spreadsheet',
                target_id=spreadsheet.id,
                request=request,
            )
        return Response({
            "consented": True,
            "consented_at": row.created_at.isoformat(),
        })


class DataReportListView(EnglishResponseMixin, APIView):
    """GET /api/agent/data/reports/ — list imported CSV files."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        project = _get_user_project(request)
        if not project:
            return Response(
                {"detail": "No active project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reports = data_service.list_reports(project)
        return Response(reports)


class DataReportDetailView(EnglishResponseMixin, APIView):
    """GET/DELETE /api/agent/data/reports/<uuid:file_id>/"""
    permission_classes = [IsAuthenticated]

    def get(self, request, file_id):
        project = _get_user_project(request)
        if not project:
            return Response(
                {"detail": "No active project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = data_service.get_report_data(file_id, project)
        if data is None:
            return Response(
                {"detail": "Report not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(data)

    def delete(self, request, file_id):
        project = _get_user_project(request)
        if not project:
            return Response(
                {"detail": "No active project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        deleted = data_service.delete_report(file_id, project)
        if not deleted:
            return Response(
                {"detail": "File not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class DataUploadView(EnglishResponseMixin, APIView):
    """POST /api/agent/data/upload/ — upload a CSV file."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        project = _get_user_project(request)
        if not project:
            return Response(
                {"detail": "No active project."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        file = request.FILES.get('file')
        if not file:
            return Response(
                {"detail": "No file provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        allowed_exts = ('.csv', '.xlsx', '.xls')
        if not file.name.lower().endswith(allowed_exts):
            return Response(
                {"detail": "Only CSV and Excel files are accepted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_size = 10 * 1024 * 1024  # 10MB
        if file.size > max_size:
            return Response(
                {"detail": "File too large (max 10MB)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = data_service.save_uploaded_file(file, request.user, project)
        if result is None:
            return Response(
                {"detail": "Failed to save file."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(result, status=status.HTTP_201_CREATED)


class FileUploadAnalyzeView(EnglishResponseMixin, APIView):
    """POST /api/agent/upload-analyze/ — upload a file and stream analysis."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    renderer_classes = [EventStreamRenderer, JSONRenderer]

    def post(self, request):
        project = _get_user_project(request)
        if not project:
            return Response(
                {"detail": "No active project."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        file = request.FILES.get('file')
        if not file:
            return Response(
                {"detail": "No file provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        allowed_exts = ('.csv', '.xlsx', '.xls')
        if not file.name.lower().endswith(allowed_exts):
            return Response(
                {"detail": "Only CSV and Excel files are accepted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        max_size = 10 * 1024 * 1024  # 10MB
        if file.size > max_size:
            return Response(
                {"detail": "File too large (max 10MB)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Save file
        result = data_service.save_uploaded_file(file, request.user, project)
        if result is None:
            return Response(
                {"detail": "Failed to save file."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        spreadsheet_import = None
        spreadsheet_import_error = None
        truncation_notice = None
        csv_dir = data_service._get_csv_dir()
        filepath = os.path.join(csv_dir, os.path.basename(result['filename']))
        try:
            # Bounded by the SPREADSHEET_AI_MAX_* settings; a genuinely large
            # import is the deferred background job.
            parsed = parse_file_to_json(filepath, result['original_filename'])
            _hit = parsed.get('limits_hit') or {}
            if _hit.get('rows') or _hit.get('cols') or _hit.get('cells'):
                truncation_notice = (
                    "This file is large — imported the first "
                    f"{getattr(django_settings, 'SPREADSHEET_AI_MAX_ROWS', 500)} rows / "
                    f"{getattr(django_settings, 'SPREADSHEET_AI_MAX_COLS', 50)} columns. "
                    "Split the sheet for a full import."
                )
            spreadsheet_import = SpreadsheetDataProvider(
                request.user
            ).create_from_parsed_upload(
                project, parsed, result['original_filename']
            )
            from .models import ImportedCSVFile

            ImportedCSVFile.objects.filter(id=result['id']).update(
                spreadsheet_id=spreadsheet_import['spreadsheet_id'],
            )
            from core.services.audit_events import safe_emit_audit_event

            safe_emit_audit_event(
                event_type='agent.spreadsheet.upload_analyzed',
                actor=request.user,
                organization=getattr(project, 'organization', None),
                project=project,
                target_type='spreadsheet',
                target_id=spreadsheet_import['spreadsheet_id'],
                context={
                    'row_count': result.get('row_count'),
                    'column_count': result.get('column_count'),
                    'original_filename': result.get('original_filename'),
                    'provider': 'gemini',
                    'model': 'gemini-2.5-flash-lite',
                },
                request=request,
            )
        except AiAnalysisDisabled as exc:
            return Response(
                {"detail": exc.message, "code": exc.code},
                status=status.HTTP_403_FORBIDDEN,
            )
        except FileParseError as exc:
            spreadsheet_import_error = f"Could not read the file: {exc}"[:200]
        except Exception as exc:
            logger.exception(
                "FileUploadAnalyzeView spreadsheet import failed for file_id=%s",
                result['id'],
            )
            # Do not let the import fail silently: the file is saved but has no
            # spreadsheet, so surface the failure to the client instead of
            # continuing as if the data were imported.
            spreadsheet_import_error = str(exc)[:200] or 'Unknown error'

        upload_fields = UploadAnalyzeInputSerializer(data=request.data)
        upload_fields.is_valid(raise_exception=True)
        try:
            generation_outputs = normalize_generation_outputs(
                upload_fields.validated_data.get('generation_outputs')
            )
        except GenerationValidationError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create or reuse session
        session_id = upload_fields.validated_data.get('session_id') or request.data.get('session_id')
        user_context = request.data.get('user_context') or None
        if session_id:
            try:
                session = AgentSession.objects.get(
                    id=session_id, user=request.user, is_deleted=False,
                )
            except AgentSession.DoesNotExist:
                session = AgentSession.objects.create(
                    user=request.user, project=project,
                    title=f"Analysis: {result['original_filename']}",
                )
        else:
            session = AgentSession.objects.create(
                user=request.user, project=project,
                title=f"Analysis: {result['original_filename']}",
            )

        user_message_metadata = {
            'original_filename': result['original_filename'],
            'file_id': result['id'],
        }
        if spreadsheet_import:
            user_message_metadata.update({
                'spreadsheet_id': spreadsheet_import['spreadsheet_id'],
                'sheet_id': spreadsheet_import['sheet_id'],
                'project_id': spreadsheet_import['project_id'],
                'url': spreadsheet_import['url'],
            })

        AgentMessage.objects.create(
            session=session,
            role='user',
            content=f'Uploaded "{result["original_filename"]}"',
            metadata=user_message_metadata,
        )

        orchestrator = AgentOrchestrator(
            user=request.user, project=project, session=session,
        )

        imported_spreadsheet_id = (
            spreadsheet_import['spreadsheet_id'] if spreadsheet_import else None
        )

        def event_stream():
            # Immediately emit file_uploaded event
            file_event_data = {
                "file_id": result['id'],
                "filename": result['filename'],
                "original_filename": result['original_filename'],
                "row_count": result['row_count'],
                "column_count": result['column_count'],
                "generation_outputs": generation_outputs,
            }
            if spreadsheet_import:
                file_event_data.update({
                    "spreadsheet_id": spreadsheet_import['spreadsheet_id'],
                    "sheet_id": spreadsheet_import['sheet_id'],
                    "project_id": spreadsheet_import['project_id'],
                    "url": spreadsheet_import['url'],
                })
            if truncation_notice:
                file_event_data["truncation_notice"] = truncation_notice
            file_event = {
                "type": "file_uploaded",
                "content": f"Uploaded \"{result['original_filename']}\" ({result['row_count']} rows, {result['column_count']} columns).",
                "data": file_event_data,
            }
            yield f"data: {json.dumps(file_event)}\n\n"
            if truncation_notice:
                yield f"data: {json.dumps({'type': 'text', 'content': truncation_notice})}\n\n"

            # Spreadsheet import failed: the upload is saved but there is no
            # spreadsheet to analyse. Tell the client and stop rather than
            # silently proceeding as though the data were imported.
            if spreadsheet_import_error is not None:
                error_event = {
                    "type": "error",
                    "content": (
                        f"Import failed: {spreadsheet_import_error}"
                    ),
                }
                yield f"data: {json.dumps(error_event)}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'data': {'session_id': str(session.id)}})}\n\n"
                return

            # Route through the workflow engine (column detection + analysis).
            assistant_content_parts = []
            assistant_metadata = {
                "file_id": result['id'],
                "generation_outputs": generation_outputs,
            }
            if spreadsheet_import:
                assistant_metadata.update({
                    "spreadsheet_id": spreadsheet_import['spreadsheet_id'],
                    "sheet_id": spreadsheet_import['sheet_id'],
                    "project_id": spreadsheet_import['project_id'],
                    "url": spreadsheet_import['url'],
                })
            last_message_type = 'text'

            try:
                for chunk in orchestrator.handle_message(
                    "",
                    file_id=result['id'],
                    spreadsheet_id=imported_spreadsheet_id,
                    generation_outputs=generation_outputs,
                    user_context=user_context):
                    chunk_type = chunk.get('type', 'text')
                    content = chunk.get('content', '')
                    data = chunk.get('data')

                    if chunk_type == 'done':
                        # Intercept done event to attach session_id for the frontend.
                        break

                    if content:
                        assistant_content_parts.append(content)
                    last_message_type = chunk_type
                    if data:
                        assistant_metadata.update(data)

                    yield f"data: {json.dumps(chunk, default=str)}\n\n"
            except QuotaError as exc:
                quota_payload = json.dumps(
                    {'code': exc.code, 'message': exc.message, **exc.payload}
                )
                yield f"data: {quota_payload}\n\n"
            except GenerationValidationError as exc:
                logger.warning("FileUploadAnalyzeView validation error: %s", exc)
                yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"
            except Exception:
                logger.exception("FileUploadAnalyzeView workflow error")
                yield f"data: {json.dumps({'type': 'error', 'content': 'An internal error occurred. Please try again.'})}\n\n"

            # Save accumulated assistant message
            if assistant_content_parts:
                AgentMessage.objects.create(
                    session=session,
                    role='assistant',
                    content='\n'.join(assistant_content_parts),
                    message_type=last_message_type,
                    metadata=assistant_metadata,
                )

            # Done event with session_id so the frontend can persist the session.
            done_event = {
                "type": "done",
                "data": {"session_id": str(session.id)},
            }
            yield f"data: {json.dumps(done_event)}\n\n"

        response = StreamingHttpResponse(
            event_stream(),
            content_type='text/event-stream',
        )
        response['Cache-Control'] = 'no-cache'
        response['X-Accel-Buffering'] = 'no'
        return response


class DataReportSummaryView(EnglishResponseMixin, APIView):
    """GET /api/agent/data/reports/summary/ — aggregated KPI data."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        project = _get_user_project(request)
        if not project:
            return Response(
                {"detail": "No active project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        summary = data_service.get_reports_summary(project)
        if summary is None:
            return Response({"detail": "No data available."}, status=status.HTTP_200_OK)
        return Response(summary)


class AnomalyLatestView(EnglishResponseMixin, APIView):
    """GET /api/agent/anomalies/latest/ — latest anomalies from most recent analysis."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        project = _get_user_project(request)
        if not project:
            return Response(
                {"detail": "No active project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Find the most recent assistant message that has anomalies in metadata
        msg = (
            AgentMessage.objects
            .filter(
                session__project=project,
                session__user=request.user,
                role='assistant',
            )
            .filter(metadata__has_key='anomalies')
            .order_by('-created_at')
            .first()
        )
        if not msg or not msg.metadata.get('anomalies'):
            return Response([])
        return Response(msg.metadata['anomalies'])


class AgentWorkflowDefinitionViewSet(SlugLookupViewSetMixin, EnglishResponseMixin, viewsets.ModelViewSet):
    """CRUD for workflow definitions. Shows project-level + system-level workflows."""
    permission_classes = [IsAuthenticated]
    pagination_class = None
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_serializer_class(self):
        if self.action == 'list':
            return AgentWorkflowDefinitionListSerializer
        return AgentWorkflowDefinitionDetailSerializer

    def get_queryset(self):
        project = _get_user_project(self.request)
        qs = AgentWorkflowDefinition.objects.filter(is_deleted=False)

        # For list action, exclude template workflows (they should only appear in templates tab)
        # For retrieve action, include template workflows (needed for template preview)
        include_template_workflows = self.action != 'list'

        if project:
            filters = Q(is_system=True) | Q(project=project, is_system=False, created_by=self.request.user)
            if include_template_workflows:
                filters |= Q(project__isnull=True, is_system=False, created_by=self.request.user)  # Template workflows
            qs = qs.filter(filters)
        else:
            filters = Q(is_system=True)
            if include_template_workflows:
                filters |= Q(project__isnull=True, is_system=False, created_by=self.request.user)  # Template workflows
            qs = qs.filter(filters)
        return qs.order_by('-is_system', '-is_default', '-created_at')

    def perform_create(self, serializer):
        project = _get_user_project(self.request)
        serializer.save(
            project=project,
            created_by=self.request.user,
            is_system=False,
        )

    def perform_update(self, serializer):
        if serializer.instance.is_system:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("System workflows cannot be modified.")
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.is_system:
            return Response(
                {"detail": "System workflows cannot be deleted."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Prevent deleting workflows that are used by templates
        if instance.templates.filter(is_deleted=False).exists():
            return Response(
                {"detail": "Cannot delete workflow that is used by active templates. Delete the template first."},
                status=status.HTTP_403_FORBIDDEN,
            )

        instance.is_deleted = True
        instance.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        """Clone a workflow (system or custom) into a new project-scoped draft."""
        source = self.get_object()
        project = _get_user_project(request)
        if not project:
            return Response(
                {'detail': 'An active project is required to duplicate a workflow.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        custom_name = (request.data.get('name') or '').strip()
        new_workflow = _clone_workflow_definition(
            source,
            project=project,
            created_by=request.user,
            name=custom_name or f"{source.name} (Copy)",
            status='draft',
        )
        serializer = AgentWorkflowDefinitionDetailSerializer(new_workflow)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def trigger_manual(self, request, pk=None):
        """POST /api/agent/workflows/{id}/trigger_manual/ - Manually trigger a workflow."""
        from .trigger_handlers import ManualHandler

        workflow = self.get_object()
        project = _get_user_project(request)

        success = ManualHandler.trigger_manual(
            workflow_id=str(workflow.id),
            user=request.user,
            project=project,
        )

        if success:
            return Response({'message': 'Workflow triggered successfully.'})
        else:
            return Response(
                {'detail': 'Failed to trigger workflow.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=['patch'])
    def update_trigger_config(self, request, pk=None):
        """PATCH /api/agent/workflows/{id}/update_trigger_config/ - Update trigger configuration."""
        workflow = self.get_object()

        if workflow.is_system:
            return Response(
                {'detail': 'Cannot modify system workflow triggers.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = TriggerConfigUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if 'trigger_config' in serializer.validated_data:
            workflow.trigger_config = serializer.validated_data['trigger_config']

        workflow.save()

        return Response(AgentWorkflowDefinitionDetailSerializer(workflow).data)


def _get_workflow_or_404(request, workflow_id):
    """Fetch workflow with project-level access check. Returns (workflow, error_response)."""
    try:
        workflow = AgentWorkflowDefinition.objects.get(
            **resolve_lookup_kwargs(workflow_id), is_deleted=False,
        )
    except AgentWorkflowDefinition.DoesNotExist:
        return None, Response(
            {"detail": "Workflow not found."},
            status=status.HTTP_404_NOT_FOUND,
        )
    # System workflows are visible to all authenticated users
    if not workflow.is_system:
        project = _get_user_project(request)
        if workflow.project != project:
            return None, Response(
                {"detail": "Workflow not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
    return workflow, None


class WorkflowStepView(EnglishResponseMixin, APIView):
    """GET list / POST add step for a workflow definition."""
    permission_classes = [IsAuthenticated]

    def get(self, request, workflow_id):
        workflow, err = _get_workflow_or_404(request, workflow_id)
        if err:
            return err
        steps = workflow.steps.filter(is_deleted=False).order_by('order')
        serializer = AgentWorkflowStepSerializer(steps, many=True)
        return Response(serializer.data)

    def post(self, request, workflow_id):
        workflow, err = _get_workflow_or_404(request, workflow_id)
        if err:
            return err
        if workflow.is_system:
            return Response(
                {"detail": "Cannot modify system workflow steps."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = AgentWorkflowStepSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Auto-assign order if not provided
        max_order = workflow.steps.filter(is_deleted=False).aggregate(
            max_order=Max('order')
        )['max_order'] or 0
        serializer.save(
            workflow=workflow,
            order=serializer.validated_data.get('order', max_order + 1),
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def delete(self, request, workflow_id):
        """Delete a step by step_id query param."""
        workflow, err = _get_workflow_or_404(request, workflow_id)
        if err:
            return err
        if workflow.is_system:
            return Response(
                {"detail": "Cannot modify system workflow steps."},
                status=status.HTTP_403_FORBIDDEN,
            )
        step_id = request.query_params.get('step_id')
        if not step_id:
            return Response(
                {"detail": "step_id query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            step = workflow.steps.get(id=step_id, is_deleted=False)
        except AgentWorkflowStep.DoesNotExist:
            return Response(
                {"detail": "Step not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        step.is_deleted = True
        step.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkflowStepDetailView(EnglishResponseMixin, APIView):
    """PATCH a single step within a workflow definition."""
    permission_classes = [IsAuthenticated]

    def patch(self, request, workflow_id, step_id):
        workflow, err = _get_workflow_or_404(request, workflow_id)
        if err:
            return err
        if workflow.is_system:
            return Response(
                {"detail": "Cannot modify system workflow steps."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            step = workflow.steps.get(id=step_id, is_deleted=False)
        except AgentWorkflowStep.DoesNotExist:
            return Response(
                {"detail": "Step not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = AgentWorkflowStepSerializer(step, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class StepReorderView(EnglishResponseMixin, APIView):
    """POST reorder steps within a workflow."""
    permission_classes = [IsAuthenticated]

    def post(self, request, workflow_id):
        workflow, err = _get_workflow_or_404(request, workflow_id)
        if err:
            return err
        if workflow.is_system:
            return Response(
                {"detail": "Cannot modify system workflow steps."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = StepReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        step_ids = serializer.validated_data['step_ids']
        steps = workflow.steps.filter(
            id__in=step_ids, is_deleted=False,
        )
        if steps.count() != len(step_ids):
            return Response(
                {"detail": "Some step IDs are invalid."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from django.db import transaction
        with transaction.atomic():
            # Phase 1: move all reordered steps to a safe high-offset zone so that
            # the final sequential assignment (phase 2) does not conflict with any
            # remaining active steps.  We cannot use negative values because
            # PositiveIntegerField enforces a DB-level CHECK (order >= 0).
            max_existing = (
                workflow.steps.aggregate(m=Max('order'))['m'] or 0
            )
            temp_base = max_existing + len(step_ids) + 1000
            for idx, step_id in enumerate(step_ids):
                AgentWorkflowStep.objects.filter(id=step_id).update(
                    order=temp_base + idx + 1
                )
            # Phase 2: assign final sequential 1-based positions
            for idx, step_id in enumerate(step_ids, start=1):
                AgentWorkflowStep.objects.filter(id=step_id).update(order=idx)

        updated_steps = workflow.steps.filter(is_deleted=False).order_by('order')
        return Response(
            AgentWorkflowStepSerializer(updated_steps, many=True).data,
        )


class WorkflowRunListView(EnglishResponseMixin, APIView):
    """GET recent workflow runs for a workflow definition."""
    permission_classes = [IsAuthenticated]

    def get(self, request, workflow_id):
        workflow, err = _get_workflow_or_404(request, workflow_id)
        if err:
            return err

        try:
            limit = int(request.query_params.get('limit', 10))
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))

        runs = AgentWorkflowRun.objects.filter(
            workflow_definition_id=workflow.id,
            session__user=request.user,
            is_deleted=False,
        ).order_by('-created_at')[:limit]

        return Response(AgentWorkflowRunSerializer(runs, many=True).data)


class WorkflowRunDetailView(EnglishResponseMixin, APIView):
    """GET execution detail for a workflow run, including step executions."""
    permission_classes = [IsAuthenticated]

    def get(self, request, run_id):
        try:
            run = AgentWorkflowRun.objects.get(
                id=run_id,
                session__user=request.user,
                is_deleted=False,
            )
        except AgentWorkflowRun.DoesNotExist:
            return Response(
                {"detail": "Workflow run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        run_data = AgentWorkflowRunSerializer(run).data
        executions = run.step_executions.order_by('step_order')
        run_data['step_executions'] = AgentStepExecutionSerializer(
            executions, many=True,
        ).data
        return Response(run_data)


class GenerationOutputsCatalogView(EnglishResponseMixin, APIView):
    """GET /api/agent/generation-outputs/ — catalog of tickable generation outputs."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({'outputs': get_catalog()})


class AgentConfigStatusView(EnglishResponseMixin, APIView):
    """GET /api/agent/config/status/ — check which API keys are configured."""
    permission_classes = [IsAuthenticated]

    # Mapping of response key -> (settings attr, env var fallback)
    KEY_MAP = {
        'gemini': ('GEMINI_API_KEY', 'GEMINI_API_KEY'),
        'anthropic': ('ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEY'),
    }

    def get(self, request):
        result = {}
        for key, (settings_attr, env_var) in self.KEY_MAP.items():
            val = getattr(django_settings, settings_attr, None) or os.environ.get(env_var, '')
            result[key] = bool(val and val.strip())
        return Response(result)


def _clone_workflow_definition(
    source_workflow,
    *,
    project=None,
    created_by=None,
    name=None,
    status='active',
):
    """
    Clone a workflow definition and all its steps.

    Returns a new AgentWorkflowDefinition with:
    - project as provided (None for template-owned copies)
    - is_system=False
    - is_default=False
    - All steps cloned with same order and config
    """
    from django.db import transaction

    with transaction.atomic():
        # Clone workflow definition
        new_workflow = AgentWorkflowDefinition.objects.create(
            name=name or f"{source_workflow.name} (Copy)",
            description=source_workflow.description,
            project=project,
            is_default=False,
            is_system=False,
            status=status,
            created_by=created_by or source_workflow.created_by,
        )

        # Clone all steps
        source_steps = source_workflow.steps.filter(is_deleted=False).order_by('order')
        for step in source_steps:
            AgentWorkflowStep.objects.create(
                workflow=new_workflow,
                name=step.name,
                step_type=step.step_type,
                order=step.order,
                config=step.config.copy() if step.config else {},
                description=step.description,
            )

        return new_workflow


class AgentWorkflowTemplateViewSet(SlugLookupViewSetMixin, EnglishResponseMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing workflow templates.

    - LIST: Filter by category, search, and optionally project_id (for is_applied annotation).
            Visibility: creator (private) + org members + project members.
    - CREATE: Clone source workflow and create template
    - UPDATE: Only creator can update
    - DELETE: Soft-delete (creator only)
    """
    permission_classes = [IsAuthenticated]
    serializer_class = AgentWorkflowTemplateSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        """
        Return templates visible to current user based on:
        1. Created by user (all templates, always visible regardless of sharing)
        2. Shared at organization level
        3. Shared to user's current active project (from X-Active-Project header)
        """
        from django.db.models import Q, Exists, OuterRef
        from core.models import ProjectMember, Project

        user = self.request.user
        qs = AgentWorkflowTemplate.objects.filter(is_deleted=False)

        # Get current active project from X-Active-Project header
        project = _get_user_project(self.request)

        # Build visibility filter
        # 1. All templates created by user (always visible, regardless of sharing)
        visibility_q = Q(created_by=user)

        # 2. Organization-level sharing
        if hasattr(user, 'organization') and user.organization:
            visibility_q |= Q(organization=user.organization)

        # 3. Project-level sharing (only for current active project)
        if project:
            # Verify user is actually a member of this project
            is_member = ProjectMember.objects.filter(
                user=user,
                project=project,
                is_active=True
            ).exists()

            if is_member:
                visibility_q |= Q(projects=project)  # M2M lookup

        qs = qs.filter(visibility_q)

        # Annotate whether template is shared to current project
        # This helps frontend decide whether to show in "Project" or "Private" section
        if project and self.action == 'list':
            qs = qs.annotate(
                is_shared_to_current_project=Exists(
                    Project.objects.filter(
                        id=project.id,
                        workflow_templates=OuterRef('pk'),
                    )
                )
            )

        # Category filter
        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)

        # Search by name
        search = self.request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(name__icontains=search)

        # Annotate is_applied_to_project when project_id is provided
        apply_project_id = self.request.query_params.get('project_id')
        if apply_project_id and self.action == 'list':
            from django.db.models import Exists
            from core.models import Project
            qs = qs.annotate(
                is_applied_to_project=Exists(
                    Project.objects.filter(
                        id=apply_project_id,
                        workflow_templates=OuterRef('pk'),
                    )
                )
            )

        return qs.prefetch_related('projects').select_related(
            'workflow_definition', 'created_by', 'organization'
        ).distinct().order_by('-created_at')

    def perform_create(self, serializer):
        """
        Create template by copying steps from source workflow.
        Templates are now fully independent - no workflow_definition dependency.
        """
        source_workflow_id = serializer.validated_data.pop('source_workflow_id', None)

        if not source_workflow_id:
            # If no source specified, create empty template
            steps_config = []
        else:
            # Get source workflow
            try:
                source_workflow = AgentWorkflowDefinition.objects.get(
                    id=source_workflow_id,
                    is_deleted=False
                )
            except AgentWorkflowDefinition.DoesNotExist:
                raise ValidationError({'source_workflow_id': 'Source workflow not found.'})

            # Copy steps configuration from source workflow
            steps = source_workflow.steps.filter(is_deleted=False).order_by('order')
            steps_config = []
            for step in steps:
                steps_config.append({
                    'step_type': step.step_type,
                    'name': step.name,
                    'order': step.order,
                    'config': step.config or {},
                    'description': step.description or '',
                })

        # Create template (serializer.create() also sets the projects M2M)
        serializer.save(
            created_by=self.request.user,
            steps_config=steps_config,
        )

    def perform_update(self, serializer):
        """Only allow creator to update their templates."""
        if serializer.instance.created_by != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('You can only edit templates you created.')

        serializer.save()

    def perform_destroy(self, instance):
        """Soft-delete template (creator only)."""
        # Only creator can delete
        if instance.created_by != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied('You can only delete templates you created.')

        # Soft delete
        instance.is_deleted = True
        instance.save()

    @action(detail=True, methods=['post'])
    def apply(self, request, pk=None):
        """
        Apply template to create a new workflow in the current project.
        Copies steps from template.steps_config to the new workflow.
        """
        template = self.get_object()
        project = _get_user_project(request)

        if not project:
            return Response(
                {'detail': 'An active project is required to apply a template.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get custom name from request
        custom_name = (request.data.get('name') or '').strip()
        workflow_name = custom_name or template.name

        # Create new workflow
        from django.db import transaction
        with transaction.atomic():
            new_workflow = AgentWorkflowDefinition.objects.create(
                name=workflow_name,
                description=template.description,
                project=project,
                is_default=False,
                is_system=False,
                status='draft',
                created_by=request.user,
            )

            # Copy steps from template.steps_config
            for step_config in template.steps_config:
                AgentWorkflowStep.objects.create(
                    workflow=new_workflow,
                    step_type=step_config.get('step_type'),
                    name=step_config.get('name'),
                    order=step_config.get('order', 0),
                    config=step_config.get('config', {}),
                    description=step_config.get('description', ''),
                )

        serializer = AgentWorkflowDefinitionDetailSerializer(new_workflow)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class WorkflowTriggerLogViewSet(EnglishResponseMixin, viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for workflow trigger logs.
    GET /api/agent/trigger-logs/?workflow_id={id}&limit=50
    """
    permission_classes = [IsAuthenticated]
    serializer_class = WorkflowTriggerLogSerializer
    pagination_class = None

    def get_queryset(self):
        workflow_id = self.request.query_params.get('workflow_id')
        limit = int(self.request.query_params.get('limit', 50))
        limit = max(1, min(limit, 100))  # Clamp between 1-100

        # Base queryset: user can only see logs for workflows they have access to
        qs = WorkflowTriggerLog.objects.select_related('workflow', 'workflow_run')

        # Filter by workflow ownership
        project = _get_user_project(self.request)
        if project:
            # User can see logs for workflows in their project or system workflows
            qs = qs.filter(
                Q(workflow__is_system=True) |
                Q(workflow__project=project, workflow__created_by=self.request.user)
            )
        else:
            # Only system workflows if no project
            qs = qs.filter(workflow__is_system=True)

        # Filter by specific workflow if provided
        if workflow_id:
            qs = qs.filter(workflow_id=workflow_id)

        return qs.order_by('-created_at')[:limit]


class WebhookReceiverView(APIView):
    """
    Webhook receiver for external triggers.
    POST /api/agent/webhooks/{workflow_id}/

    Validates signature and triggers the workflow.
    """
    permission_classes = []  # Use signature-based authentication

    def post(self, request, workflow_id):
        """Handle incoming webhook."""
        from .trigger_handlers import InstantHandler

        payload = request.data
        signature = request.headers.get('X-Webhook-Signature', '')

        success = InstantHandler.handle_webhook(
            workflow_id=workflow_id,
            payload=payload,
            signature=signature
        )

        if success:
            return Response({'message': 'Webhook processed successfully'})
        else:
            return Response(
                {'detail': 'Webhook validation failed'},
                status=status.HTTP_400_BAD_REQUEST
            )
