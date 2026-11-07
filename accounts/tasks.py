"""
Celery tasks for the accounts app (Task P2.4).
"""

import logging

from celery import shared_task

from accounts import services

logger = logging.getLogger(__name__)


@shared_task(name="accounts.send_password_reset_otp", ignore_result=True)
def send_password_reset_otp_task(email):
    """
    Look up the account, issue an OTP and email it, in the background.

    Takes the email only: no OTP or other secret ever goes through the
    broker. Any failure (Redis, SMTP, database) is logged by exception
    class name only (no OTP, no email) and swallowed. There is no
    autoretry on purpose: a retry could send the email twice.
    """
    try:
        services.request_password_reset_otp(email)
    except Exception as exc:
        logger.error("Password reset OTP task failed (%s).", type(exc).__name__)
