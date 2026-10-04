"""
Static tests for the production Nginx configuration (Part P-106).

These only read the config files. The behavioural proof (a real WebSocket
handshake and a real HTTP request through a running Nginx) is the local
dry-run documented in the P-106 handoff.

The pitfall guarded here: plain HTTP proxying works WITHOUT the Upgrade /
Connection headers, so a config that forgets them looks fine until WebSocket
connections mysteriously fail in production.
"""

import re
from pathlib import Path

import pytest

NGINX_DIR = Path(__file__).resolve().parents[2] / "nginx"
SNIPPET = "snippets/cavallo_locations.conf"
SNIPPET_INCLUDE = "include /etc/nginx/snippets/cavallo_locations.conf;"


def _read(name):
    return " ".join((NGINX_DIR / name).read_text(encoding="utf-8").split())


def _location(text, prefix):
    pattern = r"location\s+" + re.escape(prefix) + r"\s*\{(.*?)\}"
    match = re.search(pattern, text)
    assert match, f"location {prefix} block not found"
    return match.group(1)


@pytest.fixture(scope="module")
def locations():
    return _read(SNIPPET)


def test_websocket_location_proxies_to_asgi_with_upgrade_headers(locations):
    block = _location(locations, "/ws/")

    assert "proxy_pass http://$cavallo_asgi;" in block
    assert "proxy_http_version 1.1;" in block
    assert "proxy_set_header Upgrade $http_upgrade;" in block
    assert 'proxy_set_header Connection "upgrade";' in block
    assert "proxy_set_header X-Forwarded-Proto $forwarded_proto;" in block
    assert "proxy_read_timeout 3600s;" in block


def test_http_location_proxies_to_gunicorn(locations):
    block = _location(locations, "/")

    assert "proxy_pass http://$cavallo_web;" in block
    assert "Upgrade" not in block
    assert "proxy_set_header X-Forwarded-Proto $forwarded_proto;" in block
    assert "proxy_set_header Host $host;" in block


def test_upload_limit_covers_the_100mb_reel_limit(locations):
    match = re.search(r"client_max_body_size (\d+)m;", locations)

    assert match, "client_max_body_size must be set in megabytes"
    assert int(match.group(1)) >= 100


def test_upstreams_are_resolved_per_request_not_at_startup(locations):
    """A static `upstream {}` block pins the container IP at Nginx startup;
    after a deploy recreates web/asgi Nginx would proxy to a dead IP (502)."""
    assert "resolver 127.0.0.11 valid=5s ipv6=off;" in locations
    assert "set $cavallo_web web:8000;" in locations
    assert "set $cavallo_asgi asgi:8001;" in locations
    assert "upstream cavallo" not in locations


@pytest.mark.parametrize("name", ["prod.conf", "local.conf"])
def test_both_configs_share_the_routing_snippet(name):
    text = _read(name)

    assert SNIPPET_INCLUDE in text
    assert "upstream cavallo" not in text
    assert "location /ws/" not in text, "WebSocket routing belongs in the snippet"


def test_prod_redirects_http_to_https_and_keeps_acme_path_open():
    text = _read("prod.conf")

    assert "location /.well-known/acme-challenge/ { root /var/www/certbot; }" in text
    assert "return 301 https://$host$request_uri;" in text


def test_prod_terminates_tls_with_modern_protocols_only():
    text = _read("prod.conf")

    assert "listen 443 ssl;" in text
    assert "ssl_certificate /etc/letsencrypt/live/cavallo/fullchain.pem;" in text
    assert "ssl_certificate_key /etc/letsencrypt/live/cavallo/privkey.pem;" in text
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in text
    assert "TLSv1.0" not in text
    assert "TLSv1.1" not in text


def test_prod_forwards_the_real_scheme():
    assert "set $forwarded_proto $scheme;" in _read("prod.conf")


def test_local_standin_is_non_tls_and_forces_forwarded_https():
    text = _read("local.conf")

    assert "listen 80;" in text
    assert "ssl_certificate" not in text
    assert "listen 443" not in text
    assert "set $forwarded_proto https;" in text
