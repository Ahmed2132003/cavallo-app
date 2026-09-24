import pytest
from django.core.cache import cache

from reports.tests.helpers import make_business


@pytest.fixture(autouse=True)
def _clean_throttle_cache():
    # Throttle counters live in the real cache; never leak between tests.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def business(db):
    return make_business("owner")
