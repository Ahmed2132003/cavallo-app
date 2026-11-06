"""
Fixtures shared by every test in accounts/tests (Task P2.5).

The password reset throttles keep their counters in the real Redis cache
(DB 1, the same DB that holds the pwreset:* OTP and token keys), and
cache.clear() would FLUSH that whole DB. So this fixture deletes only the
password reset throttle keys by pattern. django-redis adds its own key
prefix/version to the pattern itself.
"""

import pytest
from django.core.cache import cache

_PASSWORD_RESET_THROTTLE_PATTERN = "throttle_password_reset_*"


@pytest.fixture(autouse=True)
def _clean_password_reset_throttle_cache():
    # Counters outlive a test (and a test run) for up to an hour; start and
    # end every test with none, so tests never leak counters into each other.
    cache.delete_pattern(_PASSWORD_RESET_THROTTLE_PATTERN)
    yield
    cache.delete_pattern(_PASSWORD_RESET_THROTTLE_PATTERN)