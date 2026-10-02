"""Admin tests for the payments app (P-089)."""

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from payments.models import Invoice, Subscription, Transaction

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("model", [Subscription, Transaction, Invoice])
def test_registered_and_read_only(model):
    assert model in admin.site._registry
    model_admin = admin.site._registry[model]
    request = RequestFactory().get("/")
    request.user = User(is_superuser=True, is_staff=True, is_active=True)
    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False
