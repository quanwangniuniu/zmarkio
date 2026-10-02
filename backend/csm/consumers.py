import json
import logging
from channels.db import database_sync_to_async

from core.consumers import InstrumentedAsyncWebsocketConsumer
from csm.services.guidance import guidance_group_name

logger = logging.getLogger(__name__)


class CsmConversationConsumer(InstrumentedAsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time CSM conversation updates.

    Connection URL: ws://localhost:8000/ws/csm/agent/{user_id}/

    Message Types (client -> server):
    - typing_start: Agent started typing in a conversation
    - typing_stop: Agent stopped typing
    - join_conversation: Subscribe to a specific conversation's events
    - leave_conversation: Unsubscribe from a conversation

    Message Types (server -> client):
    - new_message: New message in a conversation
    - conversation_updated: Conversation metadata changed (status, queue, etc.)
    - typing_indicator: Someone is typing
    - guidance_updated: Guidance for an Experience Group changed; refetch it
    - error: Error message
    """

    ws_channel = 'csm'

    async def connect(self):
        self.user_id = self.scope['url_route']['kwargs']['user_id']
        self.user = self.scope.get('user')
        self.joined_conversations = set()
        # conversation id -> matched Experience Group id (guidance subscriptions)
        self.guidance_groups = {}
        self.accessible_queue_ids = set()
        self.is_privileged = False

        if not self.user or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        if str(self.user.id) != str(self.user_id):
            await self.close(code=4003)
            return

        # Cache the agent's accessible queue IDs so real-time events can be
        # filtered without a DB hit on every broadcast.
        self.is_privileged, self.accessible_queue_ids = await self._load_accessible_queue_ids()

        # Join personal agent group (for targeted notifications)
        self.agent_group = f'csm_agent_{self.user_id}'
        await self.channel_layer.group_add(self.agent_group, self.channel_name)

        # Join global new-conversation broadcast group
        await self.channel_layer.group_add('csm_new_conversations', self.channel_name)

        await self.accept()
        logger.info(f'[CSM WS] Agent {self.user_id} connected')

    async def disconnect(self, close_code):
        # Leave personal group (may not exist if connect() was rejected early)
        if hasattr(self, 'agent_group'):
            await self.channel_layer.group_discard(self.agent_group, self.channel_name)
            await self.channel_layer.group_discard('csm_new_conversations', self.channel_name)

        # Leave any joined conversation groups
        for conv_id in list(self.joined_conversations):
            await self.channel_layer.group_discard(
                f'csm_conversation_{conv_id}', self.channel_name
            )
        for eg_id in set(getattr(self, 'guidance_groups', {}).values()):
            await self.channel_layer.group_discard(
                guidance_group_name(eg_id), self.channel_name
            )

        logger.info(f'[CSM WS] Agent {self.user_id} disconnected (code={close_code})')

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send_error('Invalid JSON')
            return

        msg_type = data.get('type')

        if msg_type == 'join_conversation':
            await self.handle_join_conversation(data)
        elif msg_type == 'leave_conversation':
            await self.handle_leave_conversation(data)
        elif msg_type == 'typing_start':
            await self.handle_typing(data, is_typing=True)
        elif msg_type == 'typing_stop':
            await self.handle_typing(data, is_typing=False)
        else:
            await self.send_error(f'Unknown message type: {msg_type}')

    async def handle_join_conversation(self, data):
        conv_id = data.get('conversation_id')
        if not conv_id:
            return
        if not await self.can_access_conversation(conv_id):
            await self.send_error('Access denied to this conversation')
            return
        group = f'csm_conversation_{conv_id}'
        await self.channel_layer.group_add(group, self.channel_name)
        self.joined_conversations.add(conv_id)
        await self._join_guidance_group(conv_id)
        await self.send(text_data=json.dumps({'type': 'joined', 'conversation_id': conv_id}))

    async def handle_leave_conversation(self, data):
        conv_id = data.get('conversation_id')
        if not conv_id:
            return
        group = f'csm_conversation_{conv_id}'
        await self.channel_layer.group_discard(group, self.channel_name)
        self.joined_conversations.discard(conv_id)
        await self._leave_guidance_group(conv_id)

    async def _join_guidance_group(self, conv_id):
        eg_id = await self._conversation_experience_group_id(conv_id)
        previous = self.guidance_groups.pop(conv_id, None)
        if previous is not None and previous != eg_id:
            await self._discard_guidance_group_if_unused(previous)
        if eg_id is None:
            return
        if eg_id not in self.guidance_groups.values():
            await self.channel_layer.group_add(guidance_group_name(eg_id), self.channel_name)
        self.guidance_groups[conv_id] = eg_id

    async def _leave_guidance_group(self, conv_id):
        eg_id = self.guidance_groups.pop(conv_id, None)
        if eg_id is not None:
            await self._discard_guidance_group_if_unused(eg_id)

    async def _discard_guidance_group_if_unused(self, eg_id):
        # Several open conversations can share one Experience Group.
        if eg_id not in self.guidance_groups.values():
            await self.channel_layer.group_discard(guidance_group_name(eg_id), self.channel_name)

    async def handle_typing(self, data, is_typing: bool):
        conv_id = data.get('conversation_id')
        if not conv_id or conv_id not in self.joined_conversations:
            return
        await self.channel_layer.group_send(
            f'csm_conversation_{conv_id}',
            {
                'type': 'typing.indicator',
                'conversation_id': conv_id,
                'user_id': self.user_id,
                'is_typing': is_typing,
            },
        )

    # ── Channel layer event handlers (server -> client) ──────────────────────

    async def conversation_message(self, event):
        """Broadcast a new message to all subscribers of the conversation."""
        await self.send(text_data=json.dumps({
            'type': 'new_message',
            'message': event['message'],
        }))

    async def conversation_updated(self, event):
        """Broadcast conversation metadata updates (status, queue, assigned_to, tags)."""
        conv = event.get('conversation', {})
        queue_id = conv.get('queue') or conv.get('queue_id')
        if not self._can_see_queue(queue_id):
            return
        await self.send(text_data=json.dumps({
            'type': 'conversation_updated',
            'conversation': conv,
        }))

    async def new_conversation(self, event):
        """Broadcast to agents when a customer creates a new conversation.

        Only forward the event if the conversation's queue is accessible to
        this agent, mirroring the REST-level queue filtering.
        """
        conv = event.get('conversation', {})
        queue_id = conv.get('queue') or conv.get('queue_id')
        if not self._can_see_queue(queue_id):
            return
        await self.send(text_data=json.dumps({
            'type': 'new_conversation',
            'conversation': conv,
        }))

    async def typing_indicator(self, event):
        # Don't send typing indicator back to the person who triggered it
        if str(event['user_id']) == str(self.user_id):
            return
        await self.send(text_data=json.dumps({
            'type': 'typing_indicator',
            'conversation_id': event['conversation_id'],
            'user_id': event['user_id'],
            'is_typing': event['is_typing'],
        }))

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def guidance_updated(self, event):
        """Guidance for one or more Experience Groups changed; clients refetch."""
        await self.send(text_data=json.dumps({
            'type': 'guidance_updated',
            'experience_group_ids': event.get('experience_group_ids', []),
        }))

    async def send_error(self, detail: str):
        await self.send(text_data=json.dumps({'type': 'error', 'detail': detail}))

    def _can_see_queue(self, queue_id) -> bool:
        """Return True if this agent is allowed to see a given queue.

        Privileged users (staff/superuser) see everything. Regular agents
        only see queues whose IDs were cached at connect time.
        """
        if self.is_privileged:
            return True
        if queue_id is None:
            return False
        return int(queue_id) in self.accessible_queue_ids

    @database_sync_to_async
    def _load_accessible_queue_ids(self):
        """Return (is_privileged, set_of_queue_ids) for the connected user.

        Mirrors the REST view's ``_accessible_queues`` logic.
        """
        from django.db.models import Q
        from .models import Queue, CustomerUser, QueueAgent

        user = self.user
        if user.is_staff or user.is_superuser:
            return True, set()

        admin_org_ids = CustomerUser.objects.filter(
            user=user, is_active=True,
            user_type__in=('supervisor', 'admin'),
            organisation__isnull=False,
        ).values_list('organisation_id', flat=True)

        agent_queue_ids = QueueAgent.objects.filter(
            user=user,
        ).values_list('queue_id', flat=True)

        profile_queue_ids = CustomerUser.objects.filter(
            user=user, is_active=True,
            queue__isnull=False,
        ).values_list('queue_id', flat=True)

        ids = set(Queue.objects.filter(
            Q(is_active=True),
            Q(organisation_id__in=admin_org_ids)
            | Q(id__in=agent_queue_ids)
            | Q(id__in=profile_queue_ids),
        ).distinct().values_list('id', flat=True))

        return False, ids

    @database_sync_to_async
    def _conversation_experience_group_id(self, conversation_id):
        """The conversation's matched Experience Group: its customer's EG."""
        from .models import Conversation
        return (
            Conversation.objects.filter(id=conversation_id)
            .values_list('customer__experience_group_id', flat=True)
            .first()
        )

    @database_sync_to_async
    def can_access_conversation(self, conversation_id: int) -> bool:
        from .models import Conversation, CustomerUser, QueueAgent
        try:
            conv = Conversation.objects.select_related('queue', 'customer').get(id=conversation_id)
        except Conversation.DoesNotExist:
            return False

        cu = CustomerUser.objects.filter(user=self.user, is_active=True).first()
        if not cu:
            return False

        if cu.user_type in ('supervisor', 'admin'):
            # Supervisors/admins can only access conversations within their own org
            if conv.queue is not None:
                return conv.queue.organisation_id == cu.organisation_id
            # Unassigned: match on customer organisation
            if conv.customer and conv.customer.organisation_id:
                return conv.customer.organisation_id == cu.organisation_id
            return True

        # Regular agents: must be assigned to the conversation's queue
        if conv.queue is None:
            # Unassigned: allow only if customer belongs to agent's org
            if conv.customer and conv.customer.organisation_id:
                return conv.customer.organisation_id == cu.organisation_id
            return False

        return QueueAgent.objects.filter(user=self.user, queue=conv.queue).exists()
