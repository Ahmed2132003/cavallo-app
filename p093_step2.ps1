# p093_step2.ps1  --  run from D:\Cavallo\scd-backend
$ErrorActionPreference = 'Stop'

if (-not (Test-Path .\manage.py) -or -not (Test-Path .\analytics\tasks.py)) {
    throw 'Run this script from D:\Cavallo\scd-backend (manage.py / analytics\tasks.py not found).'
}

$utf8 = New-Object System.Text.UTF8Encoding($false)

function ConvertTo-Crlf([string]$s) {
    return ($s -replace "`r?`n", "`r`n")
}

function Edit-File {
    param(
        [string]$Path,
        [string]$Old,
        [string]$New,
        [string]$SkipIfContains
    )
    $full = Join-Path (Get-Location).Path $Path
    $text = [IO.File]::ReadAllText($full)
    if ($text.Contains($SkipIfContains)) {
        Write-Host "SKIP (already applied): $Path  [$SkipIfContains]"
        return
    }
    $oldC = ConvertTo-Crlf $Old
    $newC = ConvertTo-Crlf $New
    $count = ([regex]::Matches($text, [regex]::Escape($oldC))).Count
    if ($count -ne 1) {
        throw "Anchor found $count time(s) (expected exactly 1) in $Path :: $($Old.Substring(0, [Math]::Min(60, $Old.Length)))"
    }
    $text = $text.Replace($oldC, $newC)
    [IO.File]::WriteAllText($full, $text, $utf8)
    Write-Host "EDITED: $Path"
}

function Append-File {
    param([string]$Path, [string]$Content, [string]$SkipIfContains)
    $full = Join-Path (Get-Location).Path $Path
    $text = [IO.File]::ReadAllText($full)
    if ($text.Contains($SkipIfContains)) {
        Write-Host "SKIP (already applied): $Path  [$SkipIfContains]"
        return
    }
    $text = $text.TrimEnd("`r", "`n") + "`r`n`r`n`r`n" + (ConvertTo-Crlf $Content)
    [IO.File]::WriteAllText($full, $text, $utf8)
    Write-Host "APPENDED: $Path"
}

# =================================================================== tasks.py
Edit-File -Path 'analytics\tasks.py' -SkipIfContains 'new_ratings_count (P-093)' -Old @'
  - Product views / profile views are NOT tracked anywhere in this system,
    so they are NOT computed (documented gap, not a bug).
"""
'@ -New @'
  - new_ratings_count (P-093): ratings.Rating rows created on the date. A
    customer re-rating a business UPDATES their single row (unique per
    customer + business), so only first-time ratings are counted here,
    not later edits.
  - average_rating_snapshot (P-093): BusinessProfile.average_rating at the
    moment this task reaches that business. It is a stored POINT-IN-TIME
    SNAPSHOT (so a trend chart can show what the rating was on each past
    day), never a live join. Timing nuance: the Beat job runs at 00:15 UTC
    for the previous day, so the value used is the rating as of ~00:15 UTC
    the next day. Re-running an OLD date (backfill) stores TODAY'S average,
    not the historical one: the past cannot be reconstructed.
  - active_products_count / published_posts_count / published_reels_count
    (P-093): catalog totals at task-execution time (snapshots, not
    per-day deltas). Same backfill caveat as the rating snapshot.
  - Product views / profile views are NOT tracked anywhere in this system,
    so they are NOT computed (documented gap, not a bug).
"""
'@

Edit-File -Path 'analytics\tasks.py' -SkipIfContains 'from ratings.models import Rating' -Old @'
from content.models import Post, Reel
'@ -New @'
from content.models import Post, Reel
from products.models import Product
from ratings.models import Rating
'@

Edit-File -Path 'analytics\tasks.py' -SkipIfContains 'Return the nine metric values' -Old @'
    """Return the four metric values for one business on one date."""
'@ -New @'
    """
    Return the nine metric values for one business on one date: P-084's
    four engagement counts plus P-093's rating / catalog-growth fields
    (see the module docstring for the snapshot timing nuance).
    """
'@

Edit-File -Path 'analytics\tasks.py' -SkipIfContains '"new_ratings_count": Rating.objects' -Old @'
            story__business=business, created_at__date=target_date
        ).count(),
    }
'@ -New @'
            story__business=business, created_at__date=target_date
        ).count(),
        "new_ratings_count": Rating.objects.filter(
            business=business, created_at__date=target_date
        ).count(),
        "average_rating_snapshot": business.average_rating,
        "active_products_count": Product.objects.filter(
            business=business, is_active=True
        ).count(),
        "published_posts_count": Post.published_objects.filter(
            business=business
        ).count(),
        "published_reels_count": Reel.published_objects.filter(
            business=business
        ).count(),
    }
'@

# ============================================================ tests/test_tasks.py
Edit-File -Path 'analytics\tests\test_tasks.py' -SkipIfContains 'from decimal import Decimal' -Old @'
from datetime import timezone as dt_timezone
'@ -New @'
from datetime import timezone as dt_timezone
from decimal import Decimal
'@

Edit-File -Path 'analytics\tests\test_tasks.py' -SkipIfContains 'from ratings.models import Rating' -Old @'
from businesses.services import create_business_profile
from content.models import Post, Reel
from social.models import Comment, Follow, Like
'@ -New @'
from businesses.models import BusinessProfile
from businesses.services import create_business_profile
from categories.models import Category
from content.models import Post, Reel
from products.models import Product
from ratings.models import Rating
from social.models import Comment, Follow, Like
'@

Append-File -Path 'analytics\tests\test_tasks.py' -SkipIfContains 'class TestRatingAndCatalogMetrics' -Content @'
# ---------------------------------------------------------------------------
# Part P-093: rating + catalog-growth metrics
# ---------------------------------------------------------------------------


def _rate(business, when, score=5):
    obj = Rating.objects.create(customer=_make_user(), business=business, score=score)
    _backdate(obj, when)
    return obj


def _make_published_post(business):
    return Post.objects.create(business=business, caption="pub", status="published")


def _make_published_reel(business):
    reel = _make_reel(business)
    type(reel)._base_manager.filter(pk=reel.pk).update(
        status="published", processing_status="ready"
    )
    return reel


def _make_product(business, is_active=True):
    n = next(_seq)
    return Product.objects.create(
        business=business,
        category=Category.objects.create(name=f"P093 Category {n}"),
        name=f"P093 Product {n}",
        description="desc",
        price="10.00",
        currency=Product.CURRENCY_EGP,
        is_active=is_active,
    )


def _set_average_rating(business, value):
    BusinessProfile.objects.filter(pk=business.pk).update(average_rating=value)


def _p093(row):
    return (
        row.new_ratings_count,
        row.average_rating_snapshot,
        row.active_products_count,
        row.published_posts_count,
        row.published_reels_count,
    )


@pytest.mark.django_db
class TestRatingAndCatalogMetrics:
    def test_exact_values_for_known_fixture(self):
        # 3 ratings, 5 published posts, 2 published reels, 4 active products,
        # average rating 4.33.
        business = _make_business()
        noon = _at(DAY)
        for _ in range(3):
            _rate(business, noon)
        for _ in range(5):
            _make_published_post(business)
        for _ in range(2):
            _make_published_reel(business)
        for _ in range(4):
            _make_product(business)
        _set_average_rating(business, Decimal("4.33"))

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert _p093(row) == (3, Decimal("4.33"), 4, 5, 2)

    def test_new_ratings_day_boundaries_are_exact(self):
        business = _make_business()
        before = _at(DAY - timedelta(days=1), 23, 59, 59)  # excluded
        start = _at(DAY, 0, 0, 0)  # included
        end = _at(DAY, 23, 59, 59)  # included
        after = _at(DAY + timedelta(days=1), 0, 0, 0)  # excluded
        for when in (before, start, end, after):
            _rate(business, when)

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert row.new_ratings_count == 2

    def test_other_business_data_is_not_mixed_in(self):
        a, b = _make_business("A"), _make_business("B")
        noon = _at(DAY)
        _rate(a, noon)
        for _ in range(3):
            _rate(b, noon)
        _make_published_post(a)
        for _ in range(2):
            _make_published_post(b)
        _make_published_reel(b)
        _make_product(a)
        for _ in range(2):
            _make_product(b)
        _set_average_rating(a, Decimal("1.50"))
        _set_average_rating(b, Decimal("4.75"))

        compute_daily_stats(target_date=DAY)

        row_a = BusinessDailyStats.objects.get(business=a, date=DAY)
        row_b = BusinessDailyStats.objects.get(business=b, date=DAY)
        assert _p093(row_a) == (1, Decimal("1.50"), 1, 1, 0)
        assert _p093(row_b) == (3, Decimal("4.75"), 2, 2, 1)

    def test_only_published_active_and_not_deleted_catalog_is_counted(self):
        business = _make_business()
        _make_published_post(business)
        _make_post(business)  # pending_review: not counted
        gone = _make_published_post(business)
        gone.delete()  # soft delete: not counted
        _make_published_reel(business)
        _make_reel(business)  # pending_review: not counted
        _make_product(business)
        _make_product(business, is_active=False)  # hidden: not counted
        removed = _make_product(business)
        removed.delete()  # soft delete: not counted

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert row.published_posts_count == 1
        assert row.published_reels_count == 1
        assert row.active_products_count == 1

    def test_average_rating_snapshot_is_stored_not_live(self):
        business = _make_business()
        _set_average_rating(business, Decimal("4.00"))
        compute_daily_stats(target_date=DAY)

        # The live value changes afterwards; the stored row must not follow.
        _set_average_rating(business, Decimal("2.00"))
        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert row.average_rating_snapshot == Decimal("4.00")

        # Only a re-run of the task refreshes the snapshot.
        compute_daily_stats(target_date=DAY)
        row.refresh_from_db()
        assert row.average_rating_snapshot == Decimal("2.00")

    def test_unrated_empty_business_gets_zero_values(self):
        business = _make_business()

        compute_daily_stats(target_date=DAY)

        row = BusinessDailyStats.objects.get(business=business, date=DAY)
        assert _p093(row) == (0, Decimal("0.00"), 0, 0, 0)


@pytest.mark.django_db
class TestRatingAndCatalogIdempotency:
    def test_rerun_keeps_one_row_with_unchanged_p093_values(self):
        business = _make_business()
        for _ in range(3):
            _rate(business, _at(DAY))
        for _ in range(5):
            _make_published_post(business)
        _set_average_rating(business, Decimal("4.20"))

        compute_daily_stats(target_date=DAY)
        compute_daily_stats(target_date=DAY)

        rows = BusinessDailyStats.objects.filter(business=business, date=DAY)
        assert rows.count() == 1
        assert _p093(rows.get()) == (3, Decimal("4.20"), 0, 5, 0)

    def test_rerun_updates_the_row_including_new_fields(self):
        business = _make_business()
        _rate(business, _at(DAY))
        _make_published_post(business)
        compute_daily_stats(target_date=DAY)

        _rate(business, _at(DAY, 18))
        _make_published_post(business)
        _make_product(business)
        compute_daily_stats(target_date=DAY)

        rows = BusinessDailyStats.objects.filter(business=business, date=DAY)
        assert rows.count() == 1
        row = rows.get()
        assert row.new_ratings_count == 2
        assert row.published_posts_count == 2
        assert row.active_products_count == 1
'@

Write-Host ''
Write-Host 'P-093 STEP 2 script finished. Now run the verification commands.'