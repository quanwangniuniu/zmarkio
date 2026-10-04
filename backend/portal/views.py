import logging

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet
from rest_framework import mixins
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.http import Http404
from core.services.auth_tokens import build_user_refresh_token

from customer.models import Customer
from csm.models import Conversation, ConversationMessage, Queue, SupportChannel
from csm.serializers import ConversationSerializer, ConversationMessageSerializer
from csm.services.routing_rules import route_new_conversation
from csm.services.support_channels import (
    build_offline_payload,
    evaluate_channel_availability,
    resolve_channel_for_conversation,
)
from .serializers import (
    PortalRegisterSerializer,
    PortalConversationSerializer,
    PortalConversationDetailSerializer,
    PortalMessageSerializer,
    PortalConversationCreateSerializer,
)

logger = logging.getLogger(__name__)


SUPPORTED_MESSAGE_IMAGE_TYPES = {
    'image/png', 'image/jpeg', 'image/gif', 'image/webp',
    'image/heic', 'image/heif', 'image/heic-sequence', 'image/heif-sequence',
}
SUPPORTED_MESSAGE_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'heic', 'heif'}
MAX_MESSAGE_IMAGE_BYTES = 10 * 1024 * 1024


def _is_supported_message_image(image):
    content_type = (getattr(image, 'content_type', '') or '').lower()
    extension = image.name.rsplit('.', 1)[-1].lower() if '.' in image.name else ''
    return content_type in SUPPORTED_MESSAGE_IMAGE_TYPES or extension in SUPPORTED_MESSAGE_IMAGE_EXTENSIONS


class PortalRegisterView(APIView):
    """POST /api/portal/register/ — public customer registration."""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PortalRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, customer = serializer.save()

        refresh = build_user_refresh_token(user)
        return Response(
            {
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': {
                    'id': user.id,
                    'email': user.email,
                    'full_name': customer.full_name,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class PortalConversationViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    GenericViewSet,
):
    """
    Customer-facing conversation endpoints.

    GET  /api/portal/conversations/              — my conversations
    POST /api/portal/conversations/              — start a new conversation
    GET  /api/portal/conversations/{id}/         — detail with messages
    POST /api/portal/conversations/{id}/messages/ — send a reply
    """
    permission_classes = [IsAuthenticated]

    def _get_customer(self):
        try:
            return self.request.user.customer_profile
        except Customer.DoesNotExist:
            return None

    def get_queryset(self):
        customer = self._get_customer()
        if not customer:
            return Conversation.objects.none()
        return Conversation.objects.filter(customer=customer).order_by('-started_at')

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return PortalConversationDetailSerializer
        return PortalConversationSerializer

    def create(self, request, *args, **kwargs):
        customer = self._get_customer()
        if not customer:
            return Response(
                {'detail': 'No customer profile found for this account.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = PortalConversationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        message_text = data['message']
        subject = data.get('subject', '').strip()

        support_channel = None
        availability = None
        channel_id = data.get('support_channel_id')
        embed_key = data.get('embed_key')
        if channel_id is not None or embed_key is not None:
            try:
                support_channel = resolve_channel_for_conversation(
                    support_channel_id=channel_id,
                    embed_key=str(embed_key) if embed_key is not None else None,
                )
            except Http404:
                return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
            except DjangoValidationError as exc:
                detail = exc.messages[0] if getattr(exc, 'messages', None) else str(exc)
                return Response({'detail': detail}, status=status.HTTP_400_BAD_REQUEST)

            if support_channel.channel_type != SupportChannel.ChannelType.LIVE_CHAT:
                return Response(
                    {'detail': 'Only live chat channels can start conversations.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            availability = evaluate_channel_availability(support_channel)
            if not availability['is_online']:
                return Response(
                    {
                        'detail': 'Channel is currently offline.',
                        'offline': build_offline_payload(support_channel, request),
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            if not support_channel.default_queue_id:
                return Response(
                    {'detail': 'Channel has no default queue configured.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        queue = None
        if support_channel is not None:
            queue = support_channel.default_queue
        elif customer.organisation:
            queue = Queue.objects.filter(organisation=customer.organisation).first()

        # The customer's experience-group routing rules may pick another queue.
        # Routing must never block a customer from starting a chat.
        tags = [subject] if subject else []
        try:
            # Savepoint: a DB error in routing must not abort the insert below.
            with transaction.atomic():
                queue, rule_tags = route_new_conversation(
                    customer, support_channel=support_channel,
                    availability=availability,
                    message=message_text, subject=subject, fallback_queue=queue,
                )
            seen = {tag.casefold() for tag in tags}
            tags += [tag for tag in rule_tags if tag.casefold() not in seen]
        except Exception:
            logger.exception('Routing rules failed for a new conversation; using the default queue.')

        conversation = Conversation.objects.create(
            customer=customer,
            queue=queue,
            status='pending',
            channel='web',
            support_channel=support_channel,
            tags=tags,
        )

        ConversationMessage.objects.create(
            conversation=conversation,
            sender_type='customer',
            content=message_text,
        )

        # Notify all online agents about the new conversation via WebSocket
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            'csm_new_conversations',
            {
                'type': 'new.conversation',
                'conversation': ConversationSerializer(conversation).data,
            },
        )

        return Response(
            PortalConversationDetailSerializer(conversation).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def messages(self, request, pk=None):
        """POST /api/portal/conversations/{id}/messages/ — customer sends a reply."""
        customer = self._get_customer()
        if not customer:
            return Response({'detail': 'No customer profile found.'}, status=status.HTTP_403_FORBIDDEN)
        conversation = self.get_object()
        if conversation.customer_id != customer.id:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        content = request.data.get('content', '').strip()
        image = request.FILES.get('image')
        if not content and not image:
            return Response({'detail': 'content or image is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if image:
            if not _is_supported_message_image(image):
                return Response(
                    {'detail': 'Only PNG, JPG, GIF, WebP, or HEIC images can be attached.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if image.size > MAX_MESSAGE_IMAGE_BYTES:
                return Response({'detail': 'Image must be under 10MB.'}, status=status.HTTP_400_BAD_REQUEST)

        msg = ConversationMessage.objects.create(
            conversation=conversation,
            sender_type='customer',
            content=content,
            image=image,
        )

        # A customer reply pulls any ticket that was waiting on them back into
        # the agent's queue, which resumes its SLA clock (see Ticket.save).
        for ticket in conversation.tickets.filter(status='pending_customer'):
            ticket.status = 'in_progress'
            ticket.save(update_fields=['status'])

        portal_payload = PortalMessageSerializer(msg, context={'request': request}).data

        agent_payload = ConversationMessageSerializer(msg, context={'request': request}).data

        channel_layer = get_channel_layer()
        # Broadcast new message to agents watching this conversation thread
        async_to_sync(channel_layer.group_send)(
            f'csm_conversation_{conversation.id}',
            {'type': 'conversation.message', 'message': agent_payload},
        )
        # Broadcast conversation_updated so all agents' lists refresh (new message indicator)
        async_to_sync(channel_layer.group_send)(
            'csm_new_conversations',
            {'type': 'conversation.updated', 'conversation': ConversationSerializer(conversation).data},
        )

        return Response(portal_payload, status=status.HTTP_201_CREATED)
