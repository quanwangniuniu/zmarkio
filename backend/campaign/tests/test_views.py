"""
Tests for Campaign ViewSets and API endpoints.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from decimal import Decimal
from unittest.mock import patch

from core.models import Organization, Project, ProjectMember
from campaign.models import (
    Campaign,
    CampaignStatusHistory,
    PerformanceCheckIn,
    PerformanceSnapshot,
    CampaignAttachment,
    CampaignTemplate,
    CampaignTaskLink,
    CampaignDecisionLink,
    CampaignCalendarLink,
)
from calendars.models import Calendar, Event
from decision.models import Decision
from task.models import Task

User = get_user_model()


class CampaignViewSetBaseTestCase(TestCase):
    """Base test case with common setup for Campaign ViewSet tests"""
    
    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        
        # Create organization
        self.organization = Organization.objects.create(
            name="Test Organization",
            email_domain="test.com"
        )
        
        # Create projects
        self.project = Project.objects.create(
            name="Test Project",
            organization=self.organization
        )
        self.project2 = Project.objects.create(
            name="Test Project 2",
            organization=self.organization
        )
        
        # Create users
        self.user = User.objects.create_user(
            username='testuser',
            email='testuser@test.com',
            password='testpass123',
            organization=self.organization
        )
        self.user2 = User.objects.create_user(
            username='testuser2',
            email='testuser2@test.com',
            password='testpass123',
            organization=self.organization
        )
        self.user_no_access = User.objects.create_user(
            username='noaccess',
            email='noaccess@test.com',
            password='testpass123',
            organization=self.organization
        )
        
        # Create project memberships
        ProjectMember.objects.create(
            user=self.user,
            project=self.project,
            role='member',
            is_active=True
        )
        ProjectMember.objects.create(
            user=self.user,
            project=self.project2,
            role='member',
            is_active=True
        )
        ProjectMember.objects.create(
            user=self.user2,
            project=self.project,
            role='member',
            is_active=True
        )
        
        # Authenticate client
        self.client.force_authenticate(user=self.user)
    
    def _create_campaign(self, **kwargs):
        """Helper to create campaign"""
        defaults = {
            'name': kwargs.get('name', 'Test Campaign'),
            'objective': kwargs.get('objective', Campaign.Objective.CONVERSION),
            'platforms': kwargs.get('platforms', [Campaign.Platform.META]),
            'start_date': kwargs.get('start_date', timezone.now().date()),
            'project': kwargs.get('project', self.project),
            'owner': kwargs.get('owner', self.user),
            'creator': kwargs.get('creator', self.user),
        }
        defaults.update(kwargs)
        defaults.pop('status', None)  # Remove status if passed - FSM protection
        return Campaign.objects.create(**defaults)
    
    def _create_campaign_testing(self):
        """Helper to create campaign in TESTING status"""
        campaign = self._create_campaign()
        campaign.start_testing(user=self.user)
        campaign.save()
        return campaign
    
    def _get_results(self, response):
        """Extract results from paginated response"""
        data = response.data
        if isinstance(data, dict) and 'results' in data:
            return data['results']
        return data if isinstance(data, list) else [data]


# ============================================================================
# CampaignViewSet CRUD Tests
# ============================================================================

class CampaignViewSetCRUDTestCase(CampaignViewSetBaseTestCase):
    """Test CampaignViewSet CRUD operations"""
    
    def test_list_campaigns(self):
        """Test listing campaigns"""
        # Create test campaigns
        campaign1 = self._create_campaign(name="Campaign 1")
        campaign2 = self._create_campaign(name="Campaign 2", project=self.project2)
        
        url = '/api/campaigns/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        self.assertEqual(len(results), 2)
        campaign_names = [c['name'] for c in results]
        self.assertIn('Campaign 1', campaign_names)
        self.assertIn('Campaign 2', campaign_names)
    
    def test_list_campaigns_with_project_filter(self):
        """Test listing campaigns filtered by project"""
        campaign1 = self._create_campaign(name="Campaign 1")
        campaign2 = self._create_campaign(name="Campaign 2", project=self.project2)
        
        url = f'/api/campaigns/?project={self.project.slug}'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['name'], 'Campaign 1')
    
    def test_list_campaigns_with_status_filter(self):
        """Test listing campaigns filtered by status"""
        campaign1 = self._create_campaign(name="Campaign 1")
        campaign2 = self._create_campaign_testing()
        
        url = '/api/campaigns/?status=PLANNING'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['status'], 'PLANNING')
    
    @patch('campaign.views.CampaignTaskIntegrationService.on_campaign_created')
    def test_create_campaign(self, mock_on_campaign_created):
        """Test creating a campaign"""
        url = '/api/campaigns/'
        data = {
            'name': 'New Campaign',
            'objective': Campaign.Objective.CONVERSION,
            'platforms': [Campaign.Platform.META],
            'start_date': timezone.now().date().isoformat(),
            'project_id': self.project.id,
            'owner_id': self.user.id,
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['name'], 'New Campaign')
        self.assertEqual(response.data['status'], 'PLANNING')
        # User ID is Integer, serializer may return as int or string
        creator_id = response.data['creator']['id']
        self.assertEqual(int(creator_id) if isinstance(creator_id, str) else creator_id, self.user.id)
        
        # Verify campaign was created
        campaign = Campaign.objects.get(id=response.data['id'])
        self.assertEqual(campaign.name, 'New Campaign')
        self.assertEqual(campaign.creator, self.user)
    
    def test_create_campaign_with_invalid_data(self):
        """Test creating campaign with invalid data"""
        url = '/api/campaigns/'
        data = {
            'name': '',  # Empty name
            'objective': Campaign.Objective.CONVERSION,
            'platforms': [],
            'start_date': timezone.now().date().isoformat(),
            'project_id': self.project.id,
            'owner_id': self.user.id,
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_create_campaign_with_invalid_platforms(self):
        """Test creating campaign with invalid platforms"""
        url = '/api/campaigns/'
        data = {
            'name': 'New Campaign',
            'objective': Campaign.Objective.CONVERSION,
            'platforms': ['INVALID_PLATFORM'],
            'start_date': timezone.now().date().isoformat(),
            'project_id': self.project.id,
            'owner_id': self.user.id,
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('platforms', str(response.data))
    
    def test_retrieve_campaign(self):
        """Test retrieving a single campaign"""
        campaign = self._create_campaign(name="Test Campaign")
        
        url = f'/api/campaigns/{campaign.id}/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Test Campaign')
        self.assertEqual(response.data['id'], str(campaign.id))

    def test_retrieve_campaign_by_slug(self):
        """Campaign resolves by slug (UUID pk still works; slug is the user-facing key)."""
        campaign = self._create_campaign(name="Winter Sale Campaign")
        self.assertTrue(campaign.slug)

        url = f'/api/campaigns/{campaign.slug}/'
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], str(campaign.id))
        self.assertEqual(response.data['slug'], campaign.slug)

    def test_update_campaign(self):
        """Test updating a campaign"""
        campaign = self._create_campaign(name="Original Name")
        
        url = f'/api/campaigns/{campaign.id}/'
        data = {
            'name': 'Updated Name',
            'objective': Campaign.Objective.AWARENESS,
            'platforms': [Campaign.Platform.GOOGLE_ADS],
            'start_date': campaign.start_date.isoformat(),
            'project_id': campaign.project.id,
            'owner_id': campaign.owner.id,
        }
        response = self.client.put(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Updated Name')
        self.assertEqual(response.data['objective'], Campaign.Objective.AWARENESS)
        
        # Refresh non-status fields to avoid FSM protection
        campaign.refresh_from_db(fields=['name', 'objective'])
        self.assertEqual(campaign.name, 'Updated Name')
    
    def test_partial_update_campaign(self):
        """Test partial update of a campaign"""
        campaign = self._create_campaign(name="Original Name")
        
        url = f'/api/campaigns/{campaign.id}/'
        data = {'name': 'Partially Updated'}
        response = self.client.patch(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Partially Updated')
        
        # Refresh non-status fields to avoid FSM protection
        campaign.refresh_from_db(fields=['name'])
        self.assertEqual(campaign.name, 'Partially Updated')
    
    def test_delete_campaign(self):
        """Test soft deleting a campaign"""
        campaign = self._create_campaign()
        
        url = f'/api/campaigns/{campaign.id}/'
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        
        # Campaign should be soft deleted - refresh non-status fields to avoid FSM protection
        campaign.refresh_from_db(fields=['is_deleted'])
        self.assertTrue(campaign.is_deleted)
        
        # Should not appear in list
        list_response = self.client.get('/api/campaigns/')
        results = self._get_results(list_response)
        campaign_ids = [c['id'] for c in results]
        self.assertNotIn(str(campaign.id), campaign_ids)


# ============================================================================
# CampaignViewSet Permission Tests
# ============================================================================

class CampaignViewSetPermissionTestCase(CampaignViewSetBaseTestCase):
    """Test CampaignViewSet permissions and access control"""
    
    def test_list_only_accessible_campaigns(self):
        """Test that users only see campaigns from their projects"""
        # Create campaign in user's project
        campaign1 = self._create_campaign(name="Accessible Campaign")
        
        # Create campaign in different project (user has no access)
        other_org = Organization.objects.create(name="Other Org", email_domain="other.com")
        other_project = Project.objects.create(name="Other Project", organization=other_org)
        campaign2 = Campaign.objects.create(
            name="Inaccessible Campaign",
            objective=Campaign.Objective.CONVERSION,
            platforms=[Campaign.Platform.META],
            start_date=timezone.now().date(),
            project=other_project,
            owner=self.user2,
            creator=self.user2
        )
        
        url = '/api/campaigns/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        campaign_names = [c['name'] for c in results]
        self.assertIn('Accessible Campaign', campaign_names)
        self.assertNotIn('Inaccessible Campaign', campaign_names)
    
    def test_create_campaign_without_project_access(self):
        """Test creating campaign without project access"""
        other_org = Organization.objects.create(name="Other Org", email_domain="other.com")
        other_project = Project.objects.create(name="Other Project", organization=other_org)
        
        url = '/api/campaigns/'
        data = {
            'name': 'Unauthorized Campaign',
            'objective': Campaign.Objective.CONVERSION,
            'platforms': [Campaign.Platform.META],
            'start_date': timezone.now().date().isoformat(),
            'project_id': other_project.id,
            'owner_id': self.user.id,
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('project_id', str(response.data))
    
    def test_retrieve_campaign_without_access(self):
        """Test retrieving campaign without access"""
        other_org = Organization.objects.create(name="Other Org", email_domain="other.com")
        other_project = Project.objects.create(name="Other Project", organization=other_org)
        campaign = Campaign.objects.create(
            name="Inaccessible Campaign",
            objective=Campaign.Objective.CONVERSION,
            platforms=[Campaign.Platform.META],
            start_date=timezone.now().date(),
            project=other_project,
            owner=self.user2,
            creator=self.user2
        )
        
        url = f'/api/campaigns/{campaign.id}/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
    
    def test_update_campaign_without_access(self):
        """Test updating campaign without access"""
        # Create campaign owned by user2
        campaign = self._create_campaign(owner=self.user2)
        
        # Switch to user_no_access (no project membership)
        self.client.force_authenticate(user=self.user_no_access)
        
        url = f'/api/campaigns/{campaign.id}/'
        data = {'name': 'Unauthorized Update'}
        response = self.client.patch(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ============================================================================
# CampaignViewSet Status Transition Tests
# ============================================================================

class CampaignViewSetTransitionTestCase(CampaignViewSetBaseTestCase):
    """Test CampaignViewSet status transitions"""
    
    def test_start_testing_transition(self):
        """Test PLANNING → TESTING transition"""
        campaign = self._create_campaign()
        
        url = f'/api/campaigns/{campaign.id}/start-testing/'
        response = self.client.post(url, {}, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'TESTING')
        
        campaign = Campaign.objects.get(id=campaign.id)
        self.assertEqual(campaign.status, Campaign.Status.TESTING)
        
        # Verify status history was created
        history = CampaignStatusHistory.objects.filter(campaign=campaign).latest('created_at')
        self.assertEqual(history.from_status, Campaign.Status.PLANNING)
        self.assertEqual(history.to_status, Campaign.Status.TESTING)
    
    def test_start_scaling_transition(self):
        """Test TESTING → SCALING transition with performance data"""
        campaign = self._create_campaign_testing()
        
        # Create performance snapshot
        PerformanceSnapshot.objects.create(
            campaign=campaign,
            milestone_type=PerformanceSnapshot.MilestoneType.LAUNCH,
            spend=Decimal('1000.00'),
            metric_type=PerformanceSnapshot.MetricType.ROAS,
            metric_value=Decimal('3.50'),
            snapshot_by=self.user
        )
        
        url = f'/api/campaigns/{campaign.id}/start-scaling/'
        response = self.client.post(url, {}, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'SCALING')
        
        campaign = Campaign.objects.get(id=campaign.id)
        self.assertEqual(campaign.status, Campaign.Status.SCALING)
    
    def test_start_scaling_without_performance_data(self):
        """Test SCALING transition fails without performance data"""
        campaign = self._create_campaign_testing()
        
        url = f'/api/campaigns/{campaign.id}/start-scaling/'
        response = self.client.post(url, {}, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('performance', str(response.data).lower())
    
    def test_pause_transition(self):
        """Test pause transition"""
        campaign = self._create_campaign_testing()
        
        url = f'/api/campaigns/{campaign.id}/pause/'
        response = self.client.post(url, {}, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'PAUSED')
        
        campaign = Campaign.objects.get(id=campaign.id)
        self.assertEqual(campaign.status, Campaign.Status.PAUSED)
    
    def test_resume_transition(self):
        """Test resume transition"""
        campaign = self._create_campaign_testing()
        campaign.pause(user=self.user)
        campaign.save()
        
        url = f'/api/campaigns/{campaign.id}/resume/'
        response = self.client.post(url, {}, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'TESTING')
        
        campaign = Campaign.objects.get(id=campaign.id)
        self.assertEqual(campaign.status, Campaign.Status.TESTING)
    
    def test_complete_transition(self):
        """Test complete transition"""
        campaign = self._create_campaign_testing()
        campaign.end_date = timezone.now().date()
        campaign.save()
        
        url = f'/api/campaigns/{campaign.id}/complete/'
        response = self.client.post(url, {}, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'COMPLETED')
        
        campaign = Campaign.objects.get(id=campaign.id)
        self.assertEqual(campaign.status, Campaign.Status.COMPLETED)
        self.assertIsNotNone(campaign.actual_completion_date)
    
    def test_archive_transition(self):
        """Test archive transition"""
        campaign = self._create_campaign_testing()
        campaign.complete(user=self.user)
        campaign.end_date = timezone.now().date()
        campaign.save()
        
        url = f'/api/campaigns/{campaign.id}/archive/'
        response = self.client.post(url, {}, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ARCHIVED')
        
        campaign = Campaign.objects.get(id=campaign.id)
        self.assertEqual(campaign.status, Campaign.Status.ARCHIVED)
    
    def test_restore_transition(self):
        """Test restore transition"""
        campaign = self._create_campaign_testing()
        campaign.complete(user=self.user)
        campaign.end_date = timezone.now().date()
        campaign.save()
        campaign.archive(user=self.user)
        campaign.save()
        
        url = f'/api/campaigns/{campaign.id}/restore/'
        response = self.client.post(url, {}, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'COMPLETED')
        
        campaign = Campaign.objects.get(id=campaign.id)
        self.assertEqual(campaign.status, Campaign.Status.COMPLETED)
    
    def test_status_history_endpoint(self):
        """Test status history endpoint"""
        campaign = self._create_campaign()
        campaign.start_testing(user=self.user)
        campaign.save()
        
        url = f'/api/campaigns/{campaign.id}/status-history/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)
        self.assertGreaterEqual(len(response.data), 1)
        
        # Check latest history entry
        latest = response.data[0]
        self.assertEqual(latest['from_status'], 'PLANNING')
        self.assertEqual(latest['to_status'], 'TESTING')
    
    def test_activity_timeline_endpoint(self):
        """Test activity timeline endpoint aggregates all activity types"""
        campaign = self._create_campaign()
        
        # Create status change
        campaign.start_testing(user=self.user)
        campaign.save()
        
        # Create check-in
        check_in = PerformanceCheckIn.objects.create(
            campaign=campaign,
            checked_by=self.user,
            sentiment=PerformanceCheckIn.Sentiment.POSITIVE,
            note="Good progress"
        )
        
        # Create performance snapshot
        snapshot = PerformanceSnapshot.objects.create(
            campaign=campaign,
            snapshot_by=self.user,
            milestone_type=PerformanceSnapshot.MilestoneType.WEEKLY_REVIEW,
            spend=Decimal('5000.00'),
            metric_type=PerformanceSnapshot.MetricType.ROAS,
            metric_value=Decimal('3.5'),
            percentage_change=Decimal('15.2'),
            notes="Strong performance"
        )
        
        url = f'/api/campaigns/{campaign.id}/activity-timeline/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify pagination response format
        self.assertIn('count', response.data)
        self.assertIn('results', response.data)
        self.assertIn('page', response.data)
        self.assertIn('page_size', response.data)
        self.assertIn('next', response.data)
        self.assertIn('previous', response.data)
        
        # Verify default pagination (page=1, page_size=10)
        self.assertEqual(response.data['page'], 1)
        self.assertEqual(response.data['page_size'], 10)
        self.assertEqual(response.data['count'], 3)
        self.assertEqual(len(response.data['results']), 3)
        self.assertIsNone(response.data['previous'])
        self.assertIsNone(response.data['next'])  # Only 3 items, no next page
        
        # Verify all items have required fields
        for item in response.data['results']:
            self.assertIn('type', item)
            self.assertIn('id', item)
            self.assertIn('timestamp', item)
            self.assertIn('user', item)
            self.assertIn('details', item)
            self.assertIn(item['type'], ['status_change', 'check_in', 'performance_snapshot'])
        
        # Verify items are sorted by timestamp (newest first)
        timestamps = [item['timestamp'] for item in response.data['results']]
        self.assertEqual(timestamps, sorted(timestamps, reverse=True))
        
        # Verify status_change item
        status_item = next((item for item in response.data['results'] if item['type'] == 'status_change'), None)
        self.assertIsNotNone(status_item)
        self.assertEqual(status_item['details']['from_status'], 'PLANNING')
        self.assertEqual(status_item['details']['to_status'], 'TESTING')
        
        # Verify check_in item
        checkin_item = next((item for item in response.data['results'] if item['type'] == 'check_in'), None)
        self.assertIsNotNone(checkin_item)
        self.assertEqual(checkin_item['details']['sentiment'], 'POSITIVE')
        self.assertEqual(checkin_item['details']['note'], 'Good progress')
        
        # Verify performance_snapshot item
        snapshot_item = next((item for item in response.data['results'] if item['type'] == 'performance_snapshot'), None)
        self.assertIsNotNone(snapshot_item)
        self.assertEqual(snapshot_item['details']['milestone_type'], 'WEEKLY_REVIEW')
        self.assertEqual(snapshot_item['details']['spend'], '5000.00')
        self.assertEqual(snapshot_item['details']['metric_type'], 'ROAS')
        self.assertEqual(snapshot_item['details']['metric_value'], '3.5')
    
    def test_activity_timeline_empty_campaign(self):
        """Test activity timeline for campaign with no activity"""
        campaign = self._create_campaign()
        
        url = f'/api/campaigns/{campaign.id}/activity-timeline/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify pagination response format
        self.assertIn('count', response.data)
        self.assertIn('results', response.data)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(len(response.data['results']), 0)
        self.assertIsNone(response.data['previous'])
        self.assertIsNone(response.data['next'])
    
    def test_activity_timeline_permission_check(self):
        """Test activity timeline endpoint respects permissions"""
        campaign = self._create_campaign()
        
        # Switch to user without access
        self.client.force_authenticate(user=self.user_no_access)
        
        url = f'/api/campaigns/{campaign.id}/activity-timeline/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
    
    def test_activity_timeline_pagination(self):
        """Test activity timeline pagination"""
        campaign = self._create_campaign()
        
        # Create more than 10 items to test pagination
        for i in range(15):
            PerformanceCheckIn.objects.create(
                campaign=campaign,
                checked_by=self.user,
                sentiment=PerformanceCheckIn.Sentiment.POSITIVE,
                note=f"Check-in {i}"
            )
        
        url = f'/api/campaigns/{campaign.id}/activity-timeline/'
        
        # Test first page with default page_size
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['page'], 1)
        self.assertEqual(response.data['page_size'], 10)
        self.assertEqual(response.data['count'], 15)
        self.assertEqual(len(response.data['results']), 10)
        self.assertIsNone(response.data['previous'])
        self.assertIsNotNone(response.data['next'])
        
        # Test second page
        response = self.client.get(url, {'page': 2})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['page'], 2)
        self.assertEqual(len(response.data['results']), 5)
        self.assertIsNotNone(response.data['previous'])
        self.assertIsNone(response.data['next'])
        
        # Test custom page_size
        response = self.client.get(url, {'page': 1, 'page_size': 5})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['page'], 1)
        self.assertEqual(response.data['page_size'], 5)
        self.assertEqual(len(response.data['results']), 5)
        self.assertIsNone(response.data['previous'])
        self.assertIsNotNone(response.data['next'])
    
    def test_activity_timeline_pagination_invalid_params(self):
        """Test activity timeline pagination with invalid parameters"""
        campaign = self._create_campaign()
        url = f'/api/campaigns/{campaign.id}/activity-timeline/'
        
        # Test invalid page parameter
        response = self.client.get(url, {'page': 'invalid'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        
        # Test invalid page_size parameter
        response = self.client.get(url, {'page_size': 'invalid'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        
        # Test negative page (should be set to 1)
        response = self.client.get(url, {'page': -1})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['page'], 1)
        
        # Test page_size > 100 (should be capped at 100)
        response = self.client.get(url, {'page_size': 200})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['page_size'], 100)
        
        # Test page_size < 1 (should be set to 10)
        response = self.client.get(url, {'page_size': 0})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['page_size'], 10)


# ============================================================================
# CampaignViewSet Other Actions Tests
# ============================================================================

class CampaignViewSetOtherActionsTestCase(CampaignViewSetBaseTestCase):
    """Test other CampaignViewSet actions"""
    
    def test_update_archived_campaign_fails(self):
        """Test updating archived campaign fails"""
        campaign = self._create_campaign_testing()
        campaign.complete(user=self.user)
        campaign.end_date = timezone.now().date()
        campaign.save()
        campaign.archive(user=self.user)
        campaign.save()
        
        url = f'/api/campaigns/{campaign.id}/'
        data = {'name': 'Updated Name'}
        response = self.client.patch(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Archived', str(response.data))
    
    def test_delete_archived_campaign(self):
        """Test deleting archived campaign (should fail - only PLANNING can be deleted)"""
        campaign = self._create_campaign_testing()
        campaign.complete(user=self.user)
        campaign.end_date = timezone.now().date()
        campaign.save()
        campaign.archive(user=self.user)
        campaign.save()
        
        url = f'/api/campaigns/{campaign.id}/'
        response = self.client.delete(url)
        
        # Should fail - only PLANNING campaigns can be deleted
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('PLANNING', str(response.data))


# ============================================================================
# PerformanceCheckInViewSet Tests
# ============================================================================

class PerformanceCheckInViewSetTestCase(CampaignViewSetBaseTestCase):
    """Test PerformanceCheckInViewSet"""
    
    def test_list_checkins(self):
        """Test listing check-ins"""
        campaign = self._create_campaign_testing()
        checkin1 = PerformanceCheckIn.objects.create(
            campaign=campaign,
            checked_by=self.user,
            sentiment=PerformanceCheckIn.Sentiment.POSITIVE,
            note="Good progress"
        )
        checkin2 = PerformanceCheckIn.objects.create(
            campaign=campaign,
            checked_by=self.user,
            sentiment=PerformanceCheckIn.Sentiment.NEUTRAL
        )
        
        url = f'/api/campaigns/{campaign.id}/check-ins/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        self.assertEqual(len(results), 2)
    
    def test_create_checkin(self):
        """Test creating a check-in"""
        campaign = self._create_campaign_testing()
        
        url = f'/api/campaigns/{campaign.id}/check-ins/'
        data = {
            'sentiment': PerformanceCheckIn.Sentiment.POSITIVE,
            'note': 'Great performance'
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['sentiment'], PerformanceCheckIn.Sentiment.POSITIVE)
        self.assertEqual(response.data['note'], 'Great performance')
        
        # Verify check-in was created - get from database using campaign
        checkins = PerformanceCheckIn.objects.filter(campaign=campaign)
        self.assertEqual(checkins.count(), 1)
        checkin = checkins.first()
        self.assertEqual(checkin.campaign, campaign)
        self.assertEqual(checkin.checked_by, self.user)
    
    def test_create_checkin_with_invalid_sentiment(self):
        """Test creating check-in with invalid sentiment"""
        campaign = self._create_campaign_testing()
        
        url = f'/api/campaigns/{campaign.id}/check-ins/'
        data = {
            'sentiment': 'INVALID_SENTIMENT',
            'note': 'Test note'
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_update_checkin(self):
        """Test updating a check-in"""
        campaign = self._create_campaign_testing()
        checkin = PerformanceCheckIn.objects.create(
            campaign=campaign,
            checked_by=self.user,
            sentiment=PerformanceCheckIn.Sentiment.POSITIVE,
            note="Original note"
        )
        
        url = f'/api/campaigns/{campaign.id}/check-ins/{checkin.id}/'
        data = {
            'sentiment': PerformanceCheckIn.Sentiment.NEGATIVE,
            'note': 'Updated note'
        }
        response = self.client.patch(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['sentiment'], PerformanceCheckIn.Sentiment.NEGATIVE)
        self.assertEqual(response.data['note'], 'Updated note')
    
    def test_delete_checkin(self):
        """Test deleting a check-in"""
        campaign = self._create_campaign_testing()
        checkin = PerformanceCheckIn.objects.create(
            campaign=campaign,
            checked_by=self.user,
            sentiment=PerformanceCheckIn.Sentiment.POSITIVE
        )
        
        url = f'/api/campaigns/{campaign.id}/check-ins/{checkin.id}/'
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PerformanceCheckIn.objects.filter(id=checkin.id).exists())


# ============================================================================
# PerformanceSnapshotViewSet Tests
# ============================================================================

class PerformanceSnapshotViewSetTestCase(CampaignViewSetBaseTestCase):
    """Test PerformanceSnapshotViewSet"""
    
    def test_list_snapshots(self):
        """Test listing snapshots"""
        campaign = self._create_campaign_testing()
        snapshot1 = PerformanceSnapshot.objects.create(
            campaign=campaign,
            milestone_type=PerformanceSnapshot.MilestoneType.LAUNCH,
            spend=Decimal('1000.00'),
            metric_type=PerformanceSnapshot.MetricType.ROAS,
            metric_value=Decimal('3.50'),
            snapshot_by=self.user
        )
        snapshot2 = PerformanceSnapshot.objects.create(
            campaign=campaign,
            milestone_type=PerformanceSnapshot.MilestoneType.MID_TEST,
            spend=Decimal('2000.00'),
            metric_type=PerformanceSnapshot.MetricType.CTR,
            metric_value=Decimal('0.05'),
            snapshot_by=self.user
        )
        
        url = f'/api/campaigns/{campaign.id}/performance-snapshots/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        self.assertEqual(len(results), 2)
    
    def test_create_snapshot(self):
        """Test creating a snapshot"""
        campaign = self._create_campaign_testing()
        
        url = f'/api/campaigns/{campaign.id}/performance-snapshots/'
        data = {
            'milestone_type': PerformanceSnapshot.MilestoneType.LAUNCH,
            'spend': '1000.00',
            'metric_type': PerformanceSnapshot.MetricType.ROAS,
            'metric_value': '3.50'
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['spend'], '1000.00')
        self.assertEqual(response.data['metric_type'], PerformanceSnapshot.MetricType.ROAS)
        
        # Verify snapshot was created - get from database using campaign
        snapshots = PerformanceSnapshot.objects.filter(campaign=campaign)
        self.assertEqual(snapshots.count(), 1)
        snapshot = snapshots.first()
        self.assertEqual(snapshot.campaign, campaign)
        self.assertEqual(snapshot.snapshot_by, self.user)
    
    def test_create_snapshot_requires_mandatory_fields(self):
        """Test creating snapshot requires mandatory fields"""
        campaign = self._create_campaign_testing()
        
        url = f'/api/campaigns/{campaign.id}/performance-snapshots/'
        data = {
            'milestone_type': PerformanceSnapshot.MilestoneType.LAUNCH,
            # Missing spend, metric_type, metric_value
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_create_snapshot_with_invalid_metric_type(self):
        """Test creating snapshot with invalid metric_type"""
        campaign = self._create_campaign_testing()
        
        url = f'/api/campaigns/{campaign.id}/performance-snapshots/'
        data = {
            'milestone_type': PerformanceSnapshot.MilestoneType.LAUNCH,
            'spend': '1000.00',
            'metric_type': 'INVALID_METRIC',
            'metric_value': '3.50'
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_update_snapshot(self):
        """Test updating a snapshot"""
        campaign = self._create_campaign_testing()
        snapshot = PerformanceSnapshot.objects.create(
            campaign=campaign,
            milestone_type=PerformanceSnapshot.MilestoneType.LAUNCH,
            spend=Decimal('1000.00'),
            metric_type=PerformanceSnapshot.MetricType.ROAS,
            metric_value=Decimal('3.50'),
            snapshot_by=self.user
        )
        
        url = f'/api/campaigns/{campaign.id}/performance-snapshots/{snapshot.id}/'
        data = {
            'spend': '1500.00',
            'metric_value': '4.00'
        }
        response = self.client.patch(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['spend'], '1500.00')
        self.assertEqual(Decimal(response.data['metric_value']), Decimal('4.00'))


# ============================================================================
# CampaignAttachmentViewSet Tests
# ============================================================================

class CampaignAttachmentViewSetTestCase(CampaignViewSetBaseTestCase):
    """Test CampaignAttachmentViewSet"""
    
    def test_list_attachments(self):
        """Test listing attachments"""
        campaign = self._create_campaign_testing()
        attachment1 = CampaignAttachment.objects.create(
            campaign=campaign,
            uploaded_by=self.user,
            asset_type=CampaignAttachment.AssetType.DOCUMENT,
            url='https://example.com/brief.pdf'
        )
        attachment2 = CampaignAttachment.objects.create(
            campaign=campaign,
            uploaded_by=self.user,
            asset_type=CampaignAttachment.AssetType.IMAGE,
            url='https://example.com/creative.jpg'
        )
        
        url = f'/api/campaigns/{campaign.id}/attachments/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        self.assertEqual(len(results), 2)
    
    def test_create_attachment_with_url(self):
        """Test creating attachment with URL"""
        campaign = self._create_campaign_testing()
        
        url = f'/api/campaigns/{campaign.id}/attachments/'
        data = {
            'asset_type': CampaignAttachment.AssetType.DOCUMENT,
            'url': 'https://example.com/document.pdf',
            'title': 'Campaign Brief'
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['url'], 'https://example.com/document.pdf')
        self.assertEqual(response.data['asset_type'], CampaignAttachment.AssetType.DOCUMENT)
        
        # Verify attachment was created
        attachment = CampaignAttachment.objects.get(id=response.data['id'])
        self.assertEqual(attachment.campaign, campaign)
        self.assertEqual(attachment.uploaded_by, self.user)
    
    def test_create_attachment_requires_file_or_url(self):
        """Test creating attachment requires file or URL"""
        campaign = self._create_campaign_testing()
        
        url = f'/api/campaigns/{campaign.id}/attachments/'
        data = {
            'asset_type': CampaignAttachment.AssetType.DOCUMENT,
            # Missing both file and url
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    
    def test_delete_attachment(self):
        """Test deleting an attachment"""
        campaign = self._create_campaign_testing()
        attachment = CampaignAttachment.objects.create(
            campaign=campaign,
            uploaded_by=self.user,
            asset_type=CampaignAttachment.AssetType.DOCUMENT,
            url='https://example.com/document.pdf'
        )
        
        url = f'/api/campaigns/{campaign.id}/attachments/{attachment.id}/'
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(CampaignAttachment.objects.filter(id=attachment.id).exists())


# ============================================================================
# CampaignTemplateViewSet Tests
# ============================================================================

class CampaignTemplateViewSetTestCase(CampaignViewSetBaseTestCase):
    """Test CampaignTemplateViewSet"""
    
    def test_list_templates(self):
        """Test listing templates"""
        template1 = CampaignTemplate.objects.create(
            name="Template 1",
            creator=self.user,
            sharing_scope=CampaignTemplate.SharingScope.PERSONAL,
            objective=Campaign.Objective.CONVERSION,
            platforms=[Campaign.Platform.META]
        )
        template2 = CampaignTemplate.objects.create(
            name="Template 2",
            creator=self.user,
            sharing_scope=CampaignTemplate.SharingScope.TEAM,
            project=self.project,
            objective=Campaign.Objective.AWARENESS,
            platforms=[Campaign.Platform.GOOGLE_ADS]
        )
        
        url = '/api/campaign-templates/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        self.assertGreaterEqual(len(results), 2)
    
    def test_create_template(self):
        """Test creating a template"""
        url = '/api/campaign-templates/'
        data = {
            'name': 'Test Template',
            'sharing_scope': CampaignTemplate.SharingScope.PERSONAL,
            'objective': Campaign.Objective.CONVERSION,
            'platforms': [Campaign.Platform.META],
            'description': 'A test template'
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['name'], 'Test Template')
        # User ID is Integer, serializer may return as int or string
        creator_id = response.data['creator']['id']
        self.assertEqual(int(creator_id) if isinstance(creator_id, str) else creator_id, self.user.id)
        self.assertEqual(response.data['usage_count'], 0)
    
    def test_create_template_auto_versioning(self):
        """Test template auto-versioning when same name exists"""
        # Clean up any existing templates with this name first to avoid conflicts
        CampaignTemplate.objects.filter(
            name="Duplicate Template",
            creator=self.user,
            sharing_scope=CampaignTemplate.SharingScope.PERSONAL
        ).delete()
        
        # Create initial template
        template1 = CampaignTemplate.objects.create(
            name="Duplicate Template",
            creator=self.user,
            sharing_scope=CampaignTemplate.SharingScope.PERSONAL,
            objective=Campaign.Objective.CONVERSION,
            platforms=[Campaign.Platform.META],
            version_number=1
        )
        
        # Create another template with same name
        url = '/api/campaign-templates/'
        data = {
            'name': 'Duplicate Template',
            'sharing_scope': CampaignTemplate.SharingScope.PERSONAL,
            'objective': Campaign.Objective.CONVERSION,
            'platforms': [Campaign.Platform.META]
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['version_number'], 2)
        
        # Original template should be archived
        template1.refresh_from_db()
        self.assertTrue(template1.is_archived)


# ============================================================================
# Link ViewSets Tests
# ============================================================================

class CampaignTaskLinkViewSetTestCase(CampaignViewSetBaseTestCase):
    """Test CampaignTaskLinkViewSet"""
    
    def setUp(self):
        super().setUp()
        self.campaign = self._create_campaign_testing()
        self.task = Task.objects.create(
            summary="Test Task",
            project=self.project,
            owner=self.user
        )
    
    def test_create_task_link(self):
        """Test creating a task link"""
        url = '/api/campaign-task-links/'
        data = {
            'campaign': str(self.campaign.id),
            'task': self.task.id
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # CampaignTaskLinkSerializer returns campaign and task as nested objects or IDs
        if isinstance(response.data.get('campaign'), dict):
            self.assertEqual(str(response.data['campaign']['id']), str(self.campaign.id))
        else:
            self.assertEqual(str(response.data['campaign']), str(self.campaign.id))
        if isinstance(response.data.get('task'), dict):
            self.assertEqual(str(response.data['task']['id']), str(self.task.id))
        else:
            self.assertEqual(str(response.data['task']), str(self.task.id))
    
    def test_list_task_links(self):
        """Test listing task links"""
        link = CampaignTaskLink.objects.create(
            campaign=self.campaign,
            task=self.task
        )
        
        url = '/api/campaign-task-links/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        self.assertGreaterEqual(len(results), 1)
    
    def test_delete_task_link(self):
        """Test deleting a task link"""
        link = CampaignTaskLink.objects.create(
            campaign=self.campaign,
            task=self.task
        )
        
        url = f'/api/campaign-task-links/{link.id}/'
        response = self.client.delete(url)
        
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(CampaignTaskLink.objects.filter(id=link.id).exists())
    
    def test_unique_constraint(self):
        """Test unique constraint on campaign-task pair - get_or_create returns existing link"""
        existing_link = CampaignTaskLink.objects.create(
            campaign=self.campaign,
            task=self.task
        )
        
        url = '/api/campaign-task-links/'
        data = {
            'campaign': str(self.campaign.id),
            'task': self.task.id
        }
        response = self.client.post(url, data, format='json')
        
        # get_or_create returns existing link, so status is 200 OK (not 201 Created)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Verify it's the same link
        link_id = response.data['id'] if isinstance(response.data, dict) else response.data
        self.assertEqual(str(link_id), str(existing_link.id))


class CampaignDecisionLinkViewSetTestCase(CampaignViewSetBaseTestCase):
    """Test CampaignDecisionLinkViewSet"""
    
    def setUp(self):
        super().setUp()
        self.campaign = self._create_campaign_testing()
        self.decision = Decision.objects.create(
            title="Test Decision",
            author=self.user
        )
    
    def test_create_decision_link(self):
        """Test creating a decision link"""
        url = '/api/campaign-decision-links/'
        data = {
            'campaign': str(self.campaign.id),
            'decision': str(self.decision.id),
            'trigger_type': 'test_complete'
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # CampaignDecisionLinkSerializer returns campaign and decision as nested objects or IDs
        if isinstance(response.data.get('campaign'), dict):
            self.assertEqual(str(response.data['campaign']['id']), str(self.campaign.id))
        else:
            self.assertEqual(str(response.data['campaign']), str(self.campaign.id))
        if isinstance(response.data.get('decision'), dict):
            self.assertEqual(str(response.data['decision']['id']), str(self.decision.id))
        else:
            self.assertEqual(str(response.data['decision']), str(self.decision.id))
    
    def test_list_decision_links(self):
        """Test listing decision links"""
        CampaignDecisionLink.objects.create(
            campaign=self.campaign,
            decision=self.decision,
            trigger_type='test_complete'
        )
        
        url = '/api/campaign-decision-links/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        self.assertGreaterEqual(len(results), 1)


class CampaignCalendarLinkViewSetTestCase(CampaignViewSetBaseTestCase):
    """Test CampaignCalendarLinkViewSet"""
    
    def setUp(self):
        super().setUp()
        self.campaign = self._create_campaign_testing()
        self.calendar = Calendar.objects.create(
            organization=self.organization,
            owner=self.user,
            name="Test Calendar",
            timezone="UTC"
        )
        start_datetime = timezone.now()
        end_datetime = start_datetime + timezone.timedelta(hours=1)
        self.event = Event.objects.create(
            title="Test Event",
            organization=self.organization,
            calendar=self.calendar,
            created_by=self.user,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            timezone="UTC"
        )
    
    def test_create_calendar_link(self):
        """Test creating a calendar link"""
        url = '/api/campaign-calendar-links/'
        data = {
            'campaign': str(self.campaign.id),
            'event': str(self.event.id),
            'event_type': 'milestone'
        }
        response = self.client.post(url, data, format='json')
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # CampaignCalendarLinkSerializer returns campaign and event as nested objects or IDs
        if isinstance(response.data.get('campaign'), dict):
            self.assertEqual(str(response.data['campaign']['id']), str(self.campaign.id))
        else:
            self.assertEqual(str(response.data['campaign']), str(self.campaign.id))
        if isinstance(response.data.get('event'), dict):
            self.assertEqual(str(response.data['event']['id']), str(self.event.id))
        else:
            self.assertEqual(str(response.data['event']), str(self.event.id))
    
    def test_list_calendar_links(self):
        """Test listing calendar links"""
        CampaignCalendarLink.objects.create(
            campaign=self.campaign,
            event=self.event,
            event_type='milestone'
        )
        
        url = '/api/campaign-calendar-links/'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = self._get_results(response)
        self.assertGreaterEqual(len(results), 1)

