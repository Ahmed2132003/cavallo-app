# p093_step1.ps1  --  run from D:\Cavallo\scd-backend
$ErrorActionPreference = 'Stop'

if (-not (Test-Path .\manage.py) -or -not (Test-Path .\analytics\models.py)) {
    throw 'Run this script from D:\Cavallo\scd-backend (manage.py / analytics\models.py not found).'
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

function New-ProjectFile {
    param([string]$Path, [string]$Content)
    $full = Join-Path (Get-Location).Path $Path
    if (Test-Path $full) {
        Write-Host "SKIP (file exists): $Path"
        return
    }
    [IO.File]::WriteAllText($full, (ConvertTo-Crlf $Content), $utf8)
    Write-Host "CREATED: $Path"
}

# ---------------------------------------------------------------- models.py
Edit-File -Path 'analytics\models.py' -SkipIfContains 'Part P-093 additions' -Old @'
    DOCUMENTED GAP (not a bug)
'@ -New @'
    Part P-093 additions (additive migration 0002), all genuinely backed:
      - new_ratings_count        <- ratings.Rating rows CREATED that day.
                                    A customer re-rating a business updates
                                    their single row (unique per customer +
                                    business), so only first-time ratings
                                    are counted, not later edits.
      - average_rating_snapshot  <- BusinessProfile.average_rating copied at
                                    rollup time: a POINT-IN-TIME SNAPSHOT
                                    for trend lines, NOT a live value.
      - active_products_count /
        published_posts_count /
        published_reels_count    <- snapshots of the catalog totals at
                                    rollup time (catalog-growth chart).

    DOCUMENTED GAP (not a bug)
'@

Edit-File -Path 'analytics\models.py' -SkipIfContains 'new_ratings_count = models' -Old @'
    total_story_views = models.PositiveIntegerField(default=0)
'@ -New @'
    total_story_views = models.PositiveIntegerField(default=0)
    # Part P-093 (see class docstring).
    new_ratings_count = models.PositiveIntegerField(default=0)
    # Point-in-time snapshot, same precision as
    # BusinessProfile.average_rating (max_digits=3, decimal_places=2).
    average_rating_snapshot = models.DecimalField(
        max_digits=3, decimal_places=2, default=0
    )
    active_products_count = models.PositiveIntegerField(default=0)
    published_posts_count = models.PositiveIntegerField(default=0)
    published_reels_count = models.PositiveIntegerField(default=0)
'@

# ------------------------------------------------------------ migration 0002
New-ProjectFile -Path 'analytics\migrations\0002_businessdailystats_active_products_count_and_more.py' -Content @'
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessdailystats",
            name="new_ratings_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="businessdailystats",
            name="average_rating_snapshot",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=3),
        ),
        migrations.AddField(
            model_name="businessdailystats",
            name="active_products_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="businessdailystats",
            name="published_posts_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="businessdailystats",
            name="published_reels_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
'@

# ------------------------------------------------------------- serializers.py
Edit-File -Path 'analytics\serializers.py' -SkipIfContains 'P-093' -Old @'
    Exactly the four metrics this system genuinely tracks, plus the date.
'@ -New @'
    Exactly the metrics this system genuinely tracks (P-084's four plus
    P-093's five rating / catalog-growth fields), plus the date.
'@

Edit-File -Path 'analytics\serializers.py' -SkipIfContains '"new_ratings_count",' -Old @'
            "total_story_views",
        )
        read_only_fields = fields
'@ -New @'
            "total_story_views",
            "new_ratings_count",
            "average_rating_snapshot",
            "active_products_count",
            "published_posts_count",
            "published_reels_count",
        )
        read_only_fields = fields
'@

# ------------------------------------------------------ tests/test_models.py
Edit-File -Path 'analytics\tests\test_models.py' -SkipIfContains 'from decimal import Decimal' -Old @'
import pytest
from django.contrib.auth import get_user_model
'@ -New @'
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
'@

Edit-File -Path 'analytics\tests\test_models.py' -SkipIfContains 'assert row.new_ratings_count == 0' -Old @'
        assert row.total_story_views == 0
'@ -New @'
        assert row.total_story_views == 0
        assert row.new_ratings_count == 0
        assert row.average_rating_snapshot == 0
        assert row.active_products_count == 0
        assert row.published_posts_count == 0
        assert row.published_reels_count == 0
'@

Edit-File -Path 'analytics\tests\test_models.py' -SkipIfContains 'def test_p093_fields_store_values' -Old @'
    def test_no_fields_for_untracked_metrics(self):
'@ -New @'
    def test_p093_fields_store_values(self):
        row = BusinessDailyStats.objects.create(
            business=_make_business(),
            date="2026-01-01",
            new_ratings_count=3,
            average_rating_snapshot=Decimal("4.25"),
            active_products_count=7,
            published_posts_count=5,
            published_reels_count=2,
        )
        row.refresh_from_db()
        assert row.new_ratings_count == 3
        assert row.average_rating_snapshot == Decimal("4.25")
        assert row.active_products_count == 7
        assert row.published_posts_count == 5
        assert row.published_reels_count == 2

    def test_no_fields_for_untracked_metrics(self):
'@

Edit-File -Path 'analytics\tests\test_models.py' -SkipIfContains '"published_reels_count",' -Old @'
            "total_story_views",
        }
'@ -New @'
            "total_story_views",
            "new_ratings_count",
            "average_rating_snapshot",
            "active_products_count",
            "published_posts_count",
            "published_reels_count",
        }
'@

# --------------------------------------------------------- tests/test_api.py
Edit-File -Path 'analytics\tests\test_api.py' -SkipIfContains '"published_reels_count",' -Old @'
    "total_story_views",
}
'@ -New @'
    "total_story_views",
    "new_ratings_count",
    "average_rating_snapshot",
    "active_products_count",
    "published_posts_count",
    "published_reels_count",
}
'@

Edit-File -Path 'analytics\tests\test_api.py' -SkipIfContains '"published_reels_count": 0' -Old @'
                    "total_story_views": 1,
                }
'@ -New @'
                    "total_story_views": 1,
                    "new_ratings_count": 0,
                    "average_rating_snapshot": "0.00",
                    "active_products_count": 0,
                    "published_posts_count": 0,
                    "published_reels_count": 0,
                }
'@

Write-Host ''
Write-Host 'P-093 STEP 1 script finished. Now run the verification commands.'