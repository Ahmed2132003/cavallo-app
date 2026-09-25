"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import include, path

from core.views import health_check

urlpatterns = [
    path("admin/", admin.site.urls),
    # Part P-015: unauthenticated liveness/readiness probe for load
    # balancers and uptime monitors. See core/views.py.
    path("health/", health_check),
    # Part P-017: first real API endpoint (registration). Part P-018
    # (login) and every later auth endpoint are added to
    # accounts/urls.py's own urlpatterns, not here.
    path("api/v1/auth/", include("accounts.urls")),
    # Part P-025: public, read-only, cached category tree. Only one
    # route exists inside categories/urls.py (GET tree/) — category
    # write access stays Admin-only via Django Admin ("admin/" above),
    # on purpose, per this part's explicit scope.
    path("api/v1/categories/", include("categories.urls")),
    # Part P-026: first genuinely IDOR-sensitive endpoints (architecture
    # Section 5, rule 10). /me/ on each resolves strictly from
    # request.user, never from a URL/body-supplied id — see
    # businesses/views.py's module docstring. businesses/{id}/ is the
    # one deliberately public, read-only exception.
    path("api/v1/businesses/", include("businesses.urls")),
    # Part P-052: Follow/Unfollow — a second, separate include under
    # the SAME api/v1/businesses/ prefix as the line above, since the
    # Follow model/view live in their own `social` app rather than
    # inside `businesses`. No pattern collision: businesses.urls only
    # declares "me/" and "<int:pk>/", never "<int:pk>/follow/".
    path("api/v1/businesses/", include("social.urls")),
    path("api/v1/customers/", include("businesses.customer_urls")),
    # Part P-032: second real application of P-026's IDOR-prevention
    # pattern, this time against a many-owned-resources relationship
    # (one BusinessProfile, many Products) — see products/views.py's
    # module docstring for the full singleton-vs-many-owned
    # distinction. products/{id}/ is the one endpoint here that
    # necessarily takes a URL-supplied id even for the owner's own
    # write path (PATCH/DELETE), unlike businesses/me/ above.
    path("api/v1/products/", include("products.urls")),
    # Part P-038: moderator queue API (list pending, approve, reject).
    # Every route is gated by HasCapability("can_moderate_content"); see
    # moderation/views.py.
    path("api/v1/moderation/", include("moderation.urls")),
    path("api/v1/posts/", include("content.urls")),
    # Part P-042: Reel — same content app, own urlconf module and own
    # prefix (see content/reel_urls.py for why this isn't folded into
    # content.urls above).
    path("api/v1/reels/", include("content.reel_urls")),
    # Part P-046: Story — its own top-level app (ADR-002), own urlconf
    # module and own prefix, same shape as reels above. Owner-only
    # create/list for now; no public endpoint yet (see stories/views.py
    # and this part's own "Out of Scope" note).
    path("api/v1/stories/", include("stories.urls")),
    # Part P-053: generic Like/Unlike — targets Post or Reel via
    # {"content_type": ..., "object_id": ...} in the request body, not
    # a URL-path id, since the target model varies. Own top-level
    # prefix (not folded into social.urls' /api/v1/businesses/ prefix)
    # because Like isn't business-scoped.
    path("api/v1/likes/", include("social.like_urls")),
    # Part P-054: Save/Unsave (idempotent) + own-saves list. Own
    # top-level prefix, same shape as likes above — targets Post,
    # Reel or Product via the request body, not a URL-path id.
    path("api/v1/saves/", include("social.save_urls")),
    # Part P-055: Comment create (list endpoint added in the same part).
    # Own top-level prefix, same shape as likes/saves above. Comments
    # are the one content type that is NOT moderated pre-publish.
    path("api/v1/comments/", include("social.comment_urls")),
    # Part P-056: Share tracking (append-only, deliberately
    # non-idempotent). Own top-level prefix, same shape as
    # likes/saves/comments above — targets Post or Reel via the body.
    path("api/v1/shares/", include("social.share_urls")),
    path("api/v1/reports/", include("reports.urls")),
    # Part P-059: Home Feed — own top-level prefix, same shape as
    # likes/saves/comments/shares above, since the feed isn't
    # business-scoped.
    path("api/v1/feed/", include("feed.urls")),
]