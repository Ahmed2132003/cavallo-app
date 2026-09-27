"""
ASGI config for config project.

Updated in Part P-067 for Django Channels: the "http" branch still
points at the plain Django ASGI application (unchanged, all existing
REST endpoints keep working exactly as before); a new "websocket"
branch routes to chat/routing.py's websocket_urlpatterns, wrapped in
chat.middleware.JWTAuthMiddlewareStack so scope["user"] is resolved
(from the ?token=<access_token> query param — see chat/middleware.py's
own docstring for the full convention) before ChatConsumer.connect()
ever runs.

get_asgi_application() is called, and its result bound to
django_asgi_app, BEFORE chat.routing / chat.middleware are imported.
Both of those (via chat.consumers -> chat.models) touch Django models,
which must not happen before Django's app registry is populated —
get_asgi_application() is what populates it. Importing them earlier
would raise AppRegistryNotReady.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
https://channels.readthedocs.io/en/latest/topics/routing.html
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from chat.middleware import JWTAuthMiddlewareStack  # noqa: E402
from chat.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)