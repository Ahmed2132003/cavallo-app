"""
Regression tests for the P-096 fix in core.exceptions.

Django-native Http404 / PermissionDenied must come out of
custom_exception_handler in the standard P-012 envelope with the
NOT_FOUND / PERMISSION_DENIED codes, not the generic ERROR fallback.
"""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404

from core.exceptions import custom_exception_handler


def test_django_http404_maps_to_not_found_code():
    response = custom_exception_handler(Http404("No matching row."), {})

    assert response.status_code == 404
    assert response.data["error"]["code"] == "NOT_FOUND"
    assert response.data["error"]["fields"] == {}
    assert response.data["error"]["message"]


def test_django_permission_denied_maps_to_permission_denied_code():
    response = custom_exception_handler(DjangoPermissionDenied(), {})

    assert response.status_code == 403
    assert response.data["error"]["code"] == "PERMISSION_DENIED"
    assert response.data["error"]["fields"] == {}
    assert response.data["error"]["message"]
