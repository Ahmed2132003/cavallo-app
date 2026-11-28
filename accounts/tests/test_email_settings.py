"""
Email configuration tests: EMAIL_USE_REAL_SMTP, the DEFAULT_FROM_EMAIL
fallback, TLS/SSL exclusivity and the send_test_email command.

Settings are evaluated in a fresh interpreter (subprocess) because they are
read once at import time.
"""

import json
import os
import smtplib
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError

ROOT = Path(__file__).resolve().parents[2]
SMTP = "django.core.mail.backends.smtp.EmailBackend"
CONSOLE = "django.core.mail.backends.console.EmailBackend"

_BLANK = {
    "EMAIL_USE_REAL_SMTP": "",
    "EMAIL_HOST_USER": "",
    "EMAIL_HOST_PASSWORD": "",
    "EMAIL_USE_TLS": "",
    "EMAIL_USE_SSL": "",
    "DEFAULT_FROM_EMAIL": "",
}
_CODE = (
    "import json, django; from django.conf import settings as s; "
    "print(json.dumps({k: getattr(s, k) for k in "
    "('EMAIL_BACKEND','DEFAULT_FROM_EMAIL','EMAIL_USE_TLS','EMAIL_USE_SSL')}))"
)


def _load(module="config.settings.dev", **overrides):
    env = {**os.environ, **_BLANK, **overrides, "DJANGO_SETTINGS_MODULE": module}
    return subprocess.run(
        [sys.executable, "-c", _CODE],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _ok(**kw):
    result = _load(**kw)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_real_smtp_true_uses_smtp_backend():
    assert _ok(EMAIL_USE_REAL_SMTP="true")["EMAIL_BACKEND"] == SMTP


def test_real_smtp_false_or_unset_uses_console_backend():
    assert _ok(EMAIL_USE_REAL_SMTP="false")["EMAIL_BACKEND"] == CONSOLE
    assert _ok()["EMAIL_BACKEND"] == CONSOLE


def test_test_settings_still_load_with_console_default():
    assert _ok(module="config.settings.test")["EMAIL_BACKEND"] == CONSOLE


def test_default_from_email_falls_back_to_host_user():
    cfg = _ok(EMAIL_HOST_USER="me@gmail.com")
    assert cfg["DEFAULT_FROM_EMAIL"] == "me@gmail.com"


def test_explicit_default_from_email_wins():
    cfg = _ok(EMAIL_HOST_USER="me@gmail.com", DEFAULT_FROM_EMAIL="x@example.com")
    assert cfg["DEFAULT_FROM_EMAIL"] == "x@example.com"


def test_default_from_email_placeholder_when_nothing_set():
    assert _ok()["DEFAULT_FROM_EMAIL"] == "noreply@localhost"


def test_tls_and_ssl_together_is_improperly_configured():
    result = _load(EMAIL_USE_TLS="true", EMAIL_USE_SSL="true")
    assert result.returncode != 0
    assert "ImproperlyConfigured" in result.stderr


def test_send_test_email_delivers_one_email_and_hides_password(settings):
    settings.EMAIL_HOST_PASSWORD = "s3cr3t-app-password"
    out = StringIO()

    call_command("send_test_email", "--to", "dest@example.com", stdout=out)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["dest@example.com"]
    text = out.getvalue()
    assert "s3cr3t-app-password" not in text
    assert "password set: yes" in text
    assert "backend:" in text and "port:" in text and "from:" in text


def test_send_test_email_failure_prints_only_exception_name():
    boom = smtplib.SMTPAuthenticationError(535, b"bad creds for leak@example.com")
    with mock.patch(
        "accounts.management.commands.send_test_email.send_mail", side_effect=boom
    ):
        with pytest.raises(CommandError) as excinfo:
            call_command("send_test_email", "--to", "dest@example.com")

    assert str(excinfo.value) == "FAILED: SMTPAuthenticationError"


def test_send_test_email_rejects_invalid_address():
    with pytest.raises(CommandError):
        call_command("send_test_email", "--to", "not-an-email")
