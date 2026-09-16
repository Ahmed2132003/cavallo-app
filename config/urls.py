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
    path("api/v1/customers/", include("businesses.customer_urls")),
]