"""
Tests for core.pagination.StandardCursorPagination.

No feed-like endpoint exists yet to paginate end-to-end (that starts
with the Feed itself, later in the plan) — these tests confirm the
class is configured per architecture Section 9 point 7, and that it
actually paginates a real queryset of TimestampedModel rows correctly.
"""

import pytest
from rest_framework.pagination import CursorPagination
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from core.pagination import StandardCursorPagination
from core.tests.testapp.models import Widget


def test_is_a_cursor_pagination_subclass():
    assert issubclass(StandardCursorPagination, CursorPagination)


def test_configured_per_architecture_section_9():
    assert StandardCursorPagination.page_size == 20
    assert StandardCursorPagination.ordering == "-created_at"


@pytest.mark.django_db
def test_paginates_queryset_ordered_by_created_at_descending():
    widgets = [Widget.objects.create(name=f"w{i}") for i in range(3)]

    factory = APIRequestFactory()
    request = Request(factory.get("/widgets/"))

    paginator = StandardCursorPagination()
    page = paginator.paginate_queryset(Widget.objects.all(), request)

    # Most recently created first.
    assert [w.pk for w in page] == [w.pk for w in reversed(widgets)]
