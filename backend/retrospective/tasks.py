"""
Celery tasks for retrospective engine
Handles background processing for retrospective generation and KPI analysis
"""
from typing import Dict, List, Any, Optional
from decimal import Decimal
import random
from django.utils import timezone
from django.contrib.auth import get_user_model
from celery import shared_task
from celery.utils.log import get_task_logger

from .models import RetrospectiveTask, Insight, RetrospectiveStatus, CampaignMetric
from .services import RetrospectiveService
from .rules import InsightRules

User = get_user_model()
logger = get_task_logger(__name__)


@shared_task(bind=True, max_retries=3)
def generate_retrospective(self, campaign_id: str, created_by_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Main Celery task to generate retrospective for a completed campaign
    
    Args:
        campaign_id: ID of the campaign
        created_by_id: ID of the user creating the retrospective
        
    Returns:
        Dictionary with task results
    """
    try:
        logger.info(f"Starting retrospective generation for campaign: {campaign_id}")
        
        # Get user if provided
        created_by = None
        if created_by_id:
            try:
                created_by = User.objects.get(id=created_by_id)
            except User.DoesNotExist:
                logger.warning(f"User with ID {created_by_id} not found, using system user")
        
        # Create retrospective task
        retrospective = RetrospectiveService.create_retrospective_for_campaign(
            campaign_id=campaign_id,
            created_by=created_by or User.objects.filter(is_superuser=True).first()
        )
        
        logger.info(f"Created retrospective task: {retrospective.id}")
        
        # Update task status to in progress
        retrospective.status = RetrospectiveStatus.IN_PROGRESS
        retrospective.started_at = timezone.now()
        retrospective.save()
        
        # Generate mock KPI data (synchronously in tests or when broker unavailable)
        try:
            mock_kpis = generate_mock_kpi_data.delay(str(retrospective.id))
            # Wait for KPI data to be generated
            kpi_result = mock_kpis.get(timeout=60)
        except Exception:
            # Fallback to direct call if Celery broker is unavailable
            kpi_result = generate_mock_kpi_data(str(retrospective.id))
        
        if not kpi_result.get('success'):
            raise Exception(f"Failed to generate KPI data: {kpi_result.get('error')}")
        
        logger.info(f"Generated {kpi_result.get('kpi_count', 0)} KPIs for retrospective")
        
        # Generate insights using rule engine
        insights = RetrospectiveService.generate_insights_batch(
            retrospective_id=str(retrospective.id),
            user=created_by
        )
        
        logger.info(f"Generated {len(insights)} insights for retrospective")
        
        # Mark retrospective as completed
        retrospective.status = RetrospectiveStatus.COMPLETED
        retrospective.completed_at = timezone.now()
        retrospective.save()
        
        # Generate report
        report_url = RetrospectiveService.generate_report(str(retrospective.id))
        
        logger.info(f"Generated report: {report_url}")
        
        return {
            'success': True,
            'retrospective_id': str(retrospective.id),
            'campaign_id': campaign_id,
            'kpi_count': kpi_result.get('kpi_count', 0),
            'insight_count': len(insights),
            'report_url': report_url,
            'duration_seconds': (retrospective.completed_at - retrospective.started_at).total_seconds()
        }
        
    except Exception as exc:
        logger.error(f"Error generating retrospective for campaign {campaign_id}: {str(exc)}")
        # Do not raise retry in tests; return error payload
        return {
            'success': False,
            'error': str(exc),
            'campaign_id': campaign_id
        }


@shared_task(bind=True)
def generate_mock_kpi_data(self, retrospective_id: str) -> Dict[str, Any]:
    """
    Generate mock KPI data for testing and demonstration using CampaignMetric
    
    Args:
        retrospective_id: ID of the retrospective task
        
    Returns:
        Dictionary with KPI generation results
    """
    try:
        logger.info(f"Generating mock KPI data for retrospective: {retrospective_id}")
        
        retrospective = RetrospectiveTask.objects.get(id=retrospective_id)
        campaign = retrospective.campaign
        
        # Generate mock CampaignMetric data for the last 7 days
        from datetime import timedelta
        
        mock_metrics = []
        for day in range(7):
            date = timezone.now().date() - timedelta(days=day)
            
            # Generate realistic mock data
            impressions = random.randint(10000, 50000)
            clicks = random.randint(100, 1000)
            conversions = random.randint(10, 100)
            
            # Calculate derived metrics
            ctr = Decimal(clicks / impressions) if impressions > 0 else Decimal('0')
            conversion_rate = Decimal(conversions / clicks) if clicks > 0 else Decimal('0')
            cpc = Decimal(random.uniform(1.0, 5.0))
            cpm = Decimal(random.uniform(5.0, 20.0))
            cpa = Decimal(random.uniform(20.0, 100.0))
            
            metric = CampaignMetric.objects.create(
                campaign=campaign,
                impressions=impressions,
                clicks=clicks,
                conversions=conversions,
                cost_per_click=cpc,
                cost_per_impression=cpm,
                cost_per_conversion=cpa,
                click_through_rate=ctr,
                conversion_rate=conversion_rate,
                date=date
            )
            mock_metrics.append(metric)
        
        logger.info(f"Generated {len(mock_metrics)} mock CampaignMetrics for retrospective {retrospective_id}")
        
        return {
            'success': True,
            'retrospective_id': retrospective_id,
            'campaign_id': str(campaign.id),
            'metric_count': len(mock_metrics),
            'date_range': f"{min(m.date for m in mock_metrics)} to {max(m.date for m in mock_metrics)}"
        }
        
    except Exception as exc:
        logger.error(f"Error generating mock KPI data for retrospective {retrospective_id}: {str(exc)}")
        return {
            'success': False,
            'error': str(exc),
            'retrospective_id': retrospective_id
        }


@shared_task(bind=True)
def generate_insights_for_retrospective(self, retrospective_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Generate insights for a retrospective using rule engine
    
    Args:
        retrospective_id: ID of the retrospective task
        user_id: ID of the user generating insights
        
    Returns:
        Dictionary with insight generation results
    """
    try:
        logger.info(f"Generating insights for retrospective: {retrospective_id}")
        
        # Get user if provided
        user = None
        if user_id:
            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                logger.warning(f"User with ID {user_id} not found")
        
        # Generate insights
        insights = RetrospectiveService.generate_insights_batch(
            retrospective_id=retrospective_id,
            user=user
        )
        
        # Count insights by severity
        severity_counts = {}
        for insight in insights:
            severity = insight.severity
            severity_counts[severity] = severity_counts.get(severity, 0) + 1
        
        logger.info(f"Generated {len(insights)} insights for retrospective {retrospective_id}")
        
        return {
            'success': True,
            'insight_count': len(insights),
            'severity_counts': severity_counts,
            'retrospective_id': retrospective_id
        }
        
    except Exception as exc:
        logger.error(f"Error generating insights for retrospective {retrospective_id}: {str(exc)}")
        return {
            'success': False,
            'error': str(exc),
            'retrospective_id': retrospective_id
        }


@shared_task(bind=True)
def generate_report_for_retrospective(self, retrospective_id: str) -> Dict[str, Any]:
    """
    Generate PDF report for a retrospective
    
    Args:
        retrospective_id: ID of the retrospective task
        
    Returns:
        Dictionary with report generation results
    """
    try:
        logger.info(f"Generating report for retrospective: {retrospective_id}")
        
        # Generate report
        report_url = RetrospectiveService.generate_report(retrospective_id)
        
        logger.info(f"Generated report: {report_url}")
        
        return {
            'success': True,
            'report_url': report_url,
            'retrospective_id': retrospective_id
        }
        
    except Exception as exc:
        logger.error(f"Error generating report for retrospective {retrospective_id}: {str(exc)}")
        return {
            'success': False,
            'error': str(exc),
            'retrospective_id': retrospective_id
        }


@shared_task(bind=True)
def cleanup_old_retrospectives(self, days_old: int = 90) -> Dict[str, Any]:
    """
    Clean up old retrospective data
    
    Args:
        days_old: Age threshold for cleanup (default 90 days)
        
    Returns:
        Dictionary with cleanup results
    """
    try:
        logger.info(f"Cleaning up retrospectives older than {days_old} days")
        
        cutoff_date = timezone.now() - timezone.timedelta(days=days_old)
        
        # Find old retrospectives
        old_retrospectives = RetrospectiveTask.objects.filter(
            created_at__lt=cutoff_date,
            status__in=[RetrospectiveStatus.COMPLETED, RetrospectiveStatus.REPORTED]
        )
        
        count = old_retrospectives.count()
        
        # Delete old retrospectives (this will cascade to KPIs and Insights)
        old_retrospectives.delete()
        
        logger.info(f"Cleaned up {count} old retrospectives")
        
        return {
            'success': True,
            'deleted_count': count,
            'cutoff_date': cutoff_date.isoformat()
        }
        
    except Exception as exc:
        logger.error(f"Error cleaning up old retrospectives: {str(exc)}")
        return {
            'success': False,
            'error': str(exc)
        }


@shared_task(bind=True)
def update_kpi_data_from_external_sources(self, retrospective_id: str) -> Dict[str, Any]:
    """
    Update KPI data from external sources (Google Ads, Facebook, TikTok)
    
    Args:
        retrospective_id: ID of the retrospective task
        
    Returns:
        Dictionary with update results
    """
    try:
        logger.info(f"Updating KPI data for retrospective: {retrospective_id}")
        
        # This would integrate with external APIs in real implementation
        # For now, we'll just log the task
        
        # Mock external API calls
        external_sources = ['google_ads', 'facebook', 'tiktok']
        updated_count = 0
        
        for source in external_sources:
            # Simulate API call delay
            import time
            time.sleep(0.1)
            
            # Mock successful update
            updated_count += 1
            logger.info(f"Updated KPI data from {source}")
        
        return {
            'success': True,
            'updated_sources': external_sources,
            'updated_count': updated_count,
            'retrospective_id': retrospective_id
        }
        
    except Exception as exc:
        logger.error(f"Error updating KPI data for retrospective {retrospective_id}: {str(exc)}")
        return {
            'success': False,
            'error': str(exc),
            'retrospective_id': retrospective_id
        } 