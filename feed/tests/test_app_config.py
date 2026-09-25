"""
Part P-059 — feed app wiring tests.

The feed app is a pure query/aggregation layer over Post, Reel, Follow
and BusinessProfile. It must stay model-less: no table, no migrations.
"""

from django.apps import apps


def test_feed_app_is_installed():
    assert apps.is_installed("feed")


def test_feed_app_has_no_models():
    assert list(apps.get_app_config("feed").get_models()) == []
