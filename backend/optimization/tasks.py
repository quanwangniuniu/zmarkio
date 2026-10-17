from celery import shared_task
from datetime import timedelta
from django.utils import timezone
from django.conf import settings
import logging
from typing import Dict, List, Optional
import requests

from .models import OptimizationExperiment, ExperimentMetric, ScalingAction

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def ingest_experiment_metrics(self):
    """
    Daily task to ingest experiment metrics from platform APIs
    
    Requirements:
    - Run daily via Celery Beat
    - Fetch metrics for all running experiments
    - Call platform APIs (Facebook, TikTok, Instagram, etc.)
    - Store metrics in ExperimentMetric model
    - Handle API failures with retries
    - Log detailed progress and errors
    
    Returns:
        dict: Task execution summary
    """
    logger.info("Starting daily experiment metrics ingestion task")
    
    try:
        # Get all running experiments
        running_experiments = OptimizationExperiment.objects.filter(
            status=OptimizationExperiment.ExperimentStatus.RUNNING
        ).select_related('created_by')
        
        logger.info(f"Found {running_experiments.count()} running experiments")
        
        if not running_experiments.exists():
            logger.info("No running experiments found, skipping metrics ingestion")
            return {
                'status': 'completed',
                'message': 'No running experiments found',
                'total_metrics_ingested': 0,
                'failed_experiments': [],
                'timestamp': timezone.now().isoformat()
            }
        
        total_metrics_ingested = 0
        failed_experiments = []
        successful_experiments = []
        
        for experiment in running_experiments:
            try:
                logger.info(f"Processing experiment {experiment.id}: {experiment.name}")
                
                # Ingest metrics for each campaign in the experiment
                campaign_metrics = _fetch_platform_metrics(experiment)
                
                if not campaign_metrics:
                    logger.warning(f"No metrics retrieved for experiment {experiment.id}")
                    failed_experiments.append({
                        'experiment_id': experiment.id,
                        'error': 'No metrics retrieved from platform APIs'
                    })
                    continue
                
                # Store metrics in database
                metrics_created = _store_experiment_metrics(experiment, campaign_metrics)
                total_metrics_ingested += metrics_created
                successful_experiments.append(experiment.id)
                
                logger.info(f"Successfully ingested {metrics_created} metrics for experiment {experiment.id}")
                
            except Exception as e:
                error_msg = f"Failed to ingest metrics for experiment {experiment.id}: {str(e)}"
                logger.error(error_msg)
                failed_experiments.append({
                    'experiment_id': experiment.id,
                    'error': str(e)
                })
        
        result = {
            'status': 'completed',
            'total_experiments_processed': len(running_experiments),
            'successful_experiments': len(successful_experiments),
            'failed_experiments': len(failed_experiments),
            'total_metrics_ingested': total_metrics_ingested,
            'failed_experiment_details': failed_experiments,
            'timestamp': timezone.now().isoformat()
        }
        
        logger.info(f"Metrics ingestion completed: {result}")
        return result
        
    except Exception as e:
        error_msg = f"Critical error in metrics ingestion task: {str(e)}"
        logger.error(error_msg)
        
        # Retry the task if it's a transient error
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying metrics ingestion task (attempt {self.request.retries + 1})")
            raise self.retry(countdown=300, exc=e)
        
        raise Exception(error_msg)


@shared_task(bind=True, max_retries=2, default_retry_delay=180)
def evaluate_scaling_rules(self):
    """
    Task to check conditions for scaling actions
    
    Requirements:
    - Evaluate scaling rules based on performance thresholds
    - Check experiments with recent metrics (last 24 hours)
    - Create scaling actions when conditions are met
    - Handle different scaling action types
    - Log scaling decisions and actions
    
    Returns:
        dict: Task execution summary
    """
    logger.info("Starting scaling rules evaluation task")
    
    try:
        # Get running experiments with recent metrics (last 24 hours)
        cutoff_time = timezone.now() - timedelta(hours=24)
        
        running_experiments = OptimizationExperiment.objects.filter(
            status=OptimizationExperiment.ExperimentStatus.RUNNING
        ).prefetch_related('owned_experiment_metrics')
        
        logger.info(f"Found {running_experiments.count()} running experiments to evaluate")
        
        scaling_actions_created = 0
        experiments_evaluated = 0
        scaling_decisions = []
        
        for experiment in running_experiments:
            try:
                # Check if experiment has recent metrics
                recent_metrics = experiment.owned_experiment_metrics.filter(
                    recorded_at__gte=cutoff_time
                )
                
                if not recent_metrics.exists():
                    logger.warning(f"No recent metrics found for experiment {experiment.id} (last 24h)")
                    continue
                
                experiments_evaluated += 1
                logger.info(f"Evaluating scaling rules for experiment {experiment.id}: {experiment.name}")
                
                # Evaluate scaling conditions for each campaign
                campaign_decisions = _evaluate_scaling_conditions(experiment, recent_metrics)
                
                # Create scaling actions based on decisions
                for decision in campaign_decisions:
                    if decision['should_scale']:
                        scaling_action = _create_scaling_action(experiment, decision)
                        if scaling_action:
                            scaling_actions_created += 1
                            scaling_decisions.append({
                                'experiment_id': experiment.id,
                                'scaling_action_id': scaling_action.id,
                                'action_type': decision['action_type'],
                                'campaign_id': decision['campaign_id'],
                                'reason': decision['action_details'].get('reason', 'Performance-based scaling')
                            })
                            logger.info(f"Created scaling action {scaling_action.id} for experiment {experiment.id}")
                
            except Exception as e:
                logger.error(f"Failed to evaluate scaling rules for experiment {experiment.id}: {str(e)}")
        
        result = {
            'status': 'completed',
            'experiments_evaluated': experiments_evaluated,
            'scaling_actions_created': scaling_actions_created,
            'scaling_decisions': scaling_decisions,
            'timestamp': timezone.now().isoformat()
        }
        
        logger.info(f"Scaling evaluation completed: {result}")
        return result
        
    except Exception as e:
        error_msg = f"Critical error in scaling rules evaluation task: {str(e)}"
        logger.error(error_msg)
        
        # Retry the task if it's a transient error
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying scaling evaluation task (attempt {self.request.retries + 1})")
            raise self.retry(countdown=180, exc=e)
        
        raise Exception(error_msg)


@shared_task(bind=True, max_retries=2, default_retry_delay=600)
def recompute_all_pacing_forecasts(self):
    """
    Nightly task to recompute budget pacing forecasts for active campaigns.

    Scheduled after the Meta daily fan-out so it reads fresh insight rows.
    One campaign failing does not abort the run; failures are collected and
    reported in the summary.

    Returns:
        dict: Task execution summary
    """
    from campaign.models import Campaign
    from .services import PacingService

    logger.info("Starting nightly pacing forecast recomputation")

    today = timezone.now().date()

    # Archived campaigns are read-only and no longer worth pacing.
    campaigns = Campaign.objects.filter(
        is_deleted=False
    ).exclude(
        status=Campaign.Status.ARCHIVED
    ).only('id', 'start_date', 'end_date', 'budget_estimate')

    recomputed = 0
    failed = []

    for campaign in campaigns.iterator():
        try:
            PacingService.recompute_for_campaign(campaign, today=today)
            recomputed += 1
        except Exception as e:
            logger.error(
                f"Failed to recompute pacing for campaign {campaign.id}: {e}"
            )
            failed.append({'campaign_id': campaign.id, 'error': str(e)})

    result = {
        'status': 'completed',
        'recomputed': recomputed,
        'failed': failed,
        'computed_for_date': today.isoformat(),
        'timestamp': timezone.now().isoformat(),
    }

    logger.info(f"Pacing forecast recomputation completed: {result}")
    return result


# ==================== HELPER FUNCTIONS ====================

def _fetch_platform_metrics(experiment: OptimizationExperiment) -> Dict[str, Dict[str, float]]:
    """Fetch metrics from platform APIs for an experiment's campaigns"""
    campaign_metrics = {}
    
    for campaign_id in experiment.linked_campaign_ids:
        try:
            platform, campaign_number = campaign_id.split(':', 1)
            logger.info(f"Fetching metrics for {campaign_id} from {platform}")
            
            # Call appropriate platform API
            if platform.lower() == 'fb':
                metrics = _fetch_facebook_metrics(campaign_number)
            elif platform.lower() == 'tt':
                metrics = _fetch_tiktok_metrics(campaign_number)
            elif platform.lower() == 'ins':
                metrics = _fetch_instagram_metrics(campaign_number)
            else:
                logger.warning(f"Unknown platform: {platform} for campaign {campaign_id}")
                continue
            
            if metrics:
                campaign_metrics[campaign_id] = metrics
                logger.info(f"Successfully fetched {len(metrics)} metrics for {campaign_id}")
                
        except Exception as e:
            logger.error(f"Failed to fetch metrics for campaign {campaign_id}: {str(e)}")
            continue
    
    return campaign_metrics


def _store_experiment_metrics(experiment: OptimizationExperiment, campaign_metrics: Dict) -> int:
    """Store campaign metrics in the database"""
    metrics_created = 0
    
    for campaign_id, metrics in campaign_metrics.items():
        for metric_name, metric_value in metrics.items():
            try:
                # Add campaign prefix to metric name for better organization
                prefixed_metric_name = f"{campaign_id}_{metric_name}"
                
                ExperimentMetric.objects.create(
                    experiment_id=experiment,
                    metric_name=prefixed_metric_name,
                    metric_value=float(metric_value)
                )
                metrics_created += 1
                
            except Exception as e:
                logger.error(f"Failed to store metric {metric_name} for campaign {campaign_id}: {str(e)}")
    
    return metrics_created


def _evaluate_scaling_conditions(experiment: OptimizationExperiment, recent_metrics) -> List[Dict]:
    """Evaluate scaling conditions for an experiment"""
    scaling_decisions = []
    
    # Group metrics by campaign
    campaign_metrics = {}
    for metric in recent_metrics:
        # Extract campaign ID from metric name (format: campaign_id_metric_name)
        if '_' in metric.metric_name:
            campaign_id, metric_name = metric.metric_name.split('_', 1)
        else:
            campaign_id = 'default'
            metric_name = metric.metric_name
        
        if campaign_id not in campaign_metrics:
            campaign_metrics[campaign_id] = {}
        
        campaign_metrics[campaign_id][metric_name] = metric.metric_value
    
    # Evaluate each campaign
    for campaign_id, metrics in campaign_metrics.items():
        if len(metrics) < 3:  # Need at least 3 metrics for proper evaluation
            logger.warning(f"Insufficient metrics for campaign {campaign_id}, skipping evaluation")
            continue
            
        decision = _analyze_campaign_performance(campaign_id, metrics, experiment)
        if decision:
            scaling_decisions.append(decision)
    
    return scaling_decisions


def _analyze_campaign_performance(campaign_id: str, metrics: Dict[str, float], experiment: OptimizationExperiment) -> Optional[Dict]:
    """Analyze campaign performance and determine scaling actions"""
    # Define scaling thresholds (configurable via settings)
    SCALING_THRESHOLDS = {
        'CTR_HIGH': getattr(settings, 'SCALING_CTR_HIGH_THRESHOLD', 0.05),  # 5% CTR
        'CTR_LOW': getattr(settings, 'SCALING_CTR_LOW_THRESHOLD', 0.02),    # 2% CTR
        'CPA_HIGH': getattr(settings, 'SCALING_CPA_HIGH_THRESHOLD', 50.0),  # $50 CPA
        'CPA_LOW': getattr(settings, 'SCALING_CPA_LOW_THRESHOLD', 20.0),    # $20 CPA
        'ROAS_MIN': getattr(settings, 'SCALING_ROAS_MIN_THRESHOLD', 2.0),   # 2x ROAS
        'SPEND_MIN': getattr(settings, 'SCALING_SPEND_MIN_THRESHOLD', 100.0) # $100 min spend
    }
    
    # Get relevant metrics with defaults
    ctr = metrics.get('ctr', 0)
    cpa = metrics.get('cpa', 0)
    spend = metrics.get('spend', 0)
    conversions = metrics.get('conversions', 0)
    roas = metrics.get('roas', 0)
    impressions = metrics.get('impressions', 0)
    
    # Skip evaluation if insufficient spend
    if spend < SCALING_THRESHOLDS['SPEND_MIN']:
        logger.info(f"Campaign {campaign_id} has insufficient spend (${spend:.2f}), skipping scaling evaluation")
        return None
    
    # Determine scaling action based on performance
    if (ctr > SCALING_THRESHOLDS['CTR_HIGH'] and 
        cpa < SCALING_THRESHOLDS['CPA_LOW'] and 
        roas > SCALING_THRESHOLDS['ROAS_MIN']):
        # High performing campaign - scale up budget
        return {
            'campaign_id': campaign_id,
            'should_scale': True,
            'action_type': ScalingAction.ScalingActionType.BUDGET_INCREASE,
            'action_details': {
                'increase_percentage': 25,
                'current_spend': spend,
                'reason': f'High performance: CTR {ctr:.2%}, CPA ${cpa:.2f}, ROAS {roas:.2f}x'
            }
        }
    
    elif (ctr < SCALING_THRESHOLDS['CTR_LOW'] and 
          cpa > SCALING_THRESHOLDS['CPA_HIGH']):
        # Low performing campaign - scale down budget
        return {
            'campaign_id': campaign_id,
            'should_scale': True,
            'action_type': ScalingAction.ScalingActionType.BUDGET_DECREASE,
            'action_details': {
                'decrease_percentage': 30,
                'current_spend': spend,
                'reason': f'Low performance: CTR {ctr:.2%}, CPA ${cpa:.2f}'
            }
        }
    
    elif (conversions > 0 and 
          ctr < SCALING_THRESHOLDS['CTR_LOW'] and 
          impressions > 10000):
        # Has conversions but low CTR - try audience expansion
        return {
            'campaign_id': campaign_id,
            'should_scale': True,
            'action_type': ScalingAction.ScalingActionType.AUDIENCE_EXPAND,
            'action_details': {
                'expansion_factor': 1.5,
                'current_impressions': impressions,
                'reason': f'Low CTR {ctr:.2%} but has {conversions} conversions, expanding audience'
            }
        }
    
    return None


def _create_scaling_action(experiment: OptimizationExperiment, decision: Dict, performed_by=None) -> Optional[ScalingAction]:
    """Create a scaling action based on decision"""
    try:
        scaling_action = ScalingAction.objects.create(
            experiment_id=experiment,
            campaign_id=decision['campaign_id'],
            action_type=decision['action_type'],
            action_details=decision['action_details'],
            performed_by=performed_by
        )
        
        logger.info(f"Created scaling action {scaling_action.id}: {decision['action_type']} for {decision['campaign_id']}")
        return scaling_action
        
    except Exception as e:
        logger.error(f"Failed to create scaling action: {str(e)}")
        return None


# ==================== PLATFORM API FUNCTIONS ====================

def _fetch_facebook_metrics(campaign_id: str) -> Dict[str, float]:
    """
    Fetch metrics from Facebook Marketing API
    
    Args:
        campaign_id: Facebook campaign ID
        
    Returns:
        dict: Campaign metrics
    """
    try:
        access_token = getattr(settings, 'FACEBOOK_ACCESS_TOKEN', None)
        if not access_token:
            logger.error("Facebook access token not configured")
            return {}
        
        # Facebook Marketing API endpoint
        url = f"https://graph.facebook.com/v18.0/{campaign_id}/insights"
        
        params = {
            'access_token': access_token,
            'fields': 'spend,impressions,clicks,conversions,ctr,cpc,conversion_rate',
            'date_preset': 'yesterday',  # Get yesterday's data
            'level': 'campaign'
        }
        
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        
        if 'data' in data and len(data['data']) > 0:
            metrics = data['data'][0]
            
            # Calculate derived metrics
            spend = float(metrics.get('spend', 0))
            impressions = int(metrics.get('impressions', 0))
            clicks = int(metrics.get('clicks', 0))
            conversions = int(metrics.get('conversions', 0))
            ctr = float(metrics.get('ctr', 0)) / 100  # Convert percentage to decimal
            cpc = float(metrics.get('cpc', 0))
            
            # Calculate CPA and ROAS
            cpa = spend / conversions if conversions > 0 else 0
            roas = 0  # ROAS calculation would need revenue data
            
            return {
                'spend': spend,
                'impressions': impressions,
                'clicks': clicks,
                'conversions': conversions,
                'ctr': ctr,
                'cpa': cpa,
                'roas': roas
            }
        else:
            logger.warning(f"No data returned for Facebook campaign {campaign_id}")
            return {}
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Facebook API request failed for campaign {campaign_id}: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"Facebook API error for campaign {campaign_id}: {str(e)}")
        return {}


def _fetch_tiktok_metrics(campaign_id: str) -> Dict[str, float]:
    """
    Fetch metrics from TikTok Ads API
    
    Args:
        campaign_id: TikTok campaign ID
        
    Returns:
        dict: Campaign metrics
    """
    try:
        access_token = getattr(settings, 'TIKTOK_ACCESS_TOKEN', None)
        advertiser_id = getattr(settings, 'TIKTOK_ADVERTISER_ID', None)
        
        if not access_token or not advertiser_id:
            logger.error("TikTok access token or advertiser ID not configured")
            return {}
        
        # TikTok Ads API endpoint
        url = "https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/"
        
        headers = {
            'Access-Token': access_token,
            'Content-Type': 'application/json'
        }
        
        data = {
            'advertiser_id': advertiser_id,
            'service_type': 'AUCTION',
            'report_type': 'BASIC',
            'data_level': 'CAMPAIGN',
            'dimensions': ['campaign_id'],
            'metrics': ['spend', 'impressions', 'clicks', 'conversions', 'ctr', 'cpc', 'conversion_rate'],
            'start_date': (timezone.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
            'end_date': (timezone.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
            'filters': [{
                'field_name': 'campaign_id',
                'filter_type': 'IN',
                'filter_value': [campaign_id]
            }]
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        
        if 'data' in result and 'list' in result['data'] and len(result['data']['list']) > 0:
            metrics = result['data']['list'][0]['metrics']
            
            spend = float(metrics.get('spend', 0))
            impressions = int(metrics.get('impressions', 0))
            clicks = int(metrics.get('clicks', 0))
            conversions = int(metrics.get('conversions', 0))
            ctr = float(metrics.get('ctr', 0)) / 100  # Convert percentage to decimal
            cpc = float(metrics.get('cpc', 0))
            
            # Calculate CPA and ROAS
            cpa = spend / conversions if conversions > 0 else 0
            roas = 0  # ROAS calculation would need revenue data
            
            return {
                'spend': spend,
                'impressions': impressions,
                'clicks': clicks,
                'conversions': conversions,
                'ctr': ctr,
                'cpa': cpa,
                'roas': roas
            }
        else:
            logger.warning(f"No data returned for TikTok campaign {campaign_id}")
            return {}
            
    except requests.exceptions.RequestException as e:
        logger.error(f"TikTok API request failed for campaign {campaign_id}: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"TikTok API error for campaign {campaign_id}: {str(e)}")
        return {}


def _fetch_instagram_metrics(campaign_id: str) -> Dict[str, float]:
    """
    Fetch metrics from Instagram Ads API (via Facebook)
    
    Args:
        campaign_id: Instagram campaign ID
        
    Returns:
        dict: Campaign metrics
    """
    try:
        access_token = getattr(settings, 'FACEBOOK_ACCESS_TOKEN', None)
        if not access_token:
            logger.error("Facebook access token not configured for Instagram")
            return {}
        
        # Instagram ads are managed through Facebook Graph API
        url = f"https://graph.facebook.com/v18.0/{campaign_id}/insights"
        
        params = {
            'access_token': access_token,
            'fields': 'spend,impressions,clicks,conversions,ctr,cpc,conversion_rate',
            'date_preset': 'yesterday',
            'level': 'campaign',
            'platform': 'instagram'
        }
        
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        
        if 'data' in data and len(data['data']) > 0:
            metrics = data['data'][0]
            
            spend = float(metrics.get('spend', 0))
            impressions = int(metrics.get('impressions', 0))
            clicks = int(metrics.get('clicks', 0))
            conversions = int(metrics.get('conversions', 0))
            ctr = float(metrics.get('ctr', 0)) / 100
            cpc = float(metrics.get('cpc', 0))
            
            cpa = cpc if conversions == 0 else spend / conversions if conversions > 0 else 0
            roas = 0
            
            return {
                'spend': spend,
                'impressions': impressions,
                'clicks': clicks,
                'conversions': conversions,
                'ctr': ctr,
                'cpa': cpa,
                'roas': roas
            }
        else:
            logger.warning(f"No data returned for Instagram campaign {campaign_id}")
            return {}
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Instagram API request failed for campaign {campaign_id}: {str(e)}")
        return {}
    except Exception as e:
        logger.error(f"Instagram API error for campaign {campaign_id}: {str(e)}")
        return {}