import os
from . import settings as _base_settings
from .settings import *

# settings.py sets __all__ = ('celery_app',), so the star import above brings in
# nothing else. Copy every setting explicitly so CI sees the real INSTALLED_APPS;
# without this, `makemigrations --check` and `migrate` here act on zero apps.
globals().update(
    {name: value for name, value in vars(_base_settings).items() if name.isupper()}
)

# Using postgresql
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB'),
        'USER': os.getenv('POSTGRES_USER'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('POSTGRES_PORT'),
        'OPTIONS': {
            'options': '-c search_path=public'
        },
    }
}

