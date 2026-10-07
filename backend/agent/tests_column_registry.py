"""
Unit tests for agent/column_registry.py

Coverage:
  - Rule-based detection: meta_ads schema (exact match, alias variants, mixed-case)
  - Confidence threshold: < 0.5 falls through to LLM path
  - LLM fallback: successful parse, JSON fences, partial unknown, full failure,
                  sample rows included in prompt, per-column confidence scores
  - normalize_spreadsheet: rename, unknown passthrough, multi-sheet, empty mapping
  - ColumnDetectionResult: to_dict round-trip, column_confidences defaults
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from .column_registry import (
    CAT_FINANCIAL,
    CAT_ENGAGEMENT,
    CAT_CONVERSION,
    CAT_IDENTIFIER,
    CAT_PERFORMANCE_RATIO,
    CAT_UNKNOWN,
    CAT_TEMPORAL,
    ColumnDetectionResult,
    detect_columns,
    normalize_spreadsheet,
)
from . import column_registry as registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _meta_headers():
    """Canonical Meta Ads column headers (20 columns)."""
    return [
        'Campaign name', 'Ad set name', 'Ad name',
        'Campaign ID', 'Ad set ID', 'Ad ID',
        'Amount spent', 'CPM', 'CPC', 'CPP',
        'Impressions', 'Reach', 'Clicks', 'Link clicks', 'Frequency', 'Video views',
        'Purchases', 'Purchase ROAS', 'Add to cart', 'Leads',
    ]


def _make_spreadsheet(columns, rows=None):
    return {
        'name': 'Test',
        'sheets': [{'name': 'Sheet1', 'columns': columns, 'rows': rows or []}],
    }


# ---------------------------------------------------------------------------
# ColumnDetectionResult
# ---------------------------------------------------------------------------

class ColumnDetectionResultTests(SimpleTestCase):

    def test_to_dict_round_trip(self):
        result = ColumnDetectionResult(
            schema_key='meta_ads',
            schema_name='Meta Ads Performance',
            source='rule',
            confidence=0.95,
            mappings={'Campaign name': 'campaign_name'},
            categories={'campaign_name': CAT_IDENTIFIER},
            unrecognized=[],
        )
        d = result.to_dict()
        self.assertEqual(d['schema_key'], 'meta_ads')
        self.assertEqual(d['source'], 'rule')
        self.assertEqual(d['confidence'], 0.95)
        self.assertEqual(d['mappings'], {'Campaign name': 'campaign_name'})
        self.assertEqual(d['unrecognized'], [])
        self.assertIn('column_confidences', d)

    def test_column_confidences_default_for_rule_match(self):
        result = ColumnDetectionResult(
            schema_key='meta_ads',
            schema_name='Meta Ads Performance',
            source='rule',
            confidence=1.0,
            mappings={'Campaign name': 'campaign_name', 'Unknown Col': CAT_UNKNOWN},
            categories={'campaign_name': CAT_IDENTIFIER},
            unrecognized=['Unknown Col'],
        )
        # Known columns default to 1.0, unrecognized to 0.0
        self.assertEqual(result.column_confidences['Campaign name'], 1.0)
        self.assertEqual(result.column_confidences['Unknown Col'], 0.0)

    def test_column_confidences_explicit_override(self):
        result = ColumnDetectionResult(
            schema_key=None,
            schema_name='Custom',
            source='llm',
            confidence=0.7,
            mappings={'Revenue': 'revenue'},
            categories={'revenue': CAT_FINANCIAL},
            unrecognized=[],
            column_confidences={'Revenue': 0.85},
        )
        self.assertEqual(result.column_confidences['Revenue'], 0.85)


# ---------------------------------------------------------------------------
# Rule-based detection
# ---------------------------------------------------------------------------

RULE_SOURCES = ('rule', 'db_template')


class RuleBasedDetectionTests(SimpleTestCase):
    databases = ('default',)

    def test_full_meta_ads_match(self):
        result = detect_columns(_meta_headers())
        self.assertIn(result.source, RULE_SOURCES)
        self.assertIsNotNone(result.schema_key)
        self.assertGreaterEqual(result.confidence, 0.95)
        self.assertEqual(result.unrecognized, [])

    def test_meta_ads_alias_variants(self):
        headers = ['spend', 'ctr (all)', 'link ctr', 'roas', 'conversions']
        result = detect_columns(headers)
        self.assertIn(result.source, RULE_SOURCES)
        self.assertEqual(result.mappings['spend'], 'amount_spent')
        self.assertEqual(result.mappings['ctr (all)'], 'ctr')
        self.assertEqual(result.mappings['roas'], 'purchase_roas')
        self.assertEqual(result.mappings['conversions'], 'purchases')

    def test_meta_ads_case_insensitive(self):
        headers = ['CAMPAIGN NAME', 'IMPRESSIONS', 'AMOUNT SPENT']
        result = detect_columns(headers)
        self.assertIn(result.source, RULE_SOURCES)
        self.assertEqual(result.mappings['CAMPAIGN NAME'], 'campaign_name')
        self.assertEqual(result.mappings['IMPRESSIONS'], 'impressions')

    def test_categories_populated(self):
        result = detect_columns(_meta_headers())
        self.assertEqual(result.categories['campaign_name'], CAT_IDENTIFIER)
        self.assertEqual(result.categories['amount_spent'], CAT_FINANCIAL)
        self.assertEqual(result.categories['impressions'], CAT_ENGAGEMENT)
        self.assertEqual(result.categories['purchases'], CAT_CONVERSION)

    def test_partial_match_above_threshold_still_rule(self):
        # 3 out of 4 known → 75% confidence → rule/db_template path
        headers = ['Campaign name', 'Impressions', 'Amount spent', 'MyCustomColumn']
        result = detect_columns(headers)
        self.assertIn(result.source, RULE_SOURCES)
        self.assertIn('MyCustomColumn', result.unrecognized)
        self.assertEqual(result.mappings['MyCustomColumn'], CAT_UNKNOWN)

    def test_below_confidence_threshold_falls_to_llm(self):
        # Only 1 out of 5 known → 20% → should NOT match rule or db_template
        headers = ['Campaign name', 'FooBar', 'BazQux', 'Alpha', 'Beta']
        with patch('agent.column_registry._try_llm_fallback') as mock_llm:
            mock_llm.return_value = ColumnDetectionResult(
                schema_key=None, schema_name='Unknown', source='llm',
                confidence=0.1, mappings={h: CAT_UNKNOWN for h in headers},
                categories={}, unrecognized=list(headers),
            )
            result = detect_columns(headers)
        mock_llm.assert_called_once()
        self.assertEqual(result.source, 'llm')

    def test_empty_headers_returns_unknown(self):
        result = detect_columns([])
        self.assertEqual(result.source, 'none')
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.mappings, {})


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------

_GEMINI_SETTINGS = dict(GEMINI_API_KEY='test-key')


class LLMFallbackTests(TestCase):
    databases = ('default',)

    def setUp(self):
        from django.utils import timezone
        from datetime import timedelta
        from django.contrib.auth import get_user_model
        from core.models import Organization, Project
        from agent.models import AgentSession
        from stripe_meta.models import Plan, Subscription

        User = get_user_model()
        Plan.objects.all().delete()

        self.org = Organization.objects.create(name='LLMFallbackOrg', slug='llm-fallback-org')
        self.user = User.objects.create_user(
            email='llmfallback@test.com', username='llmfallbackuser', password='pass',
        )
        self.user.organization = self.org
        self.user.save()
        self.plan = Plan.objects.create(
            name='FallbackTestPlan',
            monthly_token_quota=10_000_000,
            max_tokens_per_call=None,
            base_price_cents=0,
        )
        Subscription.objects.create(
            organization=self.org,
            plan=self.plan,
            stripe_subscription_id=f'sub_fallback_{self.org.id}',
            start_date=timezone.now(),
            end_date=timezone.now() + timedelta(days=365 * 10),
            is_active=True,
            is_internal=True,
        )
        self.project = Project.objects.create(
            name='FallbackProject', organization=self.org, owner=self.user,
        )
        self.session = AgentSession.objects.create(user=self.user, project=self.project)

    def _make_gemini_response(self, columns_list, schema_name='Custom Report', confidence=0.8):
        """Return the dict that _call_gemini would return for a successful call."""
        return {
            'text': json.dumps({
                'schema_name': schema_name,
                'confidence': confidence,
                'columns': columns_list,
            }),
            'usage': {'input': 10, 'output': 20},
        }

    @override_settings(**_GEMINI_SETTINGS)
    @patch('agent.llm_client._call_gemini')
    def test_llm_fallback_success(self, mock_run):
        headers = ['Revenue', 'Sessions', 'Bounce Rate']
        mock_run.return_value = self._make_gemini_response([
            {'original': 'Revenue', 'canonical': 'revenue', 'category': 'financial', 'confidence': 0.95},
            {'original': 'Sessions', 'canonical': 'sessions', 'category': 'engagement', 'confidence': 0.9},
            {'original': 'Bounce Rate', 'canonical': 'bounce_rate', 'category': 'performance_ratio', 'confidence': 0.8},
        ])

        result = detect_columns(headers, agent_session=self.session)

        self.assertEqual(result.source, 'llm')
        self.assertEqual(result.mappings['Revenue'], 'revenue')
        self.assertEqual(result.mappings['Sessions'], 'sessions')
        self.assertEqual(result.categories['revenue'], 'financial')
        self.assertEqual(result.column_confidences['Revenue'], 0.95)
        self.assertEqual(result.column_confidences['Sessions'], 0.9)
        self.assertEqual(result.column_confidences['Bounce Rate'], 0.8)

    @override_settings(**_GEMINI_SETTINGS)
    @patch('agent.llm_client._call_gemini')
    def test_llm_fallback_includes_sample_rows_in_prompt(self, mock_run):
        headers = ['Revenue']
        sample_rows = [{'Revenue': 1000}, {'Revenue': 2000}]
        mock_run.return_value = self._make_gemini_response([
            {'original': 'Revenue', 'canonical': 'revenue', 'category': 'financial', 'confidence': 0.9},
        ])

        detect_columns(headers, sample_rows=sample_rows, agent_session=self.session)

        # _call_gemini is called positionally: (model, system_prompt, user_prompt, ...)
        user_prompt = mock_run.call_args[0][2]
        # Sample rows must be serialised into the user prompt sent to Gemini
        self.assertIn('1000', user_prompt)

    @override_settings(**_GEMINI_SETTINGS)
    @patch('agent.column_registry._try_db_template_match', return_value=None)
    @patch('agent.llm_client._call_gemini')
    def test_llm_fallback_strips_markdown_fences(self, mock_run, _mock_db):
        headers = ['xyzUnknownMetric999']
        mock_run.return_value = {
            'text': json.dumps({
                'schema_name': 'Custom', 'confidence': 0.8,
                'columns': [{'original': 'xyzUnknownMetric999', 'canonical': 'xyz_metric', 'category': 'financial', 'confidence': 0.9}],
            }),
            'usage': {'input': 10, 'output': 20},
        }

        result = detect_columns(headers, agent_session=self.session)
        self.assertEqual(result.source, 'llm')
        self.assertEqual(result.mappings['xyzUnknownMetric999'], 'xyz_metric')

    @override_settings(**_GEMINI_SETTINGS)
    @patch('agent.llm_client._call_gemini')
    def test_llm_unknown_columns_labeled_unknown(self, mock_run):
        headers = ['WeirdCol1', 'WeirdCol2']
        mock_run.return_value = self._make_gemini_response([
            {'original': 'WeirdCol1', 'canonical': 'unknown', 'category': 'unknown', 'confidence': 0.0},
            {'original': 'WeirdCol2', 'canonical': 'unknown', 'category': 'unknown', 'confidence': 0.0},
        ])

        result = detect_columns(headers, agent_session=self.session)
        self.assertEqual(result.mappings['WeirdCol1'], CAT_UNKNOWN)
        self.assertEqual(result.mappings['WeirdCol2'], CAT_UNKNOWN)
        self.assertEqual(set(result.unrecognized), {'WeirdCol1', 'WeirdCol2'})
        # Unknown columns must have 0.0 per-column confidence
        self.assertEqual(result.column_confidences['WeirdCol1'], 0.0)
        self.assertEqual(result.column_confidences['WeirdCol2'], 0.0)

    @override_settings(**_GEMINI_SETTINGS)
    @patch('agent.llm_client._call_gemini')
    def test_llm_exception_falls_back_to_all_unknown(self, mock_run):
        headers = ['ColA', 'ColB']
        mock_run.side_effect = Exception('network error')

        result = detect_columns(headers, agent_session=self.session)
        self.assertEqual(result.source, 'none')
        self.assertEqual(result.confidence, 0.0)
        self.assertTrue(all(v == CAT_UNKNOWN for v in result.mappings.values()))

    @patch.dict('os.environ', {'GEMINI_API_KEY': ''})
    def test_llm_skipped_when_no_api_key(self):
        headers = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon']
        with override_settings(GEMINI_API_KEY=''):
            result = detect_columns(headers, agent_session=self.session)
        # Should return unknown result (no LLM, no rule match)
        self.assertIn(result.source, ('none', 'rule'))


# ---------------------------------------------------------------------------
# normalize_spreadsheet
# ---------------------------------------------------------------------------

class NormalizeSpreadsheetTests(SimpleTestCase):
    databases = ('default',)

    def test_renames_known_columns(self):
        data = _make_spreadsheet(
            columns=['Campaign name', 'Amount spent'],
            rows=[{'Campaign name': 'Test', 'Amount spent': 100.0}],
        )
        mapping = {'Campaign name': 'campaign_name', 'Amount spent': 'amount_spent'}
        normalized = normalize_spreadsheet(data, mapping)

        sheet = normalized['sheets'][0]
        self.assertEqual(sheet['columns'], ['campaign_name', 'amount_spent'])
        self.assertEqual(sheet['rows'][0]['campaign_name'], 'Test')
        self.assertEqual(sheet['rows'][0]['amount_spent'], 100.0)

    def test_unknown_columns_keep_original_name(self):
        data = _make_spreadsheet(
            columns=['Campaign name', 'SomeWeirdCol'],
            rows=[{'Campaign name': 'Test', 'SomeWeirdCol': 'value'}],
        )
        mapping = {'Campaign name': 'campaign_name', 'SomeWeirdCol': CAT_UNKNOWN}
        normalized = normalize_spreadsheet(data, mapping)

        sheet = normalized['sheets'][0]
        self.assertIn('SomeWeirdCol', sheet['columns'])
        self.assertIn('campaign_name', sheet['columns'])
        self.assertEqual(sheet['rows'][0]['SomeWeirdCol'], 'value')

    def test_empty_mapping_returns_data_unchanged(self):
        data = _make_spreadsheet(columns=['Col1', 'Col2'])
        normalized = normalize_spreadsheet(data, {})
        self.assertEqual(normalized['sheets'][0]['columns'], ['Col1', 'Col2'])

    def test_multi_sheet_all_sheets_renamed(self):
        data = {
            'name': 'Report',
            'sheets': [
                {
                    'name': 'Sheet1',
                    'columns': ['Amount spent'],
                    'rows': [{'Amount spent': 50.0}],
                },
                {
                    'name': 'Sheet2',
                    'columns': ['Amount spent'],
                    'rows': [{'Amount spent': 75.0}],
                },
            ],
        }
        mapping = {'Amount spent': 'amount_spent'}
        normalized = normalize_spreadsheet(data, mapping)

        for sheet in normalized['sheets']:
            self.assertEqual(sheet['columns'], ['amount_spent'])
            self.assertEqual(sheet['rows'][0]['amount_spent'], sheet['rows'][0].get('amount_spent'))

    def test_preserves_spreadsheet_name(self):
        data = _make_spreadsheet(columns=['Clicks'])
        data['name'] = 'My Report'
        mapping = {'Clicks': 'clicks'}
        normalized = normalize_spreadsheet(data, mapping)
        self.assertEqual(normalized['name'], 'My Report')

    @patch('agent.column_registry._try_db_template_match', return_value=None)
    def test_full_meta_ads_pipeline(self, _mock_db):
        """detect_columns → normalize_spreadsheet produces clean column names."""
        headers = _meta_headers()
        data = _make_spreadsheet(
            columns=headers,
            rows=[{h: i for i, h in enumerate(headers)}],
        )
        detection = detect_columns(headers)
        self.assertEqual(detection.source, 'rule')

        normalized = normalize_spreadsheet(data, detection.mappings)
        sheet = normalized['sheets'][0]
        self.assertIn('campaign_name', sheet['columns'])
        self.assertIn('amount_spent', sheet['columns'])
        self.assertIn('impressions', sheet['columns'])
        # No original names should remain
        for original in headers:
            self.assertNotIn(original, sheet['columns'])


# ---------------------------------------------------------------------------
# Remaining helper and persistence edge cases
# ---------------------------------------------------------------------------

class AutoCategorizationTests(SimpleTestCase):

    def test_keyword_categories_and_unknown_fallback(self):
        cases = {
            'ad_cost': CAT_FINANCIAL,
            'video_views': CAT_ENGAGEMENT,
            'purchase_count': CAT_CONVERSION,
            'efficiency_score': CAT_PERFORMANCE_RATIO,
            'campaign_name': CAT_IDENTIFIER,
            'mystery_field': CAT_UNKNOWN,
            '': CAT_UNKNOWN,
            CAT_UNKNOWN: CAT_UNKNOWN,
        }

        for canonical_name, expected in cases.items():
            with self.subTest(canonical_name=canonical_name):
                self.assertEqual(registry.auto_categorize_by_name(canonical_name), expected)

    def test_keyword_matching_does_not_use_partial_word_substrings(self):
        self.assertEqual(
            registry.auto_categorize_by_name("total_spend"),
            CAT_FINANCIAL,
        )
        self.assertEqual(
            registry.auto_categorize_by_name("week_end"),
            CAT_TEMPORAL,
        )


    def test_compound_keywords_still_match(self):
        self.assertEqual(
            registry.auto_categorize_by_name("add_to_cart"),
            CAT_CONVERSION,
        )


class DatabaseTemplateHelperTests(SimpleTestCase):
    databases = ('default',)

    @patch('agent.models.DataSchemaTemplate.objects.filter', side_effect=RuntimeError('db unavailable'))
    def test_db_template_failure_is_treated_as_no_match(self, _mock_filter):
        self.assertIsNone(registry._try_db_template_match(['Revenue']))

    @patch('agent.models.DataSchemaTemplate.objects.filter')
    def test_db_template_without_column_definitions_is_skipped(self, mock_filter):
        queryset = MagicMock()
        queryset.order_by.return_value = [
            SimpleNamespace(column_definitions=[], match_threshold=0.5)
        ]
        mock_filter.return_value = queryset

        self.assertIsNone(registry._try_db_template_match(['Revenue']))


class LearnedTemplateTests(SimpleTestCase):
    databases = ('default',)

    def test_empty_columns_are_not_saved(self):
        self.assertIsNone(registry.save_learned_template('Empty schema', 'custom', []))

    @patch('agent.models.DataSchemaTemplate.objects.get_or_create')
    def test_existing_template_receives_updated_column_definitions(self, mock_get_or_create):
        template = MagicMock()
        columns = [
            {
                'canonical_name': 'revenue',
                'aliases': ['Revenue'],
                'category': CAT_FINANCIAL,
            }
        ]
        mock_get_or_create.return_value = (template, False)

        result = registry.save_learned_template('Sales', 'custom', columns)

        self.assertIs(result, template)
        self.assertEqual(template.column_definitions, columns)
        template.save.assert_called_once_with(
            update_fields=['column_definitions', 'updated_at']
        )

    @patch('agent.models.DataSchemaTemplate.objects.get_or_create', side_effect=RuntimeError('write failed'))
    def test_template_write_failure_returns_none(self, _mock_get_or_create):
        result = registry.save_learned_template(
            'Sales',
            'custom',
            [{'canonical_name': 'revenue'}],
        )

        self.assertIsNone(result)


class LLMQuotaPropagationTests(SimpleTestCase):
    @patch('core.services.gemini_client._get_api_key', return_value='test-key')
    @patch('agent.llm_client.call_llm')
    def test_quota_error_is_not_converted_to_unknown_result(self, mock_call_llm, _mock_key):
        from stripe_meta.exceptions import QuotaError

        mock_call_llm.side_effect = QuotaError(
            code='MONTHLY_QUOTA_EXCEEDED',
            message='quota exhausted',
        )

        with self.assertRaisesRegex(QuotaError, 'quota exhausted'):
            registry._try_llm_fallback(['Revenue'])
