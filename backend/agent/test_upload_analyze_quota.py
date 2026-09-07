"""
Tests: FileUploadAnalyzeView surfaces QuotaError to the SSE stream instead of
swallowing it into the generic "An internal error occurred" message.
"""
import json
import uuid
from contextlib import ExitStack
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from core.models import Organization, Project, ProjectMember
from stripe_meta.exceptions import QuotaError
from stripe_meta.models import Plan

User = get_user_model()

URL = '/api/agent/upload-analyze/'

_FAKE_SAVE_RESULT = {
    'id': str(uuid.uuid4()),
    'filename': 'test.csv',
    'original_filename': 'test.csv',
    'row_count': 2,
    'column_count': 2,
}

_FAKE_IMPORT_RESULT = {
    'spreadsheet_id': 123,
    'sheet_id': 456,
    'project_id': 789,
    'url': '/spreadsheets/123?project_id=789',
    'name': 'test',
}


def _quota_generator(code, message):
    """Generator that raises QuotaError on first iteration."""
    def gen(*args, **kwargs):
        raise QuotaError(code=code, message=message)
        yield  # makes this a generator function
    return gen


def _parse_sse(response) -> list[dict]:
    content = b''.join(response.streaming_content)
    chunks = []
    for line in content.decode().splitlines():
        if line.startswith('data: '):
            try:
                chunks.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return chunks


class FileUploadAnalyzeQuotaTest(APITestCase):
    def setUp(self):
        Plan.objects.all().delete()
        self.org = Organization.objects.create(name='UploadOrg')
        self.user = User.objects.create_user(
            email='upload@test.com', username='uploaduser', password='pw',
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name='UploadProj', organization=self.org, owner=self.user,
        )
        ProjectMember.objects.create(project=self.project, user=self.user)
        self.client.force_authenticate(user=self.user)

    def _run(self, handle_message_side_effect):
        """POST a CSV with the orchestrator + spreadsheet-import step stubbed.

        The spreadsheet-import gateway (create_spreadsheet_from_upload +
        ImportedCSVFile bookkeeping) runs before the orchestrator; stub it so
        the stream actually reaches orchestrator.handle_message.
        """
        with ExitStack() as stack:
            MockOrch = stack.enter_context(patch('agent.views.AgentOrchestrator'))
            mock_ds = stack.enter_context(patch('agent.views.data_service'))
            stack.enter_context(
                patch('agent.views.parse_file_to_json',
                      return_value={'name': 'test.csv', 'sheets': [
                          {'name': 'Sheet1', 'columns': ['campaign', 'spend'],
                           'rows': [{'campaign': 'A', 'spend': 100}]}],
                          'limits_hit': {}})
            )
            MockProvider = stack.enter_context(
                patch('agent.views.SpreadsheetDataProvider')
            )
            MockProvider.return_value.create_from_parsed_upload.return_value = dict(
                _FAKE_IMPORT_RESULT
            )

            mock_ds.save_uploaded_file.return_value = _FAKE_SAVE_RESULT
            mock_ds._get_csv_dir.return_value = '/tmp'

            inst = MagicMock()
            inst.handle_message.side_effect = handle_message_side_effect
            MockOrch.return_value = inst

            import io
            csv_bytes = io.BytesIO(b'campaign,spend\nA,100\nB,200')
            csv_bytes.name = 'test.csv'
            return self.client.post(
                URL,
                {'file': csv_bytes},
                format='multipart',
                HTTP_X_PROJECT_ID=str(self.project.id),
            )

    def _post(self, quota_code, quota_message):
        return self._run(_quota_generator(quota_code, quota_message))

    def test_project_has_no_org_surfaced(self):
        response = self._post('PROJECT_HAS_NO_ORG', 'This project is not linked to an organization.')
        self.assertEqual(response.status_code, 200)
        chunks = _parse_sse(response)
        quota = [c for c in chunks if c.get('code') == 'PROJECT_HAS_NO_ORG']
        self.assertTrue(quota, f"Expected PROJECT_HAS_NO_ORG chunk; got: {chunks}")
        generic = [c for c in chunks if 'internal error' in str(c.get('content', '')).lower()]
        self.assertFalse(generic, f"Got generic error instead of quota code: {chunks}")

    def test_token_quota_exceeded_surfaced(self):
        response = self._post('TOKEN_QUOTA_EXCEEDED', 'Monthly quota exceeded.')
        self.assertEqual(response.status_code, 200)
        chunks = _parse_sse(response)
        quota = [c for c in chunks if c.get('code') == 'TOKEN_QUOTA_EXCEEDED']
        self.assertTrue(quota, f"Expected TOKEN_QUOTA_EXCEEDED chunk; got: {chunks}")

    def test_single_call_too_large_surfaced(self):
        response = self._post('SINGLE_CALL_TOO_LARGE', 'Request too large.')
        self.assertEqual(response.status_code, 200)
        chunks = _parse_sse(response)
        quota = [c for c in chunks if c.get('code') == 'SINGLE_CALL_TOO_LARGE']
        self.assertTrue(quota, f"Expected SINGLE_CALL_TOO_LARGE chunk; got: {chunks}")

    def test_non_quota_exception_still_generic(self):
        """Regular exceptions still yield the generic error (regression guard)."""
        response = self._run(RuntimeError('some unexpected error'))

        self.assertEqual(response.status_code, 200)
        chunks = _parse_sse(response)
        generic = [c for c in chunks if 'internal error' in str(c.get('content', '')).lower()]
        self.assertTrue(generic, f"Expected generic error chunk; got: {chunks}")
