from django.urls import path

from categories.views import CategoryTreeView

app_name = "categories"

urlpatterns = [
    path("tree/", CategoryTreeView.as_view(), name="category-tree"),
]
