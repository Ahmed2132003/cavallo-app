"""
URL routes for Part P-056's Share tracking endpoint.

Mounted under /api/v1/shares/ by config/urls.py — own top-level
prefix, same shape as social/like_urls.py, social/save_urls.py and
social/comment_urls.py: Share isn't business-scoped, it targets a
generic content object (Post/Reel) identified in the request body.

Only POST exists. Share is an append-only tracking event (deliberately
non-idempotent, see social/models.py's Share docstring), so there is no
DELETE/unshare and no list endpoint in this part.
"""

from django.urls import path

from social.views import ShareCreateView

app_name = "shares"

urlpatterns = [
    path("", ShareCreateView.as_view(), name="create"),
]
