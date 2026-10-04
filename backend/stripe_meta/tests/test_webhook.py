"""
Unit tests for stripe_meta/views.py::stripe_webhook.

Covers:
  - Bad signature → 400
  - Idempotency: same event_id twice → handler fires once, second returns duplicate
  - Handler exception → 500, error_message saved, processed_at NOT set (enables Stripe retry)
"""
import json
import logging
from unittest.mock import patch, Mock

import stripe
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings

from django.utils import timezone

from stripe_meta.models import Plan, Subscription, StripeWebhookEvent, Payment, UsageMonthly
from stripe_meta.views import (
    handle_invoice_finalized,
    handle_payment_succeeded,
    handle_subscription_created,
    handle_subscription_updated,
)
from core.models import Organization

User = get_user_model()


def _make_event(event_id, event_type='checkout.session.completed', obj=None):
    """Return a minimal dict that looks like a Stripe Event."""
    return {
        'id': event_id,
        'type': event_type,
        'data': {'object': obj or {'id': 'cs_test_obj'}},
    }


class WebhookTests(TestCase):
    """
    Webhook view is @csrf_exempt; Django's test Client skips CSRF enforcement.
    Plans deleted first so the org auto-subscribe signal is a no-op.
    """

    def setUp(self):
        Plan.objects.all().delete()
        self.org = Organization.objects.create(name='WebhookOrg')
        self.client = Client(enforce_csrf_checks=False)

    def _post(self, body_dict=None, sig='valid_sig'):
        body = json.dumps(body_dict or {}).encode()
        return self.client.post(
            '/api/stripe/webhook/',
            data=body,
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE=sig,
        )

    # ── Signature verification ─────────────────────────────────────────────

    def test_webhook_signature_invalid(self):
        """
        stripe.Webhook.construct_event raises SignatureVerificationError
        → view returns 400 and creates no StripeWebhookEvent row.
        """
        with patch('stripe_meta.views.stripe.Webhook.construct_event') as mock_ce:
            mock_ce.side_effect = stripe.SignatureVerificationError(
                'Invalid signature', 'bad_sig'
            )
            response = self._post()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StripeWebhookEvent.objects.count(), 0)

    @override_settings(
        STRIPE_WEBHOOK_SECRET='whsec_current',
        STRIPE_WEBHOOK_SECRET_NEXT='',
    )
    def test_webhook_only_current_secret(self):
        """With no next secret configured, the current secret is used."""
        event = _make_event('evt_current_only', event_type='some.unknown.event')

        with patch(
            'stripe_meta.views.stripe.Webhook.construct_event',
            return_value=event,
        ) as mock_ce:
            response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_ce.call_count, 1)
        self.assertEqual(mock_ce.call_args.args[2], 'whsec_current')

    @override_settings(
        STRIPE_WEBHOOK_SECRET='whsec_current',
        STRIPE_WEBHOOK_SECRET_NEXT='whsec_next',
    )
    def test_webhook_both_active_accepts_current_secret(self):
        """During rotation, a webhook signed with the current secret is accepted."""
        event = _make_event('evt_current_active', event_type='some.unknown.event')

        with patch(
            'stripe_meta.views.stripe.Webhook.construct_event',
            return_value=event,
        ) as mock_ce:
            response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_ce.call_count, 1)
        self.assertEqual(mock_ce.call_args.args[2], 'whsec_current')

    @override_settings(
        STRIPE_WEBHOOK_SECRET='whsec_current',
        STRIPE_WEBHOOK_SECRET_NEXT='whsec_next',
    )
    def test_webhook_both_active_accepts_next_secret(self):
        """During rotation, the next secret is tried if the current secret fails."""
        event = _make_event('evt_next_active', event_type='some.unknown.event')

        with patch(
            'stripe_meta.views.stripe.Webhook.construct_event'
        ) as mock_ce:
            mock_ce.side_effect = [
                stripe.SignatureVerificationError(
                    'Invalid signature', 'valid_sig'
                ),
                event,
            ]

            response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_ce.call_count, 2)
        self.assertEqual(mock_ce.call_args_list[0].args[2], 'whsec_current')
        self.assertEqual(mock_ce.call_args_list[1].args[2], 'whsec_next')

    @override_settings(
        STRIPE_WEBHOOK_SECRET='whsec_current',
        STRIPE_WEBHOOK_SECRET_NEXT='whsec_next',
    )
    def test_webhook_both_active_rejects_invalid_signature(self):
        """When both active secrets reject the signature, the webhook returns 400."""
        with patch(
            'stripe_meta.views.stripe.Webhook.construct_event'
        ) as mock_ce:
            mock_ce.side_effect = [
                stripe.SignatureVerificationError(
                    'Invalid signature', 'bad_sig'
                ),
                stripe.SignatureVerificationError(
                    'Invalid signature', 'bad_sig'
                ),
            ]

            response = self._post(sig='bad_sig')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(mock_ce.call_count, 2)
        self.assertEqual(mock_ce.call_args_list[0].args[2], 'whsec_current')
        self.assertEqual(mock_ce.call_args_list[1].args[2], 'whsec_next')
        self.assertEqual(StripeWebhookEvent.objects.count(), 0)

    # ── Idempotency ────────────────────────────────────────────────────────

    def test_webhook_idempotency(self):
        """
        Same event_id sent twice:
          1st call → handler fires, processed_at set.
          2nd call → duplicate branch, handler NOT called again.
        Only one StripeWebhookEvent row created.
        """
        event = _make_event('evt_idem_001')

        with patch('stripe_meta.views.stripe.Webhook.construct_event', return_value=event):
            with patch('stripe_meta.views.handle_checkout_completed') as mock_handler:
                # First call — should process
                r1 = self._post()
                self.assertEqual(r1.status_code, 200)
                mock_handler.assert_called_once()

                # Second call — should return duplicate
                r2 = self._post()
                self.assertEqual(r2.status_code, 200)
                self.assertEqual(json.loads(r2.content).get('status'), 'duplicate')
                # Handler still only called once total
                mock_handler.assert_called_once()

        self.assertEqual(
            StripeWebhookEvent.objects.filter(stripe_event_id='evt_idem_001').count(), 1
        )
        # Ensure processed_at was set after first successful call
        evt = StripeWebhookEvent.objects.get(stripe_event_id='evt_idem_001')
        self.assertIsNotNone(evt.processed_at)

    # ── Error bubbling ─────────────────────────────────────────────────────

    def test_webhook_error_bubbling(self):
        """
        Handler raises an exception:
          - View must return 500 (not silently 200).
          - StripeWebhookEvent.error_message must be non-empty.
          - StripeWebhookEvent.processed_at must remain None so Stripe retries.
        """
        event = _make_event('evt_err_001')

        with patch('stripe_meta.views.stripe.Webhook.construct_event', return_value=event):
            with patch('stripe_meta.views.handle_checkout_completed') as mock_handler:
                mock_handler.side_effect = RuntimeError('handler boom')
                response = self._post()

        self.assertEqual(response.status_code, 500)

        evt = StripeWebhookEvent.objects.get(stripe_event_id='evt_err_001')
        # error_message is TextField(blank=True) — default is '', not None.
        # Use assertTrue / assertNotEqual, never assertIsNotNone.
        self.assertTrue(evt.error_message, msg='error_message must be non-empty')
        # processed_at must NOT be set: if it were set, Stripe would not retry.
        self.assertIsNone(evt.processed_at)

    # ── Handler dispatch ───────────────────────────────────────────────────

    def test_unknown_event_type_is_ignored(self):
        """
        Event type not in handlers dict → processed_at IS set (acknowledged),
        response status 'ignored', no handler error.
        """
        event = _make_event('evt_unknown_001', event_type='some.unknown.event')

        with patch('stripe_meta.views.stripe.Webhook.construct_event', return_value=event):
            response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content).get('status'), 'ignored')
        evt = StripeWebhookEvent.objects.get(stripe_event_id='evt_unknown_001')
        self.assertIsNotNone(evt.processed_at)


# ---------------------------------------------------------------------------
# Handler-level regression tests — call handler functions directly
# ---------------------------------------------------------------------------

class WebhookHandlerRegressionTests(TestCase):
    """
    Regression tests for real-payload bugs found during live webhook testing.
    Call handler functions directly so the failure mode is isolated from the
    dispatch machinery.
    Plans deleted first so the org signal is a no-op.
    """

    def setUp(self):
        Plan.objects.all().delete()
        self.org = Organization.objects.create(name='RegressionOrg', slug='regression-org')

    def test_payment_succeeded_null_parent(self):
        """
        Stripe sends parent=null on one-off invoices (no subscription).
        handle_payment_succeeded must not raise AttributeError and must skip
        Payment creation when there is no subscription_id.

        Regression: `parent.get(...)` on None was AttributeError → 500.
        Fix: `parent = invoice_data.get('parent') or {}`
        """
        invoice = {
            'id': 'in_null_parent_regression',
            'customer': 'cus_regression_abc',
            'parent': None,
            'lines': {'data': []},
        }
        mock_customer = Mock()
        mock_customer.metadata = {'user_id': '99999', 'organization_id': str(self.org.id)}

        before = Payment.objects.count()
        with patch('stripe_meta.views.stripe.Customer.retrieve', return_value=mock_customer):
            handle_payment_succeeded(invoice)   # must not raise

        self.assertEqual(Payment.objects.count(), before)

    def test_subscription_created_missing_org_id(self):
        """
        customer.subscription.created where customer metadata has no organization_id
        (stripe trigger / manually-created customers) must:
          - not raise ValueError
          - log a warning
          - not create any Subscription row
          - return normally so the webhook view returns 200 (stops Stripe retrying)

        Regression: previously raised ValueError → 500 → Stripe retried indefinitely.
        Fix: logger.warning + return (no raise).
        """
        payload = {
            'id': 'sub_no_org_regression',
            'status': 'active',
            'customer': 'cus_no_org_regression',
            'start_date': 1700000000,
            'items': {'data': []},
        }
        mock_customer = Mock()
        mock_customer.metadata = {}   # no organization_id

        before = Subscription.objects.count()
        with patch('stripe_meta.views.stripe.Customer.retrieve', return_value=mock_customer):
            with self.assertLogs('stripe_meta.views', level=logging.WARNING):
                handle_subscription_created(payload)   # must not raise

        self.assertEqual(Subscription.objects.count(), before)


class SubscriptionCreatedTests(TestCase):
    """
    handle_subscription_created: sentinel deactivation (Bug 1 Layer A)
    and seat_count initialisation (Bug 2).
    Plans deleted first so org-creation signal is a no-op.
    """

    def setUp(self):
        Plan.objects.all().delete()
        self.org = Organization.objects.create(name='SubCreatedOrg', slug='sub-created-org')
        self.team_plan = Plan.objects.create(
            name='Team',
            stripe_price_id='price_team_test',
            monthly_token_quota=5_000_000,
            base_price_cents=4900,
            included_seats=5,
        )
        self.mock_customer = Mock()
        self.mock_customer.metadata = {'organization_id': str(self.org.id)}

    def _make_payload(self, status='active'):
        return {
            'id': 'sub_created_test_001',
            'status': status,
            'customer': 'cus_test_001',
            'start_date': 1700000000,
            'items': {
                'data': [{
                    'price': {'id': 'price_team_test'},
                    'current_period_end': 1702678400,
                }]
            },
        }

    def test_subscription_created_deactivates_free_sentinel(self):
        """
        When a real subscription (is_internal=False) is created for an org that
        already has an active Free sentinel (is_internal=True), the sentinel must
        be deactivated.

        Regression: sentinel was left active, causing get_active_real_subscription
        to return Free plan for paid orgs (Bug 1).
        """
        sentinel = Subscription.objects.create(
            organization=self.org,
            plan=self.team_plan,  # plan doesn't matter for this assertion
            stripe_subscription_id='sub_sentinel_free_001',
            start_date='2024-01-01',
            end_date='2099-01-01',
            is_active=True,
            is_internal=True,
        )

        with patch('stripe_meta.views.stripe.Customer.retrieve', return_value=self.mock_customer):
            handle_subscription_created(self._make_payload())

        sentinel.refresh_from_db()
        self.assertFalse(sentinel.is_active, "Free sentinel must be deactivated after real sub is created")

    def test_subscription_created_sets_seat_count(self):
        """
        handle_subscription_created must default seat_count to plan.included_seats
        (Team=5), not the Subscription model default of 1.

        Regression: seat_count was left at 1 because it was missing from the
        update_or_create defaults (Bug 2).
        """
        with patch('stripe_meta.views.stripe.Customer.retrieve', return_value=self.mock_customer):
            handle_subscription_created(self._make_payload())

        sub = Subscription.objects.get(stripe_subscription_id='sub_created_test_001')
        self.assertEqual(sub.seat_count, self.team_plan.included_seats)
        self.assertEqual(sub.seat_count, 5)


class WebhookConcurrentClaimTests(TestCase):
    """
    M-2: the processed_at duplicate check only rejects COMPLETED duplicates.
    The atomic claimed_at claim must stop a concurrent delivery of the same
    event from running the handler twice (e.g. double Payment rows).
    """

    def setUp(self):
        Plan.objects.all().delete()
        self.org = Organization.objects.create(name='ClaimOrg', slug='claim-org')
        self.client = Client(enforce_csrf_checks=False)

    def _post(self, body_dict=None, sig='valid_sig'):
        body = json.dumps(body_dict or {}).encode()
        return self.client.post(
            '/api/stripe/webhook/',
            data=body,
            content_type='application/json',
            HTTP_STRIPE_SIGNATURE=sig,
        )

    def test_claimed_event_returns_409_and_skips_handler(self):
        """
        An event already claimed by an in-flight delivery (claimed_at set,
        processed_at None) must short-circuit with 409 — Stripe retries later,
        by which time the winner has processed (→ duplicate) or failed
        (→ claim released, reprocess). The handler must NOT run.
        """
        event = _make_event('evt_claim_001')
        StripeWebhookEvent.objects.create(
            stripe_event_id='evt_claim_001',
            event_type='checkout.session.completed',
            claimed_at=timezone.now(),
        )

        with patch('stripe_meta.views.stripe.Webhook.construct_event', return_value=event):
            with patch('stripe_meta.views.handle_checkout_completed') as mock_handler:
                response = self._post()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content).get('status'), 'in_flight')
        mock_handler.assert_not_called()

    def test_failed_handler_releases_claim_for_retry(self):
        """
        Handler failure must release the claim (claimed_at back to None) so the
        Stripe retry can claim and reprocess — error bubbling (500) preserved.
        """
        event = _make_event('evt_claim_002')

        with patch('stripe_meta.views.stripe.Webhook.construct_event', return_value=event):
            with patch('stripe_meta.views.handle_checkout_completed') as mock_handler:
                mock_handler.side_effect = RuntimeError('boom')
                r1 = self._post()
                self.assertEqual(r1.status_code, 500)

                evt = StripeWebhookEvent.objects.get(stripe_event_id='evt_claim_002')
                self.assertIsNone(evt.claimed_at, "failed handler must release the claim")
                self.assertIsNone(evt.processed_at)

                # Stripe retry: handler now succeeds — claim re-acquired, processed.
                mock_handler.side_effect = None
                r2 = self._post()
                self.assertEqual(r2.status_code, 200)

        evt.refresh_from_db()
        self.assertIsNotNone(evt.processed_at)


class PaymentSucceededSparseLinesTests(TestCase):
    """
    M-3: lines exist but pricing.price_details lacks price/product →
    Payment fields are null=False, so a bare create would IntegrityError →
    500 → infinite Stripe retry. Must skip-and-warn instead.
    """

    def setUp(self):
        Plan.objects.all().delete()
        self.org = Organization.objects.create(name='SparseOrg', slug='sparse-org')
        self.user = User.objects.create_user(
            email='sparse@test.com', username='sparseuser', password='pw',
        )

    def test_payment_succeeded_missing_pricing_fields_skips(self):
        invoice = {
            'id': 'in_sparse_lines_001',
            'customer': 'cus_sparse_001',
            'parent': {'subscription_details': {'subscription': 'sub_sparse_001'}},
            'lines': {'data': [{'pricing': {'price_details': {}}}]},
        }
        mock_customer = Mock()
        mock_customer.metadata = {'user_id': str(self.user.id), 'organization_id': str(self.org.id)}

        before = Payment.objects.count()
        with patch('stripe_meta.views.stripe.Customer.retrieve', return_value=mock_customer):
            with self.assertLogs('stripe_meta.views', level=logging.WARNING):
                handle_payment_succeeded(invoice)   # must not raise

        self.assertEqual(Payment.objects.count(), before)


class SubscriptionUpdatedMultiItemTests(TestCase):
    """
    M-1: handle_subscription_updated must match line items by price_id, not
    items[0] — base/seat/overage items can arrive in any order — and must sync
    seat_count from the extra-seat item quantity (Stripe-side seat edits).
    """

    def setUp(self):
        Plan.objects.all().delete()
        self.org = Organization.objects.create(name='MultiItemOrg', slug='multi-item-org')
        self.team_plan = Plan.objects.create(
            name='Team',
            stripe_price_id='price_team_base_mi',
            stripe_extra_seat_price_id='price_team_seat_mi',
            stripe_overage_price_id='price_team_overage_mi',
            monthly_token_quota=5_000_000,
            base_price_cents=4900,
            included_seats=5,
        )
        self.sub = Subscription.objects.create(
            organization=self.org,
            plan=self.team_plan,
            stripe_subscription_id='sub_multi_item_001',
            start_date='2026-01-01T00:00:00Z',
            end_date='2026-02-01T00:00:00Z',
            is_active=True,
            is_internal=False,
            seat_count=5,
        )

    def test_seat_item_first_does_not_corrupt_plan_or_period(self):
        """
        Seat item listed FIRST: plan must stay Team, period must come from the
        BASE item, and seat_count must sync to included + seat quantity.
        """
        payload = {
            'id': 'sub_multi_item_001',
            'status': 'active',
            'cancel_at_period_end': False,
            'items': {
                'data': [
                    {   # extra-seat item first — items[0] is NOT the base item
                        'price': {'id': 'price_team_seat_mi'},
                        'quantity': 3,
                        'current_period_start': 1111111111,
                        'current_period_end': 1111111112,
                    },
                    {   # base item carries the authoritative period stamps
                        'price': {'id': 'price_team_base_mi'},
                        'quantity': 1,
                        'current_period_start': 1700000000,
                        'current_period_end': 1702678400,
                    },
                ]
            },
        }

        handle_subscription_updated(payload)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.plan_id, self.team_plan.id, "plan must not change")
        self.assertEqual(int(self.sub.end_date.timestamp()), 1702678400,
                         "period must come from the base item, not items[0]")
        self.assertEqual(self.sub.seat_count, 8,
                         "seat_count must sync to included_seats + seat item quantity")

    def test_seat_removal_on_stripe_side_syncs_down(self):
        """No extra-seat item in the payload → seat_count syncs to included_seats."""
        self.sub.seat_count = 9
        self.sub.save(update_fields=['seat_count'])

        payload = {
            'id': 'sub_multi_item_001',
            'status': 'active',
            'cancel_at_period_end': False,
            'items': {
                'data': [{
                    'price': {'id': 'price_team_base_mi'},
                    'quantity': 1,
                    'current_period_start': 1700000000,
                    'current_period_end': 1702678400,
                }]
            },
        }

        handle_subscription_updated(payload)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.seat_count, 5)


class InvoiceFinalizedReconciliationTests(TestCase):
    """
    M-8: the reconciliation must mirror the ceil + credited-tokens reporting
    model — the old floor math fired a FALSE MISMATCH warning on any month
    with fractional megatokens.
    """

    def setUp(self):
        Plan.objects.all().delete()
        self.org = Organization.objects.create(
            name='ReconOrg', slug='recon-org', stripe_customer_id='cus_recon_001',
        )
        self.plan = Plan.objects.create(
            name='Team',
            stripe_price_id='price_recon_base',
            stripe_overage_price_id='price_recon_overage',
            overage_price_cents_per_1m=500,
            monthly_token_quota=5_000_000,
            base_price_cents=4900,
        )
        self.sub = Subscription.objects.create(
            organization=self.org,
            plan=self.plan,
            stripe_subscription_id='sub_recon_001',
            start_date='2026-01-01T00:00:00Z',
            end_date='2026-02-01T00:00:00Z',
            is_active=True,
            is_internal=False,
        )
        self.ym = timezone.now().strftime('%Y-%m')

    def _invoice(self, overage_amount_cents):
        return {
            'id': 'in_recon_001',
            'customer': 'cus_recon_001',
            'lines': {'data': [{
                'amount': overage_amount_cents,
                'pricing': {'price_details': {'price': 'price_recon_overage'}},
            }]},
        }

    def test_fractional_overage_month_no_false_mismatch(self):
        """
        1.5M overage was reported as ceil = 2 units (credited 2M tokens);
        Stripe invoices 2 x 500c = 1000c. The reconciliation must agree —
        the old floor math expected 1 x 500c and warned MISMATCH.
        """
        UsageMonthly.objects.create(
            organization=self.org,
            year_month=self.ym,
            tokens_used=6_500_000,
            overage_tokens=1_500_000,
            overage_reported_tokens=2_000_000,
        )
        mock_customer = Mock()
        mock_customer.metadata = {'organization_id': str(self.org.id)}

        with patch('stripe_meta.views.stripe.Customer.retrieve', return_value=mock_customer):
            with self.assertNoLogs('stripe_meta.views', level=logging.WARNING):
                handle_invoice_finalized(self._invoice(1000))

    def test_real_mismatch_still_warns(self):
        """A genuine discrepancy must still produce the MISMATCH warning."""
        UsageMonthly.objects.create(
            organization=self.org,
            year_month=self.ym,
            tokens_used=6_500_000,
            overage_tokens=1_500_000,
            overage_reported_tokens=2_000_000,
        )
        mock_customer = Mock()
        mock_customer.metadata = {'organization_id': str(self.org.id)}

        with patch('stripe_meta.views.stripe.Customer.retrieve', return_value=mock_customer):
            with self.assertLogs('stripe_meta.views', level=logging.WARNING) as cm:
                handle_invoice_finalized(self._invoice(99_999))

        self.assertTrue(any('MISMATCH' in m for m in cm.output))

