"""
python manage.py send_test_email --to <address>

Sends one test email with the project's current email settings and prints
which settings it used. The SMTP password is never printed (only whether it
is set), and on failure only the exception class name is printed, because
SMTP error text can embed addresses or server replies.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

CONSOLE_BACKEND = "django.core.mail.backends.console.EmailBackend"


class Command(BaseCommand):
    help = "Send a test email using the current email settings."

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="Recipient address.")

    def handle(self, *args, **options):
        recipient = options["to"].strip()
        try:
            validate_email(recipient)
        except ValidationError:
            raise CommandError("--to is not a valid email address.") from None

        out = self.stdout.write
        out(f"backend:      {settings.EMAIL_BACKEND}")
        out(f"host:         {settings.EMAIL_HOST or '(empty)'}")
        out(f"port:         {settings.EMAIL_PORT}")
        out(f"use_tls:      {settings.EMAIL_USE_TLS}")
        out(f"use_ssl:      {settings.EMAIL_USE_SSL}")
        out(f"timeout:      {settings.EMAIL_TIMEOUT}")
        out(f"from:         {settings.DEFAULT_FROM_EMAIL}")
        out(f"user:         {settings.EMAIL_HOST_USER or '(empty)'}")
        out(f"password set: {'yes' if settings.EMAIL_HOST_PASSWORD else 'no'}")

        try:
            sent = send_mail(
                subject="Cavallo test email",
                message="This is a test email from the Cavallo backend.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=False,
            )
        except Exception as exc:
            raise CommandError(f"FAILED: {type(exc).__name__}") from None

        if sent != 1:
            raise CommandError("FAILED: the backend reported 0 emails sent.")
        if settings.EMAIL_BACKEND == CONSOLE_BACKEND:
            out(
                self.style.WARNING(
                    "OK, but the console backend only printed the email; nothing "
                    "was delivered. Set EMAIL_USE_REAL_SMTP=true to send for real."
                )
            )
        else:
            out(
                self.style.SUCCESS(f"OK: 1 email handed to the server for {recipient}.")
            )
