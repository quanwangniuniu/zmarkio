from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from optimization.tasks import recompute_all_pacing_forecasts


class PacingCoordinatorTaskTest(SimpleTestCase):
    @patch('optimization.tasks._stale_meta_account_ids', return_value=[])
    @patch('core.tenant_context.tenant_schema_context', side_effect=lambda _schema: nullcontext())
    @patch('core.models.Organization.objects.filter')
    @patch('campaign.models.Campaign.objects.filter')
    @patch('optimization.tasks.group')
    def test_large_campaign_set_is_dispatched_in_bounded_batches(
        self, mock_group, mock_filter, mock_organizations, _mock_context, _mock_freshness
    ):
        organizations = MagicMock()
        mock_organizations.return_value = organizations
        organizations.order_by.return_value = organizations
        organizations.values_list.return_value = ['alee']

        queryset = MagicMock()
        mock_filter.return_value = queryset
        queryset.exclude.return_value = queryset
        queryset.order_by.return_value = queryset
        queryset.values_list.return_value = list(range(205))

        queued_signatures = []
        job = MagicMock(id='group-123')

        def capture_group(signatures):
            queued_signatures.extend(list(signatures))
            grouped = MagicMock()
            grouped.apply_async.return_value = job
            return grouped

        mock_group.side_effect = capture_group

        result = recompute_all_pacing_forecasts.run()

        self.assertEqual(result['status'], 'queued')
        self.assertEqual(result['campaigns'], 205)
        self.assertEqual(result['batches'], 3)
        self.assertEqual(result['tenants'], 1)
        self.assertEqual([len(signature.args[0]) for signature in queued_signatures], [100, 100, 5])
        self.assertEqual({signature.args[2] for signature in queued_signatures}, {'org_alee'})

    @patch('optimization.tasks._stale_meta_account_ids', return_value=[10, 11])
    @patch('core.tenant_context.tenant_schema_context', side_effect=lambda _schema: nullcontext())
    @patch('core.models.Organization.objects.filter')
    def test_stale_spend_retries_instead_of_publishing_a_stale_forecast(
        self, mock_organizations, _mock_context, _mock_freshness
    ):
        organizations = MagicMock()
        mock_organizations.return_value = organizations
        organizations.order_by.return_value = organizations
        organizations.values_list.return_value = ['alee']

        retry_error = RuntimeError('retry requested')
        with patch.object(recompute_all_pacing_forecasts, 'retry', side_effect=retry_error) as retry:
            with self.assertRaisesRegex(RuntimeError, 'retry requested'):
                recompute_all_pacing_forecasts.run()

        retry.assert_called_once_with(countdown=600)
