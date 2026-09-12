"""
Test-only settings (Part P-011).

Used exclusively by the pytest run (see pytest.ini's
DJANGO_SETTINGS_MODULE). Never referenced by manage.py, Docker Compose,
or any deployed environment.

Inherits everything from dev.py (same DB/Redis/etc. wiring as local
dev) and adds exactly one thing dev.py must never have: the throwaway
`core.tests.testapp` app, which exists only so P-011's (and future
parts') base-mixin tests have a real table to exercise. Because this
app has no migrations module, pytest-django creates its table directly
(equivalent to `migrate --run-syncdb`) in the ephemeral test database —
nothing here touches the real dev/staging/prod database or migrations
history.
"""

from .dev import *  # noqa: F401,F403

INSTALLED_APPS = INSTALLED_APPS + ["core.tests.testapp"]  # noqa: F405