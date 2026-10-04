"""
Tests for config.settings.prod (Part P-106).

prod.py cannot be imported into the pytest process (pytest runs against
config.settings.test and Django settings are process-global), so it is loaded
in a fresh subprocess with a controlled environment and the values are read
back as JSON.

The last test feeds those REAL prod values into Django's SecurityMiddleware
to prove the two settings that must work together do: SECURE_SSL_REDIRECT
(redirect plain HTTP) and SECURE_PROXY_SSL_HEADER (trust Nginx's
X-Forwarded-Proto). Without the second one, every proxied request would be
redirected to HTTPS forever.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import Client, override_settings

BASE_DIR = Path(__file__).resolve().parents[2]

_SETTING_NAMES = [
    "DEBUG",
    "ALLOWED_HOSTS",
    "SECURE_SSL_REDIRECT",
    "SESSION_COOKIE_SECURE",
    "CSRF_COOKIE_SECURE",
    "SECURE_PROXY_SSL_HEADER",
]

_PROBE = (
    "import json\n"
    "from django.conf import settings\n"
    f"names = {_SETTING_NAMES!r}\n"
    "print(json.dumps({n: getattr(settings, n, None) for n in names}))\n"
)


@pytest.fixture(scope="module")
def prod_settings():
    env = dict(os.environ)
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings.prod",
            "ALLOWED_HOSTS": "api.example.test",
            "SENTRY_DSN": "",
        }
    )
    env.setdefault("SECRET_KEY", "p106-test-only-secret-key")
    env.setdefault("DATABASE_URL", "postgres://u:p@db:5432/x")
    env.setdefault("REDIS_URL", "redis://redis:6379/0")

    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_debug_is_off(prod_settings):
    assert prod_settings["DEBUG"] is False


def test_allowed_hosts_come_from_the_environment(prod_settings):
    assert prod_settings["ALLOWED_HOSTS"] == ["api.example.test"]


def test_ssl_redirect_and_secure_cookies_are_active(prod_settings):
    assert prod_settings["SECURE_SSL_REDIRECT"] is True
    assert prod_settings["SESSION_COOKIE_SECURE"] is True
    assert prod_settings["CSRF_COOKIE_SECURE"] is True


def test_proxy_ssl_header_trusts_nginx_forwarded_proto(prod_settings):
    assert prod_settings["SECURE_PROXY_SSL_HEADER"] == [
        "HTTP_X_FORWARDED_PROTO",
        "https",
    ]


def test_no_redirect_loop_behind_the_tls_terminating_proxy(prod_settings):
    header = tuple(prod_settings["SECURE_PROXY_SSL_HEADER"])

    with override_settings(
        SECURE_SSL_REDIRECT=prod_settings["SECURE_SSL_REDIRECT"],
        SECURE_PROXY_SSL_HEADER=header,
        ALLOWED_HOSTS=["testserver"],
    ):
        client = Client()

        # Any path works: SecurityMiddleware decides before URL resolution.
        proxied = client.get("/p106-probe/", HTTP_X_FORWARDED_PROTO="https")
        direct = client.get("/p106-probe/")

    assert proxied.status_code != 301
    assert direct.status_code == 301
    assert direct["Location"].startswith("https://")
