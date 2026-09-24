"""
URL routes for Part P-055's Comment endpoints.

Mounted under /api/v1/comments/ by config/urls.py — own top-level
prefix, same shape as social/like_urls.py and social/save_urls.py:
Comment isn't business-scoped, it targets a generic content object
(Post/Reel) identified in the request body (create) or query string
(list).

One URL, two views: GET is the public list (CommentListView), POST is
the authenticated create (CommentCreateView). See
social/views.py's comment_collection_view for the dispatch (and why it
must be csrf_exempt).
"""

from django.urls import path

from social.views import comment_collection_view

app_name = "comments"

urlpatterns = [
    path("", comment_collection_view, name="collection"),
]
