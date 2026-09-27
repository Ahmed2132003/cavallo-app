"""
Custom Channels middleware (Part P-067) — authenticates WebSocket
connections using the same JWT access tokens djangorestframework-simplejwt
already issues for HTTP requests (Part P-018).

Distinct from Django's HTTP middleware concept: this is a Channels ASGI
middleware (BaseMiddleware), wrapping (scope, receive, send), not
(request) -> response.

WebSocket connections don't carry a standard Authorization header the
way REST requests do (browsers' WebSocket API has no way to set
arbitrary headers on the handshake request), so this project uses the
query-string convention instead:

    ws://<host>/ws/conversations/<conversation_id>/?token=<access_token>

This exact convention — query param name "token", raw access-token
value, no "Bearer " prefix — is a LOCKED CONTRACT for Part P-073
(Flutter's WebSocket connection manager). Do not change the param name
or the token format here without updating that part too.

This middleware only resolves scope["user"] (to a real User, or
AnonymousUser on anything missing/invalid/expired) — it never rejects
the connection itself. ChatConsumer.connect() is the one that inspects
scope["user"] and decides whether to accept or close, the same
identity-resolution/rule-enforcement split used everywhere else in this
project (e.g. DRF's authentication classes resolve request.user;
permission classes/view logic decide access).
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken


@database_sync_to_async
def _get_user_from_token(token):
    """
    Validate a raw access-token string and resolve it to a User.

    Uses AccessToken(...) directly — simplejwt's own token validation
    (signature, expiry, token_type == "access") — rather than DRF's
    request-based JWTAuthentication, since there is no HttpRequest here
    to hand it. Wrapped in database_sync_to_async because resolving the
    token's user_id to a real User row is a synchronous ORM call, and
    this middleware runs inside Channels' async stack.

    Returns AnonymousUser on ANY failure (bad signature, expired token,
    wrong token_type, unknown user_id) — never raises, so a bad token
    always falls through to ChatConsumer.connect()'s own
    is_authenticated check rather than crashing the ASGI stack.
    """
    User = get_user_model()
    try:
        validated_token = AccessToken(token)
    except TokenError:
        return AnonymousUser()

    user_id = validated_token.get("user_id")
    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist:
        return AnonymousUser()


class JWTAuthMiddleware(BaseMiddleware):
    """
    Reads ?token=<access_token> from the WebSocket connection's query
    string and sets scope["user"] accordingly (AnonymousUser if the
    "token" param is missing or invalid).
    """

    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)
        token_values = query_params.get("token")
        token = token_values[0] if token_values else None

        scope["user"] = await _get_user_from_token(token) if token else AnonymousUser()

        return await super().__call__(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    """
    Convenience wrapper matching Channels' own naming convention (e.g.
    AuthMiddlewareStack) so config/asgi.py imports one callable rather
    than instantiating JWTAuthMiddleware directly.
    """
    return JWTAuthMiddleware(inner)
