"""Dedicated throttle for report submission (Part P-057).

Architecture Section 10/28: report-flooding mitigation. This is its own DRF
scope ("report"), independent from the login throttle (P-018) and from any
general API throttle, so its limit can be tuned on its own.

TUNABLE PLACEHOLDER: ``DEFAULT_REPORT_THROTTLE_RATE`` is a starting value
pending real-world tuning (same precedent as P-039's SLA thresholds and
P-055's COMMENT_AUTO_HIDE_THRESHOLD). Override it without touching code by
defining ``REPORT_THROTTLE_RATE`` (e.g. "20/hour") in the Django settings.
"""

from django.conf import settings
from rest_framework.throttling import UserRateThrottle

DEFAULT_REPORT_THROTTLE_RATE = "10/hour"


class ReportRateThrottle(UserRateThrottle):
    scope = "report"

    def get_rate(self):
        # Read at request time (DRF builds a throttle instance per request),
        # so a settings override takes effect immediately, including in tests.
        return getattr(settings, "REPORT_THROTTLE_RATE", DEFAULT_REPORT_THROTTLE_RATE)
