"""
URL routes for the accounts app (Part P-017).

Included under /api/v1/auth/ by config/urls.py. Part P-018 (login) adds
its own path() entry to this same urlpatterns list.
"""

from django.urls import path

from accounts.views import RegisterView

app_name = "accounts"

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
]
