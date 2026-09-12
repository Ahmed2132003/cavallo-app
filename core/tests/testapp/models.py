from django.db import models

from core.models import SoftDeleteModel, TimestampedModel


class Widget(TimestampedModel, SoftDeleteModel):
    """
    Minimal concrete model combining both mixins, for P-011's acceptance
    criteria: a real table, real rows, real manager/delete() behavior.
    Not part of production code — see this package's docstring.
    """

    name = models.CharField(max_length=50, default="widget")

    class Meta:
        app_label = "core_testapp"
