import os
from datetime import timedelta

from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend.settings')

app = Celery('backend', task_cls='core.celery_utils:TimeoutEnforcedTask')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Note: Beat schedule is configured in settings.py under CELERY_BEAT_SCHEDULE
# This includes notification tasks and other periodic tasks.


@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
