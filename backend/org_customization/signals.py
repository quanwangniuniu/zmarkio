from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import Project

from .services import seed_task_config


@receiver(post_save, sender=Project)
def create_default_task_config(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        seed_task_config(instance)
