# p101_postclean.py - run after p101_cleanup.py: drop audit-related cache keys and report what is left.
import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()
from django.contrib.auth import get_user_model
from django.core.cache import cache
from businesses.models import BusinessProfile
from categories.models import Category

cache.delete("categories:tree")
User = get_user_model()
print("P101|REMAINING|users={}|businesses={}|categories={}".format(
    User.objects.filter(username__startswith="p101_").count(),
    BusinessProfile.all_objects.filter(business_name__startswith="P101 ").count(),
    Category.objects.filter(slug__startswith="p101-").count()))
