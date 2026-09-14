import logging

from celery import shared_task
from django.contrib.postgres.search import SearchVector

logger = logging.getLogger(__name__)

@shared_task(bind=True, ignore_result=True)
def update_meeting_search_vector(self, meeting_id: int) -> None:
    """Update the search_vector field for a meeting after transcript is saved"""
    from meetings.models import Meeting

    updated = Meeting.objects.filter(pk=meeting_id).update(
        search_vector=SearchVector("title", weight="A")
        + SearchVector("summary", weight="B")
        + SearchVector("transcript", weight="C"),
    )

    if not updated:
        logger.warning("update_meeting_search_vector: meeting_id=%s not found", meeting_id)
    else:
        logger.info("update_meeting_search_vector: updated meeting_id=%s", meeting_id)