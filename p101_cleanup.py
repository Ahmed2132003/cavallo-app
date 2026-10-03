# p101_cleanup.py - removes ALL P-101 audit data. Run only when the audit is finished (Step 3).
import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from businesses.models import BusinessProfile
from categories.models import Category
from content.models import Post, Reel
from moderation.models import ModerationQueue
from products.models import Product
from social.models import Follow
from stories.models import Story

User = get_user_model()
ids = list(BusinessProfile.all_objects.filter(business_name__startswith="P101 ").values_list("id", flat=True))
for model in (Post, Reel, Story):
    ct = ContentType.objects.get_for_model(model)
    oids = list(model.all_objects.filter(business_id__in=ids).values_list("id", flat=True))
    ModerationQueue.objects.filter(content_type=ct, object_id__in=oids).delete()
Follow.objects.filter(business_id__in=ids).delete()
for model in (Story, Reel, Post, Product):
    model.all_objects.filter(business_id__in=ids).delete()
BusinessProfile.all_objects.filter(id__in=ids).delete()
User.objects.filter(username__startswith="p101_").delete()
Category.objects.filter(slug__startswith="p101-", parent__isnull=False).delete()
Category.objects.filter(slug__startswith="p101-").delete()
print("P101|CLEANUP=done")
