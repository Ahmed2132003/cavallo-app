"""
URL routes for the accounts app (Parts P-017, P-018).

Included under /api/v1/auth/ by config/urls.py.
"""

from django.urls import path

from accounts.views import LoginView, LogoutView, RefreshView, RegisterView

app_name = "accounts"

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    # Part P-018:
    path("login/", LoginView.as_view(), name="login"),
    path("refresh/", RefreshView.as_view(), name="refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
]
