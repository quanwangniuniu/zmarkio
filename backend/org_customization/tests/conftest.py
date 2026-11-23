import pytest
from django.core.cache import cache
from django.test import override_settings


@pytest.fixture(autouse=True)
def isolated_cache():
    with override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}):
        cache.clear()
        yield
