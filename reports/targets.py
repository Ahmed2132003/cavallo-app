"""Reportable targets for POST /api/v1/reports/ (Part P-057).

Report has its OWN explicit closed whitelist, deliberately separate from
Like's / Save's / Comment's / Share's whitelists, so a change to any of those
can never silently open or close what can be reported.

A target is reportable only if the reporter could legitimately have seen it:
published Posts/Reels, published and unexpired Stories, active Products,
non-hidden Comments, and Businesses. Anything else is a 404.
"""

from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from businesses.models import BusinessProfile
from content.models import Post, Reel
from products.models import Product
from social.models import Comment
from stories.models import Story


def _comments():
    return Comment.objects.filter(is_hidden=False)


def _posts():
    return Post.published_objects.all()


def _reels():
    return Reel.published_objects.all()


def _stories():
    return Story.objects.filter(
        status=Story.Status.PUBLISHED,
        expires_at__gt=timezone.now(),
    )


def _products():
    return Product.objects.filter(is_active=True)


def _businesses():
    return BusinessProfile.objects.all()


# key sent by the client -> callable returning the reportable queryset.
REPORT_TARGETS = {
    "comment": _comments,
    "post": _posts,
    "reel": _reels,
    "story": _stories,
    "product": _products,
    "business": _businesses,
}

REPORT_ALLOWED_CONTENT_TYPES = tuple(REPORT_TARGETS)


def resolve_report_target(content_type, object_id):
    """Return the target instance, or raise 400 (unknown type) / 404."""
    queryset_factory = REPORT_TARGETS.get(content_type)
    if queryset_factory is None:
        allowed = ", ".join(REPORT_ALLOWED_CONTENT_TYPES)
        raise ValidationError(
            {"content_type": [f"Unsupported content_type. Allowed: {allowed}."]}
        )
    target = queryset_factory().filter(pk=object_id).first()
    if target is None:
        raise NotFound("The reported content was not found.")
    return target
