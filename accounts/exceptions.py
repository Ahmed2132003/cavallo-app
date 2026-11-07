"""
API exceptions for the accounts app (Task P2.4).
"""

from rest_framework import status
from rest_framework.exceptions import APIException


class PasswordResetInvalid(APIException):
    """
    The one failure answer for the password reset verify and confirm
    endpoints. Same status and message whatever the real reason was, so
    the response never says whether the account, OTP or token was wrong.
    The core exception handler shapes it into the standard envelope.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Invalid or expired verification data."
    default_code = "password_reset_invalid"
