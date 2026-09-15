import logging

from celery import shared_task
from django.contrib.postgres.search import SearchVector
from django.db.models import Value
from django.db.models.functions import Coalesce
from django_redis import get_redis_connection

logger = logging.getLogger(__name__)

@shared_task(bind=True, ignore_result=True)
def update_meeting_search_vector(self, meeting_id: int) -> None:
    """Update the search_vector field for a meeting after transcript is saved"""
    from meetings.models import Meeting

    redis = get_redis_connection("default")
    lock_key = f"lock:search_vector_update:{meeting_id}"

    with redis.lock(lock_key, timeout=30):
        updated = Meeting.objects.filter(pk=meeting_id).update(
            search_vector=SearchVector(Coalesce("title", Value("")), weight="A")
            + SearchVector(Coalesce("summary", Value("")), weight="B")
            + SearchVector(Coalesce("transcript", Value("")), weight="C"),
        )

    if not updated:
        logger.warning("update_meeting_search_vector: meeting_id=%s not found", meeting_id)
    else:
        logger.info("update_meeting_search_vector: updated meeting_id=%s", meeting_id)