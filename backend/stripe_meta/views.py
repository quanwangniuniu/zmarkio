import logging
import time
import traceback
import stripe
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from django.http import JsonResponse, HttpResponseRedirect, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .permissions import HasValidOrganizationToken, IsOrganizationAdmin
from .services import (
    InvoiceHistoryError,
    get_active_real_subscription,
    get_seat_availability,
    list_org_invoices,
)
from .models import Plan, Subscription, UsageDaily, UsageMonthly, Payment, StripeWebhookEvent
from .serializers import (
    PlanSerializer, SubscriptionSerializer, UsageDailySerializer, CheckoutSessionSerializer,
    OrganizationSerializer, CreateOrganizationSerializer, OrganizationUserSerializer
)
from .tasks import settle_final_overage
from rest_framework.pagination import PageNumberPagination
from django.db import transaction
from core.models import Organization, CustomUser, Project
from core.services.organization_activity import log_org_activity
from core.slug_mixins import resolve_project_pk

# Configure Stripe
stripe.api_key = settings.STRIPE_SECRET_KEY
logger = logging.getLogger(__name__)

@api_view(['GET'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken])
def list_plans(request):
    """List non-archived subscription plans. Zero Stripe API calls."""
    try:
        plans = Plan.objects.filter(is_archived=False).order_by('base_price_cents')
        serializer = PlanSerializer(plans, many=True)
        return Response({
            'count': plans.count(),
            'results': serializer.data,
        })
    except Exception as e:
        return Response(
            {'error': str(e), 'code': 'PLANS_RETRIEVAL_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_payments(request):
    """List recent Stripe invoices for the authenticated user's organization."""
    organization = getattr(request.user, 'organization', None)
    if not organization:
        return Response(
            {'error': 'Organization not found', 'code': 'NO_ORG'},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        return Response(list_org_invoices(organization))
    except InvoiceHistoryError as exc:
        return Response(
            {'error': str(exc), 'code': 'INVOICE_HISTORY_ERROR'},
            status=status.HTTP_502_BAD_GATEWAY,
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken, IsOrganizationAdmin])
def switch_plan(request):
    """Switch user's subscription to a different plan"""
    try:
        plan_id = request.data.get('plan_id')
        if not plan_id:
            return Response(
                {'error': 'plan_id is required', 'code': 'MISSING_PLAN_ID'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        user = request.user
        
        try:
            new_plan = Plan.objects.get(id=plan_id)
        except Plan.DoesNotExist:
            return Response(
                {'error': 'Plan not found', 'code': 'PLAN_NOT_FOUND'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get current subscription — prefer real (is_internal=False) over sentinel.
        # order_by('is_internal') puts False(0) before True(1).
        current_subscription = (
            Subscription.objects.filter(organization=user.organization, is_active=True)
            .order_by('is_internal')
            .first()
        )

        if not current_subscription:
            return Response(
                {'error': 'No active subscription found', 'code': 'NO_SUBSCRIPTION'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Free sentinel (is_internal=True) has no Stripe subscription to modify —
        # direct the client to the checkout flow instead.
        if current_subscription.is_internal:
            return Response({'redirect_to': 'checkout'}, status=status.HTTP_200_OK)

        # Check if already on the same plan
        if current_subscription.plan.id == new_plan.id:
            return Response(
                {'error': 'Already subscribed to this plan', 'code': 'SAME_PLAN'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Downgrade to a plan with no Stripe price (i.e. the Free plan).
        # We cannot swap Stripe subscription items to a plan with no price_id.
        # Instead, schedule cancellation at the end of the current billing period:
        # the user keeps paid benefits until then, and the subscription.deleted
        # webhook will reactivate the Free sentinel when the period actually ends.
        if not new_plan.stripe_price_id:
            stripe_subscription = stripe.Subscription.retrieve(
                current_subscription.stripe_subscription_id
            )
            period_end = stripe_subscription.get('current_period_end')
            stripe.Subscription.modify(
                current_subscription.stripe_subscription_id,
                cancel_at_period_end=True,
            )

            current_subscription.cancel_at_period_end = True
            current_subscription.save(update_fields=['cancel_at_period_end'])

            return Response(
                {'status': 'scheduled_downgrade', 'effective_at': period_end},
                status=status.HTTP_200_OK,
            )

        # Retrieve subscription from Stripe and locate the base-price item by price_id.
        # Using price_id match rather than items.data[0] because multi-item subscriptions
        # (base + seat + overage) can have the base item at any index.
        stripe_subscription = stripe.Subscription.retrieve(current_subscription.stripe_subscription_id)
        current_item = next(
            (
                item for item in stripe_subscription['items']['data']
                if item['price']['id'] == current_subscription.plan.stripe_price_id
            ),
            None,
        )
        if not current_item:
            return Response(
                {'error': 'Base plan item not found in Stripe subscription', 'code': 'ITEM_NOT_FOUND'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        current_item_id = current_item['id']
        
        # Get prices to determine if upgrade or downgrade
        current_price_data = stripe.Price.retrieve(current_subscription.plan.stripe_price_id)
        new_price_data = stripe.Price.retrieve(new_plan.stripe_price_id)
        current_price = current_price_data.unit_amount / 100  # Convert cents to dollars
        new_price = new_price_data.unit_amount / 100
        
        is_upgrade = new_price > current_price
        
        # Update subscription in Stripe based on upgrade/downgrade
        if is_upgrade:
            # UPGRADE: Immediate switch with proration
            stripe.Subscription.modify(
                current_subscription.stripe_subscription_id,
                items=[{
                    'id': current_item_id,
                    'price': new_plan.stripe_price_id,
                }],
                proration_behavior='always_invoice'  # Charge prorated amount immediately
            )
            # DON'T update local subscription - webhook will handle it
            # The subscription.updated webhook will update the plan when upgrade completes
            
            return Response({
                'requested': True
            })
        else:
            # DOWNGRADE: Immediate switch with no refund
            stripe.Subscription.modify(
                current_subscription.stripe_subscription_id,
                items=[{
                    'id': current_item_id,
                    'price': new_plan.stripe_price_id,
                }],
                proration_behavior='none'  # No refund or proration
            )
            # DON'T update local subscription - webhook will handle it
            # The subscription.updated webhook will update the plan when downgrade completes

            # Settle accrued overage before the Team metered item goes away on
            # downgrade — same idempotent helper, current month.
            settle_ym = timezone.now().strftime('%Y-%m')
            settle_org_id = current_subscription.organization_id
            transaction.on_commit(
                lambda org_id=settle_org_id, ym=settle_ym: settle_final_overage.delay(org_id, ym)
            )

            return Response({
                'requested': True
            })
        
    except stripe.StripeError as e:
        return Response(
            {'error': str(e), 'code': 'STRIPE_ERROR'},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        return Response(
            {'error': str(e), 'code': 'PLAN_SWITCH_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken])
def get_subscription(request):
    """Get current user's subscription"""
    try:
        user = request.user
        subscription = get_active_real_subscription(user.organization)

        if not subscription:
            return Response(
                {'error': 'No active subscription found', 'code': 'NO_SUBSCRIPTION'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = SubscriptionSerializer(subscription)
        member_count = CustomUser.objects.filter(organization=user.organization).count()
        return Response({**serializer.data, 'member_count': member_count})
    except Exception as e:
        return Response(
            {'error': str(e), 'code': 'SUBSCRIPTION_RETRIEVAL_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['POST'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken, IsOrganizationAdmin])
def cancel_subscription(request):
    """Schedule subscription cancellation at the end of the current billing period."""
    try:
        user = request.user
        subscription = get_active_real_subscription(user.organization)

        if not subscription:
            return Response(
                {'error': 'No active subscription found', 'code': 'NO_SUBSCRIPTION'},
                status=status.HTTP_400_BAD_REQUEST
            )

        stripe_sub = stripe.Subscription.retrieve(subscription.stripe_subscription_id)
        period_end = stripe_sub.get('current_period_end')

        stripe.Subscription.modify(
            subscription.stripe_subscription_id,
            cancel_at_period_end=True,
        )

        subscription.cancel_at_period_end = True
        subscription.save(update_fields=['cancel_at_period_end'])

        # Settle accrued overage now, while the sub is still alive and meterable.
        # A period-end cancel goes is_active=False before the monthly batch runs,
        # so its final partial month would otherwise be dropped. on_commit so we
        # only settle if the cancel actually persisted; the helper is idempotent.
        settle_ym = timezone.now().strftime('%Y-%m')
        settle_org_id = subscription.organization_id
        transaction.on_commit(
            lambda org_id=settle_org_id, ym=settle_ym: settle_final_overage.delay(org_id, ym)
        )

        return Response({'success': True, 'cancel_at': period_end})

    except stripe.StripeError as e:
        return Response(
            {'error': str(e), 'code': 'STRIPE_ERROR'},
            status=status.HTTP_400_BAD_REQUEST
        )
    except Exception as e:
        return Response(
            {'error': str(e), 'code': 'CANCEL_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken, IsOrganizationAdmin])
def preview_seat_purchase(request):
    """
    Preview the proration charge for increasing seat count.
    Returns amount_due (what Stripe will charge immediately) and the proration_date
    that must be passed back to purchase_seats so the amounts match exactly.
    """
    try:
        org = request.user.organization
        if not org:
            return Response(
                {'error': 'No organization found', 'code': 'NO_ORGANIZATION'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            new_seat_count = int(request.query_params.get('seat_count', ''))
        except (TypeError, ValueError):
            return Response(
                {'error': 'seat_count query param must be a positive integer', 'code': 'INVALID_SEAT_COUNT'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_seat_count < 1:
            return Response(
                {'error': 'seat_count must be a positive integer', 'code': 'INVALID_SEAT_COUNT'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sub = get_active_real_subscription(org)

        if not sub or sub.is_internal:
            return Response(
                {
                    'error': 'Seat purchase is not available on the Free plan. Upgrade to Team first.',
                    'code': 'FREE_PLAN_CANNOT_PURCHASE_SEATS',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        plan = sub.plan
        if not plan.stripe_extra_seat_price_id:
            return Response(
                {'error': 'Current plan does not support extra seats.', 'code': 'NO_EXTRA_SEAT_PRICE'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_seat_count = sub.seat_count
        member_count = CustomUser.objects.filter(organization=org).count()

        if new_seat_count <= current_seat_count:
            return Response(
                {
                    'error': (
                        f'New seat count ({new_seat_count}) must be greater than current '
                        f'({current_seat_count}).'
                    ),
                    'code': 'SEAT_COUNT_NOT_INCREASED',
                    'current_seat_count': current_seat_count,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_seat_count < member_count:
            return Response(
                {
                    'error': (
                        f'Cannot set seat count below current member count ({member_count}).'
                    ),
                    'code': 'SEAT_COUNT_BELOW_MEMBERS',
                    'member_count': member_count,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        proration_date = int(time.time())

        stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
        extra_seat_item = next(
            (
                item for item in stripe_sub['items']['data']
                if item['price']['id'] == plan.stripe_extra_seat_price_id
            ),
            None,
        )

        new_extra_qty = max(0, new_seat_count - (plan.included_seats or 1))

        if extra_seat_item:
            preview_items = [{'id': extra_seat_item['id'], 'quantity': new_extra_qty}]
        else:
            preview_items = [{'price': plan.stripe_extra_seat_price_id, 'quantity': new_extra_qty}]

        invoice = stripe.Invoice.create_preview(
            customer=stripe_sub['customer'],
            subscription=sub.stripe_subscription_id,
            subscription_details={
                'items': preview_items,
                'proration_date': proration_date,
            },
        )

        extra_seats = max(0, new_seat_count - (plan.included_seats or 1))
        monthly_total_cents = (plan.base_price_cents or 0) + extra_seats * (plan.extra_seat_price_cents or 0)

        return Response(
            {
                'proration_now_cents': invoice.amount_due,
                'monthly_total_cents': monthly_total_cents,
                'proration_date': proration_date,
            },
            status=status.HTTP_200_OK,
        )

    except stripe.StripeError as e:
        return Response(
            {'error': str(e), 'code': 'STRIPE_ERROR'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:
        logger.exception("preview_seat_purchase unexpected error")
        return Response(
            {'error': str(e), 'code': 'PREVIEW_SEATS_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken, IsOrganizationAdmin])
def purchase_seats(request):
    """
    Purchase additional seats (Model B).
    Increases seat_count on the Stripe extra-seat line item with immediate proration.
    Only available on paid plans; Free orgs must upgrade to Team first.
    """
    try:
        org = request.user.organization
        if not org:
            return Response(
                {'error': 'No organization found', 'code': 'NO_ORGANIZATION'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_seat_count = request.data.get('seat_count')
        proration_date = request.data.get('proration_date')
        if not isinstance(new_seat_count, int) or new_seat_count < 1:
            return Response(
                {'error': 'seat_count must be a positive integer', 'code': 'INVALID_SEAT_COUNT'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sub = get_active_real_subscription(org)

        if not sub or sub.is_internal:
            return Response(
                {
                    'error': 'Seat purchase is not available on the Free plan. Upgrade to Team first.',
                    'code': 'FREE_PLAN_CANNOT_PURCHASE_SEATS',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        plan = sub.plan
        if not plan.stripe_extra_seat_price_id:
            return Response(
                {'error': 'Current plan does not support extra seats.', 'code': 'NO_EXTRA_SEAT_PRICE'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_seat_count = sub.seat_count
        member_count = CustomUser.objects.filter(organization=org).count()

        if new_seat_count <= current_seat_count:
            return Response(
                {
                    'error': (
                        f'New seat count ({new_seat_count}) must be greater than current '
                        f'({current_seat_count}). To reduce seats, contact support.'
                    ),
                    'code': 'SEAT_COUNT_NOT_INCREASED',
                    'current_seat_count': current_seat_count,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_seat_count < member_count:
            return Response(
                {
                    'error': (
                        f'Cannot set seat count below current member count ({member_count}). '
                        'Remove members first or choose a higher seat count.'
                    ),
                    'code': 'SEAT_COUNT_BELOW_MEMBERS',
                    'member_count': member_count,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Locate extra-seat item by price_id (not by array index — multi-item subs vary).
        stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
        extra_seat_item = next(
            (
                item for item in stripe_sub['items']['data']
                if item['price']['id'] == plan.stripe_extra_seat_price_id
            ),
            None,
        )

        new_extra_qty = max(0, new_seat_count - (plan.included_seats or 1))

        modify_kwargs = {'proration_behavior': 'create_prorations'}
        if isinstance(proration_date, int):
            modify_kwargs['proration_date'] = proration_date

        if extra_seat_item:
            item_patch = {'id': extra_seat_item['id'], 'quantity': new_extra_qty}
            stripe.Subscription.modify(
                sub.stripe_subscription_id,
                items=[item_patch],
                **modify_kwargs,
            )
        elif new_extra_qty > 0:
            # No extra-seat item yet (org was at exactly included_seats); add the item.
            item_patch = {'price': plan.stripe_extra_seat_price_id, 'quantity': new_extra_qty}
            stripe.Subscription.modify(
                sub.stripe_subscription_id,
                items=[item_patch],
                **modify_kwargs,
            )
        # else: new_seat_count <= included_seats — no Stripe change needed, just update DB.

        Subscription.objects.filter(pk=sub.pk).update(seat_count=new_seat_count)

        extra_seats = max(0, new_seat_count - (plan.included_seats or 1))
        monthly_total_cents = (plan.base_price_cents or 0) + extra_seats * (plan.extra_seat_price_cents or 0)

        logger.info(
            "purchase_seats org=%s sub=%s seat_count %d→%d",
            org.id, sub.stripe_subscription_id, current_seat_count, new_seat_count,
        )

        return Response(
            {'seat_count': new_seat_count, 'monthly_total_cents': monthly_total_cents},
            status=status.HTTP_200_OK,
        )

    except stripe.StripeError as e:
        return Response(
            {'error': str(e), 'code': 'STRIPE_ERROR'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:
        logger.exception("purchase_seats unexpected error")
        return Response(
            {'error': str(e), 'code': 'PURCHASE_SEATS_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_organization(request):
    """Create a new organization"""
    try:
        if request.user.organization:
            return Response(
                {'error': 'User is already in an organization', 'code': 'USER_ALREADY_IN_ORGANIZATION'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate input data using serializer
        serializer = CreateOrganizationSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = serializer.validated_data

        from django.db import connection
        from core.services.tenant import slug_to_schema_name

        organization = Organization.objects.create(
            name=validated_data['name'],
            desc=validated_data.get('description', ''),
            email_domain=validated_data.get('email_domain', '')
        )

        # Assign user to organization
        user = request.user
        user.organization = organization
        user.save()

        # CRITICAL: Switch search_path immediately after creating org so any
        # subsequent operations in this request (or future org-specific logic)
        # use the correct tenant schema.
        schema_name = slug_to_schema_name(organization.slug)
        with connection.cursor() as c:
            c.execute('SET search_path TO %s, public', [schema_name])

        # Align with all other org-creation paths: CSM records + billing admin
        from customer.models import CustomerOrganisation
        from csm.models import CustomerUser
        from core.admin_utils import assign_org_admin
        cust_org, _ = CustomerOrganisation.objects.get_or_create(
            organization=organization,
            defaults={'name': organization.name},
        )
        CustomerUser.objects.get_or_create(
            user=user,
            organisation=cust_org,
            defaults={
                'user_type': 'admin',
                'is_active': True,
                'is_creator': True,
            },
        )
        assign_org_admin(user, organization)

        serializer = OrganizationSerializer(organization)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    except Exception as e:
        return Response(
            {'error': str(e), 'code': 'ORGANIZATION_CREATION_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['POST'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken, IsOrganizationAdmin])
def invite_users_to_organization(request):
    """Invite users to organization by email"""
    try:
        user = request.user
        
        if not user.organization:
            return Response(
                {'error': 'User is not in any organization', 'code': 'NO_ORGANIZATION'},
                status=status.HTTP_400_BAD_REQUEST
            )
        emails = request.data.get('emails')
        if not emails or not isinstance(emails, list) or len(emails) == 0:
            return Response(
                {'error': 'No emails provided', 'code': 'NO_EMAILS_PROVIDED'},
                status=status.HTTP_400_BAD_REQUEST
            )
        organization = user.organization

        # Seat cap check (Model B): enforce purchased seat limit atomically with the
        # member adds — select_for_update on the subscription row serializes
        # concurrent invites so two requests at cap-1 can't both pass (check-then-act
        # race). Covers both paid plans and Free orgs (sentinel seat_count=1).
        # sub=None (org has no subscription at all) is treated as uncapped — don't block.
        with transaction.atomic():
            sub = (
                Subscription.objects.select_for_update()
                .filter(organization=organization, is_active=True)
                .order_by('is_internal')
                .select_related('plan')
                .first()
            )
            if sub:
                current_count = CustomUser.objects.filter(organization=organization).count()
                available = sub.seat_count - current_count
                if len(emails) > available:
                    is_free = sub.is_internal  # sentinel ↔ Free tier; paid sub ↔ Team/higher
                    return Response(
                        {
                            'error': (
                                'Free plan is limited to 1 seat. Upgrade to Team to add more members.'
                                if is_free
                                else 'Seat limit reached. Purchase more seats to add more members.'
                            ),
                            'code': 'SEAT_LIMIT_REACHED',
                            'seats_available': max(0, available),
                            'seats_purchased': sub.seat_count,
                            'upgrade_required': is_free,
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )

            try:
                for email in emails:
                    user = CustomUser.objects.filter(email=email).first()
                    if not user:
                        raise Exception(f'User {email} not found')
                    elif user.organization:
                        raise Exception(f'User {email} is already a member of an organization')
                    else:
                        user.organization = organization
                        user.save()
            except Exception as e:
                return Response(
                    {'error': str(e), 'code': 'INVITE_USERS_ERROR'},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        return Response({
            'success': True
        })

    except Exception as e:
        return Response(
            {'error': str(e), 'code': 'INVITE_USERS_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['POST'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken])
def leave_organization(request):
    """Remove current user from their organization"""
    user = request.user
    org = user.organization

    if not org:
        return Response(
            {'error': 'User is not in any organization', 'code': 'NO_ORGANIZATION'},
            status=status.HTTP_403_FORBIDDEN
        )

    # Require an explicit org token — do not accept the onboarding exemption
    # (no-token fallback) for this destructive action.
    if not request.META.get('HTTP_X_ORGANIZATION_TOKEN'):
        return Response(
            {'error': 'Organization token required', 'code': 'TOKEN_REQUIRED'},
            status=status.HTTP_403_FORBIDDEN
        )

    try:
        user.organization = None
        user.save()

        return Response({
            'success': True
        })

    except Exception as e:
        return Response(
            {'error': str(e), 'code': 'LEAVE_ORGANIZATION_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['POST'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken])
def create_checkout_session(request):
    """Create Stripe checkout session for token-based subscription."""
    try:
        serializer = CheckoutSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        plan_id = serializer.validated_data['plan_id']
        success_url = serializer.validated_data['success_url']
        cancel_url = serializer.validated_data['cancel_url']
        seat_count = serializer.validated_data.get('seat_count', 1)

        plan = Plan.objects.get(id=plan_id)
        user = request.user
        org = user.organization

        # Free sentinel subscriptions are is_internal=True — do not treat them as
        # "already subscribed". Only block on a real paid active subscription.
        existing = Subscription.objects.filter(
            organization=org,
            is_active=True,
            is_internal=False,
        ).first()
        if existing:
            return Response(
                {
                    'error': 'Organization already has an active subscription. Use switch plan to change your plan.',
                    'code': 'SUBSCRIPTION_EXISTS',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Customer caching — select_for_update prevents duplicate Customer.create
        # calls under concurrent checkout clicks (concurrency bug fix).
        with transaction.atomic():
            org_locked = Organization.objects.select_for_update().get(pk=org.pk)
            if org_locked.stripe_customer_id:
                customer_id = org_locked.stripe_customer_id
            else:
                customer = stripe.Customer.create(
                    name=org_locked.name,
                    email=user.email,
                    metadata={
                        'user_id': str(user.id),
                        'organization_id': str(org_locked.id),
                    },
                )
                org_locked.stripe_customer_id = customer.id
                org_locked.save(update_fields=['stripe_customer_id'])
                customer_id = customer.id

        # Three-tier line items: base + extra seats + metered overage
        line_items = [{'price': plan.stripe_price_id, 'quantity': 1}]
        extra_seats = seat_count - (plan.included_seats or 1)
        if extra_seats > 0 and plan.stripe_extra_seat_price_id:
            line_items.append({
                'price': plan.stripe_extra_seat_price_id,
                'quantity': extra_seats,
            })
        if plan.stripe_overage_price_id:
            line_items.append({'price': plan.stripe_overage_price_id})

        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=['card'],
            line_items=line_items,
            mode='subscription',
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                'user_id': str(user.id),
                'organization_id': str(org.id),
                'seat_count': str(seat_count),
            },
        )

        return Response({'checkout_url': session.url}, status=status.HTTP_200_OK)

    except Plan.DoesNotExist:
        return Response(
            {'error': 'Plan not found', 'code': 'PLAN_NOT_FOUND'},
            status=status.HTTP_404_NOT_FOUND,
        )
    except stripe.StripeError as e:
        return Response(
            {'error': str(e), 'code': 'STRIPE_ERROR'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as e:
        logger.exception("create_checkout_session unexpected error")
        return Response(
            {'error': str(e), 'code': 'CHECKOUT_SESSION_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken])
def get_usage(request):
    """Get current user's usage statistics"""
    try:
        user = request.user
        
        # Check if organization has an active subscription
        has_active_subscription = Subscription.objects.filter(
            organization=user.organization,
            is_active=True
        ).exists()
        
        if not has_active_subscription:
            return Response({
                'error': 'No active subscription',
                'code': 'NO_ACTIVE_SUBSCRIPTION'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        usage = UsageDaily.objects.filter(user=user).first()
        
        if not usage:
            return Response({
                'error': 'No usage found',
                'code': 'NO_USAGE_FOUND'
            }, status=status.HTTP_404_NOT_FOUND)
        
        serializer = UsageDailySerializer(usage)
        return Response(serializer.data)
        
    except Exception as e:
        return Response(
            {'error': str(e), 'code': 'USAGE_RETRIEVAL_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def quota_preview(request):
    """
    Return current-month token usage + plan quota for a project's organization.

    Query param: project_id (required)
    Response:
      {
        "tokens_used": int,
        "tokens_reserved": int,
        "overage_tokens": int,
        "monthly_token_quota": int | null,   // null = unlimited
        "year_month": "YYYY-MM"
      }
    """
    project_id = request.query_params.get('project_id')
    if not project_id:
        return Response(
            {'error': 'project_id is required', 'code': 'MISSING_PROJECT_ID'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # The frontend passes the project slug (project routes are slug-only).
    # resolve_project_pk accepts either a numeric pk (legacy) or a slug.
    try:
        project = Project.objects.select_related('organization').get(id=resolve_project_pk(project_id))
    except Project.DoesNotExist:
        return Response(
            {'error': 'Project not found', 'code': 'PROJECT_NOT_FOUND'},
            status=status.HTTP_404_NOT_FOUND,
        )

    org = project.organization
    if not org:
        return Response(
            {'error': 'Project has no linked organization', 'code': 'NO_ORG'},
            status=status.HTTP_404_NOT_FOUND,
        )

    ym = timezone.now().strftime('%Y-%m')
    usage = UsageMonthly.objects.filter(organization=org, year_month=ym).first()

    sub = (
        Subscription.objects.filter(organization=org, is_active=True)
        .select_related('plan')
        .order_by('is_internal')
        .first()
    )
    quota = sub.plan.monthly_token_quota if sub and sub.plan else None
    overage_price = sub.plan.overage_price_cents_per_1m if sub and sub.plan else None

    return Response({
        'project_name': project.name,
        'tokens_used': usage.tokens_used if usage else 0,
        'tokens_reserved': usage.tokens_reserved if usage else 0,
        'overage_tokens': usage.overage_tokens if usage else 0,
        'monthly_token_quota': quota,
        'overage_price_cents_per_1m': overage_price,
        'year_month': ym,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken])
def org_token_summary(request):
    """Return current-month token usage + plan quota for the requesting user's organization."""
    org = request.user.organization
    if not org:
        return Response(
            {'error': 'User has no linked organization', 'code': 'NO_ORG'},
            status=status.HTTP_404_NOT_FOUND,
        )

    ym = timezone.now().strftime('%Y-%m')
    usage = UsageMonthly.objects.filter(organization=org, year_month=ym).first()

    sub = (
        Subscription.objects.filter(organization=org, is_active=True)
        .select_related('plan')
        .order_by('is_internal')
        .first()
    )
    plan = sub.plan if sub else None

    return Response({
        'tokens_used': usage.tokens_used if usage else 0,
        'overage_tokens': usage.overage_tokens if usage else 0,
        'monthly_token_quota': plan.monthly_token_quota if plan else None,
        'overage_price_cents_per_1m': plan.overage_price_cents_per_1m if plan else None,
        'currency': plan.currency if plan else 'AUD',
    })

def _construct_webhook_event(payload, sig_header):
    secrets = [
        settings.STRIPE_WEBHOOK_SECRET,
        getattr(settings, 'STRIPE_WEBHOOK_SECRET_NEXT', ''),
    ]

    last_signature_error = None

    for secret in secrets:
        if not secret:
            continue

        try:
            return stripe.Webhook.construct_event(
                payload,
                sig_header,
                secret,
            )
        except stripe.SignatureVerificationError as exc:
            last_signature_error = exc

    if last_signature_error is not None:
        raise last_signature_error

    raise stripe.SignatureVerificationError(
        'No Stripe webhook signing secret configured',
        sig_header,
    )

@csrf_exempt
@require_http_methods(["POST"])
def stripe_webhook(request):
    """Handle Stripe webhook events with idempotency and error bubbling."""
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')

    try:
        event = _construct_webhook_event(payload, sig_header)
    except ValueError:
        return JsonResponse({'error': 'Invalid payload'}, status=400)
    except stripe.SignatureVerificationError:
        return JsonResponse({'error': 'Invalid signature'}, status=400)
    except Exception:
        logger.exception("stripe_webhook construct_event failed")
        return JsonResponse({'error': 'Webhook processing error', 'code': 'WEBHOOK_ERROR'}, status=500)

    event_id = event['id']
    event_type = event['type']

    webhook_event, created = StripeWebhookEvent.objects.get_or_create(
        stripe_event_id=event_id,
        defaults={'event_type': event_type},
    )
    if not created and webhook_event.processed_at is not None:
        logger.info("stripe_webhook duplicate event_id=%s event_type=%s", event_id, event_type)
        return JsonResponse({'received': True, 'status': 'duplicate'})

    # Atomic claim: only one concurrent delivery of the same event may proceed.
    # The conditional UPDATE is the lock — exactly one delivery flips claimed_at
    # from NULL. The loser returns 409 so Stripe retries later; by then the winner
    # has either set processed_at (retry → duplicate) or failed and released the
    # claim (retry → reprocess).
    claimed = StripeWebhookEvent.objects.filter(
        pk=webhook_event.pk, claimed_at__isnull=True,
    ).update(claimed_at=timezone.now())
    if not claimed:
        logger.info(
            "stripe_webhook event already claimed by a concurrent delivery "
            "event_id=%s event_type=%s", event_id, event_type,
        )
        return JsonResponse({'received': False, 'status': 'in_flight'}, status=409)

    handlers = {
        'checkout.session.completed': handle_checkout_completed,
        'customer.subscription.created': handle_subscription_created,
        'customer.subscription.updated': handle_subscription_updated,
        'customer.subscription.deleted': handle_subscription_deleted,
        'invoice.payment_succeeded': handle_payment_succeeded,
        'invoice.payment_failed': handle_payment_failed,
        'invoice.created': handle_invoice_created,
        'invoice.finalized': handle_invoice_finalized,
    }

    handler = handlers.get(event_type)
    if handler is None:
        webhook_event.processed_at = timezone.now()
        webhook_event.save(update_fields=['processed_at'])
        logger.info("stripe_webhook ignored event_id=%s event_type=%s", event_id, event_type)
        return JsonResponse({'received': True, 'status': 'ignored'})

    try:
        handler(event['data']['object'], event_id=event_id)
        webhook_event.processed_at = timezone.now()
        webhook_event.save(update_fields=['processed_at'])
        return JsonResponse({'received': True})
    except Exception:
        # Release the claim so Stripe's retry can reprocess; keep error bubbling (500).
        webhook_event.error_message = traceback.format_exc()[:2000]
        webhook_event.claimed_at = None
        webhook_event.save(update_fields=['error_message', 'claimed_at'])
        logger.exception("stripe_webhook handler failed event_id=%s event_type=%s", event_id, event_type)
        return JsonResponse({'error': 'Handler failed'}, status=500)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


@api_view(['GET'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken])
def list_organization_users(request):
    """List users in the authenticated user's organization with pagination"""
    try:
        if not request.user.organization:
            return Response(
                {'error': 'No organization found for user', 'code': 'NO_ORGANIZATION'},
                status=status.HTTP_400_BAD_REQUEST
            )

        qs = CustomUser.objects.filter(organization=request.user.organization).order_by('id')
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = OrganizationUserSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
    except Exception as e:
        return Response(
            {'error': str(e), 'code': 'ORG_USERS_LIST_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['DELETE'])
@permission_classes([IsAuthenticated, HasValidOrganizationToken, IsOrganizationAdmin])
def remove_organization_user(request, user_id: int):
    """Remove a user from the authenticated user's organization by user_id"""
    try:
        org = request.user.organization
        if not org:
            return Response(
                {'error': 'No organization found for user', 'code': 'NO_ORGANIZATION'},
                status=status.HTTP_400_BAD_REQUEST
            )

        target = CustomUser.objects.filter(id=user_id, organization=org).first()
        if not target:
            return Response(
                {'error': 'User not found in organization', 'code': 'USER_NOT_IN_ORG'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Allow anyone in org to remove any user for now (no roles yet)
        target.organization = None
        target.save()

        return Response({'success': True})
    except Exception as e:
        return Response(
            {'error': str(e), 'code': 'ORG_USER_REMOVE_ERROR'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

def handle_checkout_completed(session, event_id=None):
    """Handle successful checkout session completion."""
    org_id = (session.get('metadata') or {}).get('organization_id')
    logger.info("handle_checkout_completed enter event_id=%s org_id=%s", event_id, org_id)
    # No-op: subscription/invoice webhooks are the source of truth for DB updates.
    logger.info("handle_checkout_completed exit event_id=%s org_id=%s", event_id, org_id)


def handle_subscription_created(subscription_data, event_id=None):
    """Handle new subscription creation."""
    subscription_id = subscription_data.get('id')
    customer_id = subscription_data.get('customer')
    org_id = None
    if customer_id:
        customer = stripe.Customer.retrieve(customer_id)
        org_id = customer.metadata.get('organization_id')

    logger.info(
        "handle_subscription_created enter event_id=%s subscription_id=%s org_id=%s",
        event_id, subscription_id, org_id,
    )

    if not org_id:
        logger.warning(
            "handle_subscription_created: no org_id in customer metadata "
            "subscription_id=%s customer_id=%s event_id=%s — skipping",
            subscription_id, customer_id, event_id,
        )
        return

    organization = Organization.objects.filter(id=org_id).first()
    if not organization:
        logger.warning(
            "handle_subscription_created: org %s not found subscription_id=%s event_id=%s — skipping",
            org_id, subscription_id, event_id,
        )
        return

    items = subscription_data.get('items', {}).get('data', [])
    price_id = items[0]['price']['id'] if items else None
    plan = Plan.objects.filter(stripe_price_id=price_id).first() if price_id else None

    start_date = subscription_data.get('start_date')
    end_date = items[0].get('current_period_end') if items else None

    # Locate the overage line item for later reference (Meter Events use customer_id,
    # but store it here for reconciliation).
    overage_price_id = plan.stripe_overage_price_id if plan else None
    overage_item = next(
        (item for item in items if item.get('price', {}).get('id') == overage_price_id),
        None,
    ) if overage_price_id else None

    # Derive purchased seat_count from the extra-seat line item quantity.
    # extra-seat qty + included_seats = total seats the user bought at checkout.
    # Authoritative source: Stripe items reflect the actual purchase; session metadata is not read here.
    extra_seat_price_id = plan.stripe_extra_seat_price_id if plan else None
    extra_seat_item = next(
        (item for item in items if item.get('price', {}).get('id') == extra_seat_price_id),
        None,
    ) if extra_seat_price_id else None
    extra_seat_qty = extra_seat_item['quantity'] if extra_seat_item else 0
    seat_count_purchased = (plan.included_seats or 1) + extra_seat_qty

    subscription, created = Subscription.objects.update_or_create(
        stripe_subscription_id=subscription_id,
        defaults={
            'organization': organization,
            'plan': plan,
            'start_date': datetime.fromtimestamp(start_date) if start_date else None,
            'end_date': datetime.fromtimestamp(end_date) if end_date else None,
            'is_active': subscription_data.get('status') == 'active',
            'stripe_overage_item_id': overage_item['id'] if overage_item else None,
            'seat_count': seat_count_purchased,
        },
    )

    if subscription.is_active and not subscription.is_internal:
        deactivated = Subscription.objects.filter(
            organization=organization,
            is_internal=True,
            is_active=True,
        ).update(is_active=False)
        if deactivated:
            logger.info(
                "handle_subscription_created deactivated %d Free sentinel(s) for org %s",
                deactivated, org_id,
            )

    if subscription.is_active and not subscription.is_internal and plan:
        log_org_activity(
            organization,
            'plan_subscribed',
            metadata={'plan_name': plan.name, 'seat_count': subscription.seat_count},
        )

    logger.info(
        "handle_subscription_created exit event_id=%s org_id=%s created=%s",
        event_id, org_id, created,
    )


def handle_payment_succeeded(invoice_data, event_id=None):
    """Handle successful payment."""
    customer_id = invoice_data.get('customer')
    logger.info("handle_payment_succeeded enter event_id=%s customer_id=%s", event_id, customer_id)

    parent = invoice_data.get('parent') or {}
    subscription_details = parent.get('subscription_details', {})
    stripe_subscription_id = subscription_details.get('subscription')

    customer = stripe.Customer.retrieve(customer_id) if customer_id else None
    user_id = customer.metadata.get('user_id') if customer else None
    org_id = customer.metadata.get('organization_id') if customer else None
    user = CustomUser.objects.filter(id=user_id).first()

    lines = invoice_data.get('lines', {}).get('data', [])
    if lines:
        pricing = lines[0].get('pricing', {})
        price_details = pricing.get('price_details', {})
        price_id = price_details.get('price')
        product_id = price_details.get('product')
    else:
        price_id = None
        product_id = None

    if stripe_subscription_id and user:
        invoice_id = invoice_data.get('id')
        # Payment fields are null=False — a sparse lines payload (one-off invoice
        # items, API shape drift) would IntegrityError → 500 → infinite Stripe
        # retry. Skip-and-warn instead, matching the null-parent / missing-org_id
        # handling in the other handlers.
        if not price_id or not product_id:
            logger.warning(
                "handle_payment_succeeded: missing price/product in invoice lines "
                "invoice_id=%s price_id=%s product_id=%s event_id=%s — skipping Payment record",
                invoice_id, price_id, product_id, event_id,
            )
            return
        Payment.objects.create(
            user=user,
            stripe_invoice_id=invoice_id,
            stripe_subscription_id=stripe_subscription_id,
            stripe_product_id=product_id,
            stripe_price_id=price_id,
            stripe_customer_id=customer_id,
            is_active=True,
        )
        logger.info(
            "handle_payment_succeeded exit event_id=%s org_id=%s invoice_id=%s",
            event_id, org_id, invoice_id,
        )
    else:
        logger.info(
            "handle_payment_succeeded exit event_id=%s org_id=%s — skipped (missing subscription or user)",
            event_id, org_id,
        )


def handle_subscription_updated(subscription_data, event_id=None):
    """Handle subscription updates (plan changes, renewals)."""
    subscription_id = subscription_data['id']
    logger.info("handle_subscription_updated enter event_id=%s subscription_id=%s", event_id, subscription_id)

    try:
        subscription = Subscription.objects.get(stripe_subscription_id=subscription_id)
    except Subscription.DoesNotExist:
        logger.warning("handle_subscription_updated: subscription %s not found, ignoring", subscription_id)
        return

    org_id = subscription.organization_id
    old_plan_name = subscription.plan.name if subscription.plan else None
    old_seat_count = subscription.seat_count
    old_cancel = subscription.cancel_at_period_end

    subscription.is_active = subscription_data['status'] == 'active'
    subscription.cancel_at_period_end = subscription_data.get('cancel_at_period_end', False)

    items = subscription_data.get('items', {}).get('data', [])
    if items:
        # Match line items by price_id — items[0] is NOT the base item on multi-item
        # subscriptions (base + extra seats + metered overage can arrive in any order).
        def _item_for_price(price_id):
            return next(
                (i for i in items if i.get('price', {}).get('id') == price_id),
                None,
            )

        # Base item: current plan's price first; if absent, the plan changed on
        # Stripe's side — find whichever known Plan price is present.
        base_item = _item_for_price(subscription.plan.stripe_price_id)
        if base_item is None:
            for candidate_plan in Plan.objects.exclude(stripe_price_id__isnull=True).exclude(stripe_price_id=''):
                candidate_item = _item_for_price(candidate_plan.stripe_price_id)
                if candidate_item is not None:
                    subscription.plan = candidate_plan
                    base_item = candidate_item
                    break

        if base_item is not None:
            subscription.start_date = datetime.fromtimestamp(base_item.get('current_period_start', 0))
            subscription.end_date = datetime.fromtimestamp(base_item.get('current_period_end', 0))

        # Sync purchased seats from the extra-seat item quantity (mirrors
        # handle_subscription_created) so Stripe-side seat edits don't drift locally.
        extra_seat_price_id = subscription.plan.stripe_extra_seat_price_id
        if extra_seat_price_id:
            seat_item = _item_for_price(extra_seat_price_id)
            extra_qty = seat_item.get('quantity', 0) if seat_item else 0
            subscription.seat_count = (subscription.plan.included_seats or 1) + extra_qty

    subscription.save()

    # Emit org activity events for meaningful changes only
    org = subscription.organization
    new_plan_name = subscription.plan.name if subscription.plan else None
    if old_plan_name and new_plan_name and old_plan_name != new_plan_name:
        log_org_activity(
            org, 'plan_changed',
            metadata={'old_plan': old_plan_name, 'new_plan': new_plan_name},
        )
    if old_seat_count != subscription.seat_count:
        log_org_activity(
            org, 'seats_changed',
            metadata={'old_seats': old_seat_count, 'new_seats': subscription.seat_count},
        )
    if not old_cancel and subscription.cancel_at_period_end:
        log_org_activity(org, 'plan_cancel_scheduled', metadata={})
    elif old_cancel and not subscription.cancel_at_period_end:
        log_org_activity(org, 'plan_reactivated', metadata={})

    logger.info("handle_subscription_updated exit event_id=%s org_id=%s", event_id, org_id)

def handle_payment_failed(invoice_data, event_id=None):
    """Handle failed payment (e.g., expired credit card)."""
    customer_id = invoice_data.get('customer')
    logger.info("handle_payment_failed enter event_id=%s customer_id=%s", event_id, customer_id)
    # Stripe retries automatically; subscription marked inactive via customer.subscription.deleted on final failure.
    logger.info("handle_payment_failed exit event_id=%s customer_id=%s", event_id, customer_id)

def handle_subscription_deleted(subscription_data, event_id=None):
    """Handle subscription cancellation / period-end deletion.

    When a paid subscription is deleted (either immediately or after cancel_at_period_end),
    mark it inactive and reactivate the org's Free sentinel so the org retains basic access.
    """
    subscription_id = subscription_data['id']
    logger.info("handle_subscription_deleted enter event_id=%s subscription_id=%s", event_id, subscription_id)

    try:
        subscription = Subscription.objects.get(stripe_subscription_id=subscription_id)
    except Subscription.DoesNotExist:
        logger.warning("handle_subscription_deleted: subscription %s not found, ignoring", subscription_id)
        return

    org = subscription.organization
    org_id = subscription.organization_id

    # Option (b): idempotently re-settle any overage accrued between the cancel
    # request and this period-end deletion. Best-effort billing (the Stripe sub
    # may already be gone) but fully settles our local ledger; delta + Stripe
    # identifier dedup means it never double-charges what cancel-time reported.
    settle_ym = timezone.now().strftime('%Y-%m')
    transaction.on_commit(
        lambda org_id=org_id, ym=settle_ym: settle_final_overage.delay(org_id, ym)
    )

    subscription.is_active = False
    plan_name = subscription.plan.name if subscription.plan else None
    subscription.save()

    log_org_activity(
        org, 'plan_cancelled',
        metadata={'plan_name': plan_name or ''},
    )

    # Reactivate the Free sentinel so the org falls back to the Free tier.
    # The sentinel was deactivated by handle_subscription_created when the paid sub started.
    reactivated = Subscription.objects.filter(
        organization=org,
        is_internal=True,
    ).update(is_active=True)

    if reactivated:
        logger.info(
            "handle_subscription_deleted reactivated %d Free sentinel(s) for org %s",
            reactivated, org_id,
        )
    else:
        # Sentinel was somehow missing — recreate it from scratch.
        free_plan = Plan.objects.filter(name='Free', is_archived=False).first()
        if free_plan:
            Subscription.objects.create(
                organization=org,
                plan=free_plan,
                stripe_subscription_id=f'sub_free_internal_{org_id}',
                start_date=timezone.now(),
                end_date=timezone.now() + timedelta(days=365 * 100),
                is_active=True,
                is_internal=True,
            )
            logger.info(
                "handle_subscription_deleted recreated Free sentinel for org %s", org_id,
            )
        else:
            logger.error(
                "handle_subscription_deleted: Free plan not found, cannot reactivate sentinel for org %s",
                org_id,
            )

    logger.info("handle_subscription_deleted exit event_id=%s org_id=%s", event_id, org_id)


def handle_invoice_created(data, event_id=None):
    """
    Log invoice creation for awareness.
    No DB write — invoice.finalized is the reconciliation checkpoint.
    Exceptions bubble up to the webhook dispatcher (matches commit-1 design).
    """
    invoice_id = data.get('id')
    customer_id = data.get('customer')
    logger.info(
        "handle_invoice_created event_id=%s invoice_id=%s customer_id=%s",
        event_id, invoice_id, customer_id,
    )


def handle_invoice_finalized(data, event_id=None):
    """
    Reconcile Stripe's computed overage amount against the local UsageMonthly record.
    Mismatch → warning log for ops review; no automatic data correction.
    Exceptions bubble up to the webhook dispatcher.
    """
    from .models import UsageMonthly

    invoice_id = data.get('id')
    customer_id = data.get('customer')
    logger.info(
        "handle_invoice_finalized enter event_id=%s invoice_id=%s customer_id=%s",
        event_id, invoice_id, customer_id,
    )

    # Locate the org via the Stripe customer record.
    if not customer_id:
        logger.warning("handle_invoice_finalized: no customer_id on invoice %s", invoice_id)
        return

    customer = stripe.Customer.retrieve(customer_id)
    org_id = customer.metadata.get('organization_id') if customer else None
    if not org_id:
        logger.warning("handle_invoice_finalized: no organization_id in customer %s metadata", customer_id)
        return

    # Sum the overage line amounts from the invoice (in cents).
    lines = data.get('lines', {}).get('data', [])
    stripe_overage_cents = sum(
        line.get('amount', 0)
        for line in lines
        if line.get('pricing', {}).get('price_details', {}).get('price')
        in {
            sub.plan.stripe_overage_price_id
            for sub in Subscription.objects.filter(
                organization_id=org_id, is_active=True, is_internal=False,
            ).select_related('plan')
            if sub.plan and sub.plan.stripe_overage_price_id
        }
    )

    ym = timezone.now().strftime('%Y-%m')
    usage = UsageMonthly.objects.filter(organization_id=org_id, year_month=ym).first()
    if not usage:
        logger.info(
            "handle_invoice_finalized: no UsageMonthly for org %s ym=%s — skipping reconciliation",
            org_id, ym,
        )
        return

    # Derive expected overage cents from local token count.
    sub = (
        Subscription.objects.filter(organization_id=org_id, is_active=True, is_internal=False)
        .select_related('plan')
        .first()
    )
    plan = sub.plan if sub else None
    rate = plan.overage_price_cents_per_1m if plan else None
    if rate is not None:
        # Expected units must mirror report_overage_to_stripe's ceil + credited model:
        # overage_reported_tokens is always units x 1M (what we actually sent to the
        # meter); before any report, expect ceil of the raw overage.
        reported_units = usage.overage_reported_tokens // 1_000_000
        expected_units = reported_units if reported_units > 0 else -(-usage.overage_tokens // 1_000_000)
        local_overage_cents = expected_units * rate
        if stripe_overage_cents != local_overage_cents:
            logger.warning(
                "handle_invoice_finalized MISMATCH org=%s ym=%s "
                "stripe_overage_cents=%d local_overage_cents=%d — manual review required",
                org_id, ym, stripe_overage_cents, local_overage_cents,
            )

    logger.info(
        "handle_invoice_finalized exit event_id=%s org_id=%s stripe_overage_cents=%d",
        event_id, org_id, stripe_overage_cents,
    )
