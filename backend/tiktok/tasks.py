import os
import logging
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from utils.tasks import scan_file_for_virus_generic
from .models import PublicPreview

logger = logging.getLogger(__name__)


@shared_task(bind=True)
def scan_tiktok_creative_for_virus(self, creative_id):
    """Scan TikTok creative file for viruses using generic scanner."""
    return scan_file_for_virus_generic.delay(
        model_path='tiktok.models.TikTokCreative',
        file_id=creative_id,
        file_path_key='storage_path',
        status_field='scan_status'
    )


@shared_task
def cleanup_expired_previews():
    """
    Clean up expired preview records from the database
    This task runs daily to remove expired preview records and their JSON data
    Scheduled to run at 02:00 UTC daily (low traffic period)
    """
    try:
        # Get current time
        now = timezone.now()
        
        # Count expired previews before deletion
        expired_count = PublicPreview.objects.filter(
            expires_at__lt=now
        ).count()
        
        if expired_count == 0:
            logger.info("No expired TikTok previews found for cleanup")
            return {
                'status': 'success',
                'message': 'No expired previews found',
                'deleted_count': 0
            }
        
        # Delete expired previews in a transaction
        with transaction.atomic():
            deleted_count, _ = PublicPreview.objects.filter(
                expires_at__lt=now
            ).delete()
        
        logger.info(f"Successfully cleaned up {deleted_count} expired TikTok preview records")
        
        return {
            'status': 'success',
            'message': f'Cleaned up {deleted_count} expired previews',
            'deleted_count': deleted_count
        }
        
    except Exception as e:
        logger.error(f"Error cleaning up expired TikTok previews: {str(e)}")
        return {
            'status': 'error',
            'message': f'Error cleaning up expired previews: {str(e)}',
            'deleted_count': 0
        }
