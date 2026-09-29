"""
Essential WebSocket tests for retrospective real-time updates
Tests WebSocket updates (group and timing) as required by BE4-04
"""
import asyncio
import pytest
from datetime import datetime, timedelta
from decimal import Decimal
from django.test import override_settings

from core.test_utils import TenantSafeTransactionTestCase
from django.contrib.auth import get_user_model
from channels.testing import WebsocketCommunicator
from channels.db import database_sync_to_async

from core.models import Project, Organization
from retrospective.models import RetrospectiveTask, Insight, CampaignMetric, RetrospectiveStatus
from retrospective.services import RetrospectiveService
from retrospective.consumers import RetrospectiveConsumer

User = get_user_model()


@override_settings(
    CHANNEL_LAYERS={
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }
)
@pytest.mark.timeout(600)
class WebSocketRetrospectiveUpdatesTest(TenantSafeTransactionTestCase):
    """Test WebSocket updates (group and timing) for retrospective workflow"""

    def setUp(self):
        """Set up test data"""
        import uuid
        # Use a single suffix per setUp so all related objects share a unique namespace,
        # preventing IntegrityError on unique fields if a prior flush was incomplete.
        self._suffix = uuid.uuid4().hex[:8]
        unique_name = f"WS Test Agency {self._suffix}"
        self.organization = Organization.objects.create(
            name=unique_name,
            email_domain=f"wstest_{self._suffix}.com"
        )

        # Create users for group testing
        self.media_buyer = User.objects.create_user(
            username=f"wsbuyer_{self._suffix}",
            email=f"wsbuyer_{self._suffix}@wstest.com",
            password="testpass123",
            organization=self.organization
        )

        self.team_lead = User.objects.create_user(
            username=f"wslead_{self._suffix}",
            email=f"wslead_{self._suffix}@wstest.com",
            password="testpass123",
            organization=self.organization
        )

        # Create campaign
        self.campaign = Project.objects.create(
            name=f"WebSocket Test Campaign {self._suffix}",
            organization=self.organization
        )

    @database_sync_to_async
    def create_retrospective(self, **kwargs):
        """Create a retrospective asynchronously"""
        return RetrospectiveTask.objects.create(**kwargs)
    
    @database_sync_to_async
    def update_retrospective_status(self, retrospective, status):
        """Update retrospective status asynchronously"""
        retrospective.status = status
        if status == RetrospectiveStatus.IN_PROGRESS:
            retrospective.started_at = datetime.now()
        elif status == RetrospectiveStatus.COMPLETED:
            retrospective.completed_at = datetime.now()
        retrospective.save()
        return retrospective
    
    @database_sync_to_async
    def create_insight(self, **kwargs):
        """Create an insight asynchronously"""
        return Insight.objects.create(**kwargs)
    
    @database_sync_to_async
    def create_multiple_insights(self, retrospective, count):
        """Create multiple insights asynchronously"""
        insights = []
        for i in range(count):
            insight = Insight.objects.create(
                retrospective=retrospective,
                title=f"Test Insight {i}",
                description=f"Description for insight {i}" * 10,  # Long description
                severity='medium',
                rule_id=f'rule_{i}',
                suggested_actions=[f"Action {j}" for j in range(5)]
            )
            insights.append(insight)
        return insights
        
        # Create KPI data
        self.create_kpi_data()

    def create_kpi_data(self):
        """Create test KPI data"""
        base_date = datetime.now().date()
        
        for i in range(10):
            date = base_date - timedelta(days=i)
            CampaignMetric.objects.get_or_create(
                campaign=self.campaign,
                date=date,
                defaults={
                    'impressions': 1000 + (i * 10),
                    'clicks': 50 + (i * 2),
                    'conversions': 5 + (i * 0.1),
                    'cost_per_click': Decimal('2.50') + Decimal(str(i * 0.01)),
                    'cost_per_impression': Decimal('0.10') + Decimal(str(i * 0.001)),
                    'cost_per_conversion': Decimal('25.00') + Decimal(str(i * 0.1)),
                    'click_through_rate': Decimal('0.05') + Decimal(str(i * 0.001)),
                    'conversion_rate': Decimal('0.10') + Decimal(str(i * 0.001))
                }
            )

    async def test_websocket_group_notifications_multiple_users(self):
        """Test WebSocket group notifications for multiple users (BE4-04 requirement)"""
        # Create retrospective
        retrospective = await self.create_retrospective(
            campaign=self.campaign,
            created_by=self.media_buyer,
            status=RetrospectiveStatus.IN_PROGRESS
        )

        # Connect multiple WebSocket clients (simulating group)
        buyer_communicator = WebsocketCommunicator(
            RetrospectiveConsumer.as_asgi(),
            f"/ws/retrospective/{retrospective.id}/"
        )

        lead_communicator = WebsocketCommunicator(
            RetrospectiveConsumer.as_asgi(),
            f"/ws/retrospective/{retrospective.id}/"
        )

        try:
            await buyer_communicator.connect()
            await lead_communicator.connect()

            # Receive connection confirmation messages first
            buyer_connection_msg = await buyer_communicator.receive_json_from()
            lead_connection_msg = await lead_communicator.receive_json_from()

            # Verify connection messages
            assert buyer_connection_msg['type'] == 'connection_established'
            assert lead_connection_msg['type'] == 'connection_established'

            # Send group notification about retrospective completion
            await buyer_communicator.send_json_to({
                'type': 'retrospective_completed',
                'retrospective_id': str(retrospective.id),
                'message': 'Retrospective analysis completed - ready for review',
                'target_group': ['media_buyer', 'team_lead'],
                'timestamp': datetime.now().isoformat()
            })

            # Both group members should receive the notification
            buyer_response = await buyer_communicator.receive_json_from()
            lead_response = await lead_communicator.receive_json_from()

            assert buyer_response['type'] == 'retrospective_completed'
            assert lead_response['type'] == 'retrospective_completed'
            assert buyer_response['retrospective_id'] == str(retrospective.id)
            assert lead_response['retrospective_id'] == str(retrospective.id)
        finally:
            await buyer_communicator.disconnect()
            await lead_communicator.disconnect()
    
    async def test_websocket_insight_updates_realtime(self):
        """Test real-time insight updates via WebSocket"""
        # Create retrospective
        retrospective = await self.create_retrospective(
            campaign=self.campaign,
            created_by=self.media_buyer,
            status=RetrospectiveStatus.IN_PROGRESS
        )

        # Connect WebSocket
        communicator = WebsocketCommunicator(
            RetrospectiveConsumer.as_asgi(),
            f"/ws/retrospective/{retrospective.id}/"
        )

        try:
            await communicator.connect()

            # Receive connection confirmation message first
            connection_msg = await communicator.receive_json_from()
            assert connection_msg['type'] == 'connection_established'

            # Create insight and send update
            insight = await self.create_insight(
                retrospective=retrospective,
                title="Critical ROI Issue",
                description="ROI dropped below 0.5 threshold",
                severity='critical',
                created_by=self.media_buyer
            )

            await communicator.send_json_to({
                'type': 'insight_generated',
                'retrospective_id': str(retrospective.id),
                'insight_id': str(insight.id),
                'insight_title': insight.title,
                'severity': insight.severity,
                'timestamp': datetime.now().isoformat()
            })

            # Receive insight update
            response = await communicator.receive_json_from()
            assert response['type'] == 'insight_generated'
            assert response['insight_id'] == str(insight.id)
            assert response['severity'] == 'critical'
        finally:
            await communicator.disconnect()

    async def test_websocket_timing_performance(self):
        """Test WebSocket message timing (BE4-04 requirement)"""
        # Create retrospective
        retrospective = await self.create_retrospective(
            campaign=self.campaign,
            created_by=self.media_buyer
        )

        # Connect WebSocket
        communicator = WebsocketCommunicator(
            RetrospectiveConsumer.as_asgi(),
            f"/ws/retrospective/{retrospective.id}/"
        )

        try:
            await communicator.connect()

            # Receive connection confirmation message first
            connection_msg = await communicator.receive_json_from()
            assert connection_msg['type'] == 'connection_established'

            # Test rapid message delivery timing
            import time
            start_time = time.time()

            # Send multiple rapid updates
            messages_sent = 5
            for i in range(messages_sent):
                await communicator.send_json_to({
                    'type': 'kpi_update',
                    'retrospective_id': str(retrospective.id),
                    'kpi_name': f'ROI_Update_{i}',
                    'value': 0.7 + (i * 0.1),
                    'timestamp': datetime.now().isoformat()
                })

            # Receive all updates
            responses = []
            for i in range(messages_sent):
                response = await communicator.receive_json_from()
                responses.append(response)

            end_time = time.time()

            # Verify timing performance — use a generous limit suitable for CI
            duration = end_time - start_time
            assert duration < 30.0  # Allow up to 30 s in constrained CI environments

            # Verify all messages received
            assert len(responses) == messages_sent
            for i, response in enumerate(responses):
                assert response['type'] == 'kpi_update'
                assert f'ROI_Update_{i}' in response['kpi_name']
        finally:
            await communicator.disconnect()

    async def test_websocket_group_broadcast_efficiency(self):
        """Test efficient group broadcasting for team notifications"""
        # Create retrospective
        retrospective = await self.create_retrospective(
            campaign=self.campaign,
            created_by=self.media_buyer,
            status=RetrospectiveStatus.COMPLETED
        )

        # Connect multiple users to simulate team group
        communicators = []
        user_count = 3

        try:
            # Create multiple WebSocket connections
            for i in range(user_count):
                communicator = WebsocketCommunicator(
                    RetrospectiveConsumer.as_asgi(),
                    f"/ws/retrospective/{retrospective.id}/"
                )
                await communicator.connect()
                communicators.append(communicator)

            # Receive connection confirmation messages first
            connection_responses = []
            for communicator in communicators:
                connection_msg = await communicator.receive_json_from()
                connection_responses.append(connection_msg)
                assert connection_msg['type'] == 'connection_established'

            # Send group broadcast about retrospective completion
            import time
            start_time = time.time()

            await communicators[0].send_json_to({
                'type': 'group_broadcast',
                'retrospective_id': str(retrospective.id),
                'message': 'Retrospective analysis complete - insights available',
                'broadcast_to': 'all_team_members',
                'timestamp': datetime.now().isoformat()
            })

            # All connected users should receive the broadcast
            responses = []
            for communicator in communicators:
                response = await communicator.receive_json_from()
                responses.append(response)

            end_time = time.time()

            # Verify broadcast efficiency — use a generous limit suitable for CI
            broadcast_time = end_time - start_time
            assert broadcast_time < 30.0  # Allow up to 30 s in constrained CI environments

            # Verify all users received the same message
            assert len(responses) == user_count
            for response in responses:
                assert response['type'] == 'group_broadcast'
                assert response['retrospective_id'] == str(retrospective.id)
        finally:
            for communicator in communicators:
                await communicator.disconnect()


