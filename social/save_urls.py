"""
URL routes for Part P-054's Save/Unsave + own-saves-list endpoints.

Own top-level prefix (api/v1/saves/), same shape as social/like_urls.py
— Save isn't business-scoped, it targets a generic content object
(Post/Reel/Product) identified in the request body (toggle) or scoped
to request.user (list), not a business id in the URL path.
"""

from django.urls import path

from social.views import SaveListView, SaveToggleView

app_name = "saves"

urlpatterns = [
    path("", SaveToggleView.as_view(), name="toggle"),
    path("me/", SaveListView.as_view(), name="list"),
]
