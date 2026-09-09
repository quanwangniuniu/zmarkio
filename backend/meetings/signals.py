from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender="meetings.Meeting")
def update_search_vector_on_save(sender, instance, **kwargs):
    """Keep search_vector in sync whenever a Meeting is created or updated."""
    from meetings.tasks import update_meeting_search_vector
    update_meeting_search_vector.delay(instance.pk)  # type: ignore[operator]
