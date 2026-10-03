# p101_seed.py - PART P-101 STEP 1: realistic-volume audit data. Idempotent.
import os, random
from datetime import timedelta
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.search import SearchVector
from django.db import connection
from django.utils import timezone

from businesses.models import BusinessProfile
from categories.models import Category
from content.models import Post, Reel
from moderation.models import ModerationQueue
from products.models import Product
from social.models import Follow
from stories.models import Story

User = get_user_model()
random.seed(101)
N_BIZ, N_CUST = 300, 60
N_PRODUCTS, N_POSTS, N_REELS, N_STORIES, N_ACTIVE_STORIES = 6000, 6000, 3000, 3000, 150
ADJ = ["cotton", "leather", "premium", "wholesale", "classic", "modern", "handmade", "slim", "durable",
       "organic", "sport", "casual", "luxury", "kids", "winter", "summer", "steel", "wooden", "ceramic", "printed"]
NOUN = ["shirt", "shoes", "jacket", "bag", "sofa", "table", "watch", "dress", "pants", "scarf", "belt",
        "carpet", "lamp", "bottle", "toy", "cable", "charger", "fabric", "uniform", "packaging"]
CITIES = [("Egypt", "Cairo", 30), ("Egypt", "Giza", 12), ("Egypt", "Alexandria", 14), ("Egypt", "Ismailia", 5),
          ("Egypt", "Mansoura", 6), ("Saudi Arabia", "Riyadh", 12), ("Saudi Arabia", "Jeddah", 9), ("UAE", "Dubai", 12)]
PARENTS = ["Fashion", "Furniture", "Electronics", "Food", "Textiles", "Industrial"]
CHILDREN = ["Men", "Women", "Kids", "Wholesale"]


def text(n=3):
    return " ".join(f"{random.choice(ADJ)} {random.choice(NOUN)}" for _ in range(n))


def wpick(pairs):
    r, acc = random.random() * sum(w for _, w in pairs), 0
    for v, w in pairs:
        acc += w
        if r <= acc:
            return v
    return pairs[-1][0]


def counts():
    biz = BusinessProfile.all_objects.filter(business_name__startswith="P101 ")
    ids = biz.values("id")
    print(f"P101|businesses={biz.count()}")
    print(f"P101|products={Product.all_objects.filter(business__in=ids).count()}")
    print(f"P101|posts={Post.all_objects.filter(business__in=ids).count()}")
    print(f"P101|reels={Reel.all_objects.filter(business__in=ids).count()}")
    print(f"P101|stories={Story.all_objects.filter(business__in=ids).count()}")
    print(f"P101|follows={Follow.objects.filter(business__in=ids).count()}")
    print(f"P101|categories={Category.objects.filter(slug__startswith='p101-').count()}")
    print(f"P101|mq_total={ModerationQueue.objects.count()}")
    print(f"P101|mq_pending={ModerationQueue.objects.filter(status='pending').count()}")
    now = timezone.now()
    print("P101|stories_active=%d" % Story.all_objects.filter(
        business__in=ids, status="published", expires_at__gt=now, is_deleted=False).count())


def main():
    if User.objects.filter(username__startswith="p101_").exists():
        print("P101|SEED=already_present")
        counts()
        return
    now = timezone.now()
    pw = make_password("P101-audit-pass")
    parents = Category.objects.bulk_create(
        [Category(name=n, slug=f"p101-{n.lower()}", is_active=True) for n in PARENTS])
    kids = Category.objects.bulk_create(
        [Category(name=f"{p.name} {c}", slug=f"p101-{p.name.lower()}-{c.lower()}", parent=p, is_active=True)
         for p in parents for c in CHILDREN])
    cat_ids = [c.id for c in kids]

    users = User.objects.bulk_create(
        [User(username=f"p101_biz_{i}", email=f"p101_biz_{i}@audit.invalid", password=pw,
              account_type="business", is_business_verified=(i % 5 == 0)) for i in range(N_BIZ)] +
        [User(username=f"p101_cust_{i}", email=f"p101_cust_{i}@audit.invalid", password=pw,
              account_type="customer") for i in range(N_CUST)], batch_size=500)
    biz_users, cust_users = users[:N_BIZ], users[N_BIZ:]

    cities = [(c, ci) for c, ci, w in CITIES for _ in range(w)]
    bizs = []
    for i, u in enumerate(biz_users):
        country, city = random.choice(cities)
        bizs.append(BusinessProfile(
            user=u, business_name=f"P101 {text(1)} {i}", business_type=random.choice(["trader"] * 6 + ["factory"] * 4),
            country=country, city=city, description=text(4),
            category_id=(None if random.random() < 0.1 else random.choice(cat_ids)),
            is_featured=(random.random() < 0.1), follower_count=random.randint(0, 5000),
            average_rating=round(random.uniform(0, 5), 2), ratings_count=random.randint(0, 300)))
    bizs = BusinessProfile.objects.bulk_create(bizs, batch_size=500)
    biz_ids = [b.id for b in bizs]
    weights = [1 / (i + 1) ** 0.7 for i in range(len(biz_ids))]

    def rb():
        return random.choices(biz_ids, weights=weights)[0]

    Product.objects.bulk_create([
        Product(business_id=rb(), category_id=random.choice(cat_ids), name=text(2), description=text(5),
                price=round(random.uniform(5, 5000), 2), currency=wpick([("EGP", 70), ("SAR", 15), ("AED", 10), ("JOD", 5)]),
                is_active=(random.random() < 0.95), is_deleted=(random.random() < 0.02))
        for _ in range(N_PRODUCTS)], batch_size=1000)

    pst = [("published", 70), ("pending_review", 20), ("rejected", 10)]
    posts = Post.objects.bulk_create([
        Post(business_id=rb(), caption=text(6), status=wpick(pst), is_deleted=(random.random() < 0.03))
        for _ in range(N_POSTS)], batch_size=1000)
    reels = Reel.objects.bulk_create([
        Reel(business_id=rb(), caption=text(6), video="reels/videos/p101.mp4", duration_seconds=random.randint(5, 60),
             status=wpick(pst), processing_status=wpick([("ready", 95), ("failed", 3), ("processing", 2)]),
             is_deleted=(random.random() < 0.03))
        for _ in range(N_REELS)], batch_size=1000)

    stories = []
    for i in range(N_STORIES):
        if i < N_ACTIVE_STORIES:
            st = "published"; pub = now - timedelta(hours=random.randint(1, 20)); exp = pub + timedelta(hours=24)
        else:
            st = wpick([("published", 50), ("pending_review", 30), ("rejected", 20)])
            if st == "pending_review":
                pub = now; exp = now + timedelta(hours=24)
            else:
                pub = now - timedelta(hours=25 + random.randint(0, 240)); exp = pub + timedelta(hours=24)
        stories.append(Story(business_id=rb(), media="stories/media/p101.jpg", status=st,
                             published_at=pub, expires_at=exp, is_deleted=(random.random() < 0.02)))
    stories = Story.objects.bulk_create(stories, batch_size=1000)

    mq = []
    for model, objs, prio in ((Post, posts, "normal"), (Reel, reels, "normal"), (Story, stories, "fast_path")):
        ct = ContentType.objects.get_for_model(model)
        for o in objs:
            if model is Reel and o.processing_status != "ready":
                continue
            s = {"published": "approved", "pending_review": "pending", "rejected": "rejected"}[o.status]
            mq.append(ModerationQueue(content_type=ct, object_id=o.id, status=s, priority=prio))
    ModerationQueue.objects.bulk_create(mq, batch_size=1000)

    follows = set()
    for u in cust_users:
        for b in random.sample(biz_ids, random.randint(20, 30)):
            follows.add((u.id, b))
    Follow.objects.bulk_create([Follow(follower_id=u, business_id=b) for u, b in follows], batch_size=1000)

    Product.all_objects.filter(business_id__in=biz_ids).update(search_vector=SearchVector("name", "description"))
    BusinessProfile.all_objects.filter(id__in=biz_ids).update(search_vector=SearchVector("business_name", "description"))

    marker = "business_id IN (SELECT id FROM businesses_businessprofile WHERE business_name LIKE 'P101 %')"
    with connection.cursor() as cur:
        for model in (Product, Post, Reel, Story):
            t = model._meta.db_table
            cur.execute(f"UPDATE {t} SET created_at = now() - random() * interval '90 days' WHERE {marker}")
        for model in (Category, User, BusinessProfile, Product, Post, Reel, Story, Follow, ModerationQueue):
            cur.execute(f"ANALYZE {model._meta.db_table}")
    print("P101|SEED=created")
    counts()


main()
