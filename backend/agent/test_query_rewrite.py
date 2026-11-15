"""rewrite_project_question. The model call is mocked; history is stubbed."""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings


@override_settings(RAG_QUERY_REWRITE_TIMEOUT_SECONDS=15)
class RewriteProjectQuestionTest(SimpleTestCase):
    def setUp(self):
        self.session = MagicMock(id='session-1')
        self.project = MagicMock(id=7)
        self.question = 'Why was that?'

    @patch('agent.query_rewrite.recent_rewrite_turns', return_value=[])
    @patch('agent.llm_client.call_llm')
    def test_no_history_skips_the_model(self, mock_llm, _turns):
        from agent.query_rewrite import rewrite_project_question

        with self.assertLogs('agent.query_rewrite', level='INFO') as logs:
            rewritten = rewrite_project_question(self.session, self.project, self.question)

        self.assertEqual(rewritten, self.question)
        mock_llm.assert_not_called()
        self.assertIn('fell_back=False', logs.output[0])
        self.assertIn('reason=no_history', logs.output[0])
        self.assertIn(self.question, logs.output[0])

    @patch('agent.query_rewrite.recent_rewrite_turns', return_value=[('Which vendor?', 'Vendor A.')])
    @patch('agent.llm_client.call_llm')
    def test_single_line_rewrite_is_used(self, mock_llm, _turns):
        from agent.query_rewrite import rewrite_project_question

        mock_llm.return_value = {
            'text': '{"question": "Why did we pick vendor A?"}',
            'usage': {'input': 1, 'output': 1},
        }

        with self.assertLogs('agent.query_rewrite', level='INFO') as logs:
            rewritten = rewrite_project_question(self.session, self.project, self.question)

        self.assertEqual(rewritten, 'Why did we pick vendor A?')
        kwargs = mock_llm.call_args.kwargs
        self.assertEqual(kwargs['agent_session'], self.session)
        self.assertEqual(kwargs['provider'], 'ollama')
        self.assertEqual(kwargs['model'], 'qwen3:4b')
        self.assertEqual(kwargs['temperature'], 0)
        self.assertEqual(kwargs['timeout'], 15)
        self.assertEqual(kwargs['response_format']['required'], ['question'])
        self.assertIn('Respond only with JSON:', kwargs['system_prompt'])
        self.assertIn('Vendor A.', kwargs['user_prompt'])
        self.assertIn(self.question, kwargs['user_prompt'])
        self.assertIn('Example 2 — too short (wrong) vs searchable (right):', kwargs['user_prompt'])
        self.assertIn('Wrong (too short — bare label only):', kwargs['user_prompt'])
        self.assertIn('Did the client approve homepage banner Option B that Priya recommended?', kwargs['user_prompt'])
        self.assertIn('Rewritten:', kwargs['user_prompt'])

        self.assertIn('fell_back=False', logs.output[0])
        self.assertIn('reason=rewritten', logs.output[0])
        self.assertIn('Why did we pick vendor A?', logs.output[0])

    @patch('agent.query_rewrite.recent_rewrite_turns', return_value=[('Which vendor?', 'Vendor A.')])
    @patch('agent.llm_client.call_llm')
    def test_empty_output_falls_back(self, mock_llm, _turns):
        from agent.query_rewrite import rewrite_project_question, rewrite_project_question_result

        mock_llm.return_value = {'text': '  \n', 'usage': {'input': 1, 'output': 0}}

        with self.assertLogs('agent.query_rewrite', level='INFO') as logs:
            rewritten = rewrite_project_question(self.session, self.project, self.question)
            detailed = rewrite_project_question_result(self.session, self.project, self.question)

        self.assertEqual(rewritten, self.question)
        self.assertEqual(detailed.rejected_text, '')
        self.assertIn('fell_back=True', logs.output[0])
        self.assertIn('reason=unusable_output', logs.output[0])

    @patch('agent.query_rewrite.recent_rewrite_turns', return_value=[('Which vendor?', 'Vendor A.')])
    @patch('agent.llm_client.call_llm')
    def test_explanation_falls_back(self, mock_llm, _turns):
        mock_llm.return_value = {
            'text': 'Here is the rewritten question.\n\nWhy did we pick vendor A?',
            'usage': {'input': 1, 'output': 1},
        }

        from agent.query_rewrite import rewrite_project_question_result
        detailed = rewrite_project_question_result(self.session, self.project, self.question)

        self.assertEqual(detailed.question, self.question)
        self.assertTrue(detailed.fell_back)
        self.assertEqual(detailed.reason, 'unusable_output')
        self.assertIn('Here is the rewritten question.', detailed.rejected_text)

    @patch('agent.query_rewrite.recent_rewrite_turns', return_value=[('Which vendor?', 'Vendor A.')])
    @patch('agent.llm_client.call_llm')
    def test_model_error_falls_back(self, mock_llm, _turns):
        from agent.query_rewrite import rewrite_project_question, rewrite_project_question_result

        mock_llm.side_effect = TimeoutError('rewrite timed out')

        with self.assertLogs('agent.query_rewrite', level='INFO') as logs:
            rewritten = rewrite_project_question(self.session, self.project, self.question)
            detailed = rewrite_project_question_result(self.session, self.project, self.question)

        self.assertEqual(rewritten, self.question)
        self.assertTrue(detailed.fell_back)
        self.assertEqual(detailed.reason, 'error')
        self.assertEqual(detailed.question, self.question)
        self.assertTrue(any('fell_back=True' in line and 'reason=error' in line for line in logs.output))
        self.assertTrue(any('error_type=TimeoutError' in line for line in logs.output))

    @patch('agent.query_rewrite.recent_rewrite_turns', return_value=[('Which vendor?', 'Vendor A.')])
    @patch('agent.llm_client.call_llm')
    def test_unchanged_question_is_not_a_fallback(self, mock_llm, _turns):
        from agent.query_rewrite import rewrite_project_question_result

        mock_llm.return_value = {
            'text': '{"question": "Why was that?"}',
            'usage': {'input': 1, 'output': 1},
        }

        detailed = rewrite_project_question_result(self.session, self.project, self.question)

        self.assertEqual(detailed.question, self.question)
        self.assertFalse(detailed.fell_back)
        self.assertEqual(detailed.reason, 'unchanged')
        self.assertEqual(detailed.rejected_text, '')


class ParseRewriteResponseTest(SimpleTestCase):
    def _parse(self, text):
        from agent.query_rewrite import _question_from_rewrite_response
        return _question_from_rewrite_response(text)

    def test_valid_json_returns_the_question(self):
        self.assertEqual(self._parse('{"question": "Why vendor A?"}'), 'Why vendor A?')

    def test_non_json_is_rejected(self):
        self.assertIsNone(self._parse('Why vendor A?'))

    def test_missing_question_is_rejected(self):
        self.assertIsNone(self._parse('{"note": "Why vendor A?"}'))

    def test_empty_question_is_rejected(self):
        self.assertIsNone(self._parse('{"question": "  "}'))

    def test_multiline_question_is_rejected(self):
        self.assertIsNone(self._parse('{"question": "Why vendor A?\\nBecause it was cheaper."}'))


class RewriteTimeoutForwardingTest(SimpleTestCase):
    @patch('core.services.gemini_client._get_api_key', return_value='test-key')
    @patch('core.services.gemini_client._gemini_request_with_retry')
    def test_explicit_timeout_caps_the_gemini_deadline(self, mock_retry, _key):
        response = MagicMock()
        response.json.return_value = {
            'candidates': [{'content': {'parts': [{'text': 'Why vendor A?'}]}}],
            'usageMetadata': {'promptTokenCount': 3, 'candidatesTokenCount': 4},
        }
        mock_retry.return_value = response

        from agent.llm_client import _call_gemini

        _call_gemini(
            'gemini-2.5-flash-lite', 'sys', 'user', 256, 0, None, timeout=15,
        )

        self.assertEqual(mock_retry.call_args.kwargs['timeout'], 15)
        self.assertEqual(mock_retry.call_args.kwargs['total_deadline'], 15)
