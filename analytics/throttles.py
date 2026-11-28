from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class _SettingsRateMixin:
    """
    Reads the rate from a Django setting at call time (falls back to a
    default), so no REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] edit is needed.
    The client IP is used only transiently as a cache key by DRF's
    throttle; it is never written to the database.
    """

    settings_name = ""
    default_rate = "60/min"

    def get_rate(self):
        return getattr(settings, self.settings_name, None) or self.default_rate


class ViewEventAnonThrottle(_SettingsRateMixin, AnonRateThrottle):
    scope = "view_event_anon"
    settings_name = "ANALYTICS_VIEW_EVENT_ANON_RATE"
    default_rate = "60/min"


class ViewEventUserThrottle(_SettingsRateMixin, UserRateThrottle):
    scope = "view_event_user"
    settings_name = "ANALYTICS_VIEW_EVENT_USER_RATE"
    default_rate = "120/min"
