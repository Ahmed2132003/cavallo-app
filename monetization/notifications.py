"""BE-08 stub. BE-11 replaces the body with real notifications."""

import logging

logger = logging.getLogger(__name__)


def notify_subscription_event(business, event):
    logger.info("Subscription event: business=%s event=%s", business.pk, event)