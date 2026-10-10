"""
Essential test cases for retrospective models
Tests the core retrospective task lifecycle: auto-create → complete
"""
import pytest
from decimal import Decimal
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from retrospective.models import CampaignMetric, RetrospectiveTask, Insight, RetrospectiveStatus, InsightSeverity
from core.models import Project, Organization

User = get_user_model()


class TenantRegistrationTests(SimpleTestCase):
    """
    Regression test for MED-264: RetrospectiveTask/CampaignMetric/Insight
    must stay registered as tenant models. See core/tenant_config.py's
    "MED-264 tenancy fix" comment -- these were previously public-schema-only
    despite being project-scoped.
    """

    def test_retrospective_models_are_tenant_models(self):
        from core.tenant_config import get_tenant_models

        tenant_models = get_tenant_models()
        self.assertIn(RetrospectiveTask, tenant_models)
        self.assertIn(CampaignMetric, tenant_models)
        self.assertIn(Insight, tenant_models)


@pytest.mark.timeout(600)
class RetrospectiveTaskLifecycleTest(TestCase):
    """Test retrospective task lifecycle (auto-create → complete)"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.organization = Organization.objects.create(
            name='Test Organization',
            email_domain='test.com'
        )
        
        self.campaign = Project.objects.create(
            name='Test Campaign',
            organization=self.organization
        )
    
    def test_retrospective_task_lifecycle(self):
        """Test complete retrospective lifecycle: scheduled → in_progress → completed → reported"""
        
        # Create retrospective (auto-create simulation)
        retrospective = RetrospectiveTask.objects.create(
            campaign=self.campaign,
            created_by=self.user,
            status=RetrospectiveStatus.SCHEDULED
        )
        
        self.assertEqual(retrospective.status, RetrospectiveStatus.SCHEDULED)
        
        # Transition to in_progress
        self.assertTrue(retrospective.can_transition_to(RetrospectiveStatus.IN_PROGRESS))
        retrospective.status = RetrospectiveStatus.IN_PROGRESS
        retrospective.started_at = timezone.now()
        retrospective.save()
        
        # Transition to completed
        self.assertTrue(retrospective.can_transition_to(RetrospectiveStatus.COMPLETED))
        retrospective.status = RetrospectiveStatus.COMPLETED
        retrospective.completed_at = timezone.now()
        retrospective.save()
        
        # Transition to reported
        self.assertTrue(retrospective.can_transition_to(RetrospectiveStatus.REPORTED))
        retrospective.status = RetrospectiveStatus.REPORTED
        retrospective.save()
        
        # Verify lifecycle completion
        self.assertEqual(retrospective.status, RetrospectiveStatus.REPORTED)
        self.assertIsNotNone(retrospective.duration)
    
    def test_invalid_status_transitions(self):
        """Test invalid status transitions are blocked"""
        
        retrospective = RetrospectiveTask.objects.create(
            campaign=self.campaign,
            created_by=self.user,
            status=RetrospectiveStatus.SCHEDULED
        )
        
        # Cannot skip states
        self.assertFalse(retrospective.can_transition_to(RetrospectiveStatus.COMPLETED))
        self.assertFalse(retrospective.can_transition_to(RetrospectiveStatus.REPORTED))
    
    def test_retrospective_timing_validation(self):
        """Test retrospective timing constraints"""
        
        start_time = timezone.now()
        end_time = start_time - timezone.timedelta(hours=1)
        
        retrospective = RetrospectiveTask.objects.create(
            campaign=self.campaign,
            created_by=self.user,
            started_at=start_time,
            completed_at=end_time
        )
        
        with self.assertRaises(ValidationError):
            retrospective.full_clean()

    def test_confidence_level_validation(self):
        """Test confidence_level must stay in 1..5 options"""
        retrospective = RetrospectiveTask(
            campaign=self.campaign,
            created_by=self.user,
            confidence_level=6
        )

        with self.assertRaises(ValidationError):
            retrospective.full_clean()
    

    def test_confidence_level_type_validation(self):
        """Test that confidence_level should be an integer not a string"""
        retrospective = RetrospectiveTask(
            campaign=self.campaign,
            created_by=self.user,
            confidence_level="high",
        )
        with self.assertRaises(ValidationError):
            retrospective.full_clean()
    
    def test_confidence_level_float_validation(self):
        """Test that confidence_level should be an integer not a float"""
        retrospective = RetrospectiveTask(
            campaign=self.campaign,
            created_by=self.user,
            confidence_level=3.5,
        )
        with self.assertRaises(ValidationError):
            retrospective.full_clean()

    def test_post_outcome_choice_validation(self):
        """Test post-outcome option fields reject invalid values"""
        retrospective = RetrospectiveTask(
            campaign=self.campaign,
            created_by=self.user,
            outcome_compared_to_expectation='unexpectedly_good',
            would_make_same_decision_again='maybe',
        )
        with self.assertRaises(ValidationError):
            retrospective.full_clean()


@pytest.mark.timeout(600)
class InsightGenerationTest(TestCase):
    """Test insight generation under different KPI inputs"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.organization = Organization.objects.create(
            name='Test Organization',
            email_domain='test.com'
        )
        
        self.campaign = Project.objects.create(
            name='Test Campaign',
            organization=self.organization
        )
        
        self.retrospective = RetrospectiveTask.objects.create(
            campaign=self.campaign,
            created_by=self.user
        )
    
    def test_insight_creation_with_rule_engine(self):
        """Test creating insights from rule engine"""
        
        insight = Insight.objects.create(
            retrospective=self.retrospective,
            title='Poor ROI Performance',
            description='ROI is below target threshold',
            severity=InsightSeverity.CRITICAL,
            rule_id='roi_poor',
            created_by=self.user,
            generated_by='rule_engine',
            triggered_kpis=['ROI', 'CPC', 'CTR'],
            suggested_actions=[
                'Review audience targeting',
                'Optimize ad copy',
                'Adjust bid strategy'
            ]
        )
        
        self.assertEqual(insight.rule_id, 'roi_poor')
        self.assertEqual(insight.generated_by, 'rule_engine')
        self.assertEqual(len(insight.triggered_kpis), 3)
        self.assertEqual(len(insight.suggested_actions), 3)
    
    def test_insight_severity_based_on_kpi_performance(self):
        """Test insight severity assignment based on KPI performance levels"""
        
        # Critical insight for very poor performance
        critical_insight = Insight.objects.create(
            retrospective=self.retrospective,
            title='Critical Performance Issue',
            description='Multiple KPIs below critical thresholds',
            severity=InsightSeverity.CRITICAL,
            rule_id='multi_kpi_critical',
            created_by=self.user
        )
        
        # Medium insight for moderate issues
        medium_insight = Insight.objects.create(
            retrospective=self.retrospective,
            title='Performance Optimization Needed',
            description='Some KPIs below target but not critical',
            severity=InsightSeverity.MEDIUM,
            rule_id='performance_medium',
            created_by=self.user
        )
        
        # Verify severity levels
        self.assertEqual(critical_insight.severity, InsightSeverity.CRITICAL)
        self.assertEqual(medium_insight.severity, InsightSeverity.MEDIUM)
    
    def test_insight_validation_requirements(self):
        """Test insight validation for required fields"""
        
        # Test that retrospective is required
        with self.assertRaises(Exception):
            Insight.objects.create(
                title='Test Insight',
                description='No retrospective insight',
                severity=InsightSeverity.MEDIUM,
                created_by=self.user
            )


@pytest.mark.timeout(600)
class RetrospectiveModelIntegrationTest(TestCase):
    """Integration tests for retrospective models"""
    
    def setUp(self):
        """Set up test data"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        
        self.organization = Organization.objects.create(
            name='Integration Test Organization',
            email_domain='test.com'
        )
        
        self.campaign = Project.objects.create(
            name='Integration Test Campaign',
            organization=self.organization
        )
        
        self.retrospective = RetrospectiveTask.objects.create(
            campaign=self.campaign,
            created_by=self.user,
            status=RetrospectiveStatus.IN_PROGRESS,
            started_at=timezone.now()
        )
    
    def test_complete_workflow_with_insights(self):
        """Test complete retrospective workflow with insight generation"""
        
        # Create insights with different severity levels
        insights_data = [
            ('Critical ROI Issue', InsightSeverity.CRITICAL),
            ('Medium CTR Performance', InsightSeverity.MEDIUM),  
            ('Low Impression Share', InsightSeverity.LOW)
        ]
        
        for title, severity in insights_data:
            Insight.objects.create(
                retrospective=self.retrospective,
                title=title,
                description=f'Analysis for {title}',
                severity=severity,
                created_by=self.user
            )
        
        # Complete the retrospective
        self.retrospective.status = RetrospectiveStatus.COMPLETED
        self.retrospective.completed_at = timezone.now()
        self.retrospective.save()
        
        # Verify insights were created with different severities
        insights = self.retrospective.insights.all()
        self.assertEqual(insights.count(), 3)
        
        severity_counts = {
            InsightSeverity.CRITICAL: insights.filter(severity=InsightSeverity.CRITICAL).count(),
            InsightSeverity.MEDIUM: insights.filter(severity=InsightSeverity.MEDIUM).count(),
            InsightSeverity.LOW: insights.filter(severity=InsightSeverity.LOW).count()
        }
        
        self.assertEqual(severity_counts[InsightSeverity.CRITICAL], 1)
        self.assertEqual(severity_counts[InsightSeverity.MEDIUM], 1)
        self.assertEqual(severity_counts[InsightSeverity.LOW], 1)

    def test_retrospective_status_transitions(self):
        """Test valid status transitions"""
        retrospective = RetrospectiveTask.objects.create(
            campaign=self.campaign,
            created_by=self.user,
            status=RetrospectiveStatus.SCHEDULED
        )
        
        # Test valid transitions
        self.assertTrue(retrospective.can_transition_to(RetrospectiveStatus.IN_PROGRESS))
        self.assertTrue(retrospective.can_transition_to(RetrospectiveStatus.CANCELLED))
        
        # Transition to in_progress
        retrospective.status = RetrospectiveStatus.IN_PROGRESS
        retrospective.started_at = timezone.now()
        retrospective.save()
        
        # Test next valid transitions
        self.assertTrue(retrospective.can_transition_to(RetrospectiveStatus.COMPLETED))
        self.assertTrue(retrospective.can_transition_to(RetrospectiveStatus.CANCELLED))
        self.assertFalse(retrospective.can_transition_to(RetrospectiveStatus.SCHEDULED))
        
        # Transition to completed
        retrospective.status = RetrospectiveStatus.COMPLETED
        retrospective.completed_at = timezone.now()
        retrospective.save()
        
        # Test final transition
        self.assertTrue(retrospective.can_transition_to(RetrospectiveStatus.REPORTED))
        self.assertFalse(retrospective.can_transition_to(RetrospectiveStatus.IN_PROGRESS))


    def test_concurrent_retrospective_creation(self):
        """Test handling of concurrent retrospective creation"""
        from django.db import transaction
        from retrospective.services import RetrospectiveService
        
        # Create another user for concurrent testing
        user2 = User.objects.create_user(
            username='testuser2',
            email='test2@example.com',
            password='testpass123'
        )
        
        # Simulate concurrent creation attempts
        with transaction.atomic():
            # First creation should succeed
            retrospective1 = RetrospectiveService.create_retrospective_for_campaign(
                campaign_id=str(self.campaign.id),
                created_by=self.user
            )
            
            # Second creation should return existing retrospective
            retrospective2 = RetrospectiveService.create_retrospective_for_campaign(
                campaign_id=str(self.campaign.id),
                created_by=user2
            )
            
            # Should be the same retrospective
            self.assertEqual(retrospective1.id, retrospective2.id) 