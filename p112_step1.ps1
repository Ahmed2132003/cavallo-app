<#
 p112_step1.ps1  --  Part P-112, STEP 1 of 3 (first third)
 Scope: (A) read-only inventories of both repos
        (B) backend: User.preferred_language + GET/PATCH /api/v1/auth/me/
        (C) backend: Category name_en / name_ar + data migration + per-language cache
 Run from anywhere:
   powershell -ExecutionPolicy Bypass -File .\p112_step1.ps1
 Safe to re-run: every edit has a marker and is skipped when already applied.
 All anchors are verified BEFORE the first file is touched.
 Keep this file ASCII only (PowerShell 5.1 reads BOM-less scripts as ANSI).
#>
param(
    [string]$Backend = 'D:\Cavallo\scd-backend',
    [string]$Flutter = 'D:\Cavallo\social_commerce_app'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

if (-not (Test-Path (Join-Path $Backend 'manage.py'))) { throw "Backend root not found: $Backend" }
if (-not (Test-Path (Join-Path $Flutter 'pubspec.yaml'))) { throw "Flutter root not found: $Flutter" }
Start-Transcript -Path (Join-Path $Backend 'p112_step1_evidence.txt') -Force | Out-Null

function Read-Text([string]$p) { [System.IO.File]::ReadAllText($p) }
function Write-Text([string]$p, [string]$t) {
    $d = Split-Path $p -Parent
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
    [System.IO.File]::WriteAllText($p, $t, $Utf8NoBom)
}
function Get-Nl([string]$raw) { if ($raw.Contains("`r`n")) { "`r`n" } else { "`n" } }
function Convert-Nl([string]$t, [string]$nl) { ($t -replace "`r`n", "`n") -replace "`n", $nl }

# ------------------------------------------------------------------
# PART A - inventories (read-only)
# ------------------------------------------------------------------
Write-Host "`n=== PART A: inventories ===" -ForegroundColor Cyan

# --- A1 backend
$inv = New-Object System.Collections.Generic.List[string]
$inv.Add("P-112 STEP 1 - BACKEND INVENTORY ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))")
$inv.Add("")
$inv.Add("[1] Any existing use of Accept-Language / LocaleMiddleware / LANGUAGES / LOCALE_PATHS / gettext (must be empty or understood):")
$hits = Get-ChildItem $Backend -Recurse -Include *.py -File |
    Where-Object { $_.FullName -notmatch '\\(migrations|\.venv|venv|p101_backups)\\' } |
    Select-String -Pattern 'Accept-Language|HTTP_ACCEPT_LANGUAGE|LocaleMiddleware|LOCALE_PATHS|\bLANGUAGES\b|gettext|translation\.override'
if ($hits) { foreach ($h in $hits) { $inv.Add("  " + $h.Path.Substring($Backend.Length + 1) + ":" + $h.LineNumber + ": " + $h.Line.Trim()) } } else { $inv.Add("  (none)") }
$inv.Add("")
$inv.Add("[2] Latest migrations per touched app:")
foreach ($app in 'accounts', 'categories', 'devices', 'notifications') {
    $dir = Join-Path $Backend "$app\migrations"
    $names = Get-ChildItem $dir -Filter '0*.py' | Sort-Object Name | ForEach-Object { $_.BaseName }
    $inv.Add("  ${app}: " + ($names -join ', '))
}
$inv.Add("")
$inv.Add("[3] Consumers of Category.name / category name (these keep working: the name column is untouched):")
$hits = Get-ChildItem $Backend -Recurse -Include *.py -File |
    Where-Object { $_.FullName -notmatch '\\(migrations|tests|\.venv|venv|p101_backups)\\' -and $_.Name -notmatch '^p101_' } |
    Select-String -Pattern 'category\.name|category__name|Category\.objects|CATEGORY_TREE_CACHE_KEY|build_category_tree'
foreach ($h in $hits) { $inv.Add("  " + $h.Path.Substring($Backend.Length + 1) + ":" + $h.LineNumber + ": " + $h.Line.Trim()) }
$inv.Add("")
$inv.Add("[4] Notification dispatch (used by STEP 2): functions in notifications/services.py and tasks.py:")
foreach ($f in 'notifications\services.py', 'notifications\tasks.py') {
    $p = Join-Path $Backend $f
    if (Test-Path $p) { Select-String -Path $p -Pattern '^\s*def ' | ForEach-Object { $inv.Add("  ${f}:" + $_.LineNumber + ": " + $_.Line.Trim()) } }
}
$inv.Add("")
$inv.Add("[5] Device registration (used by STEP 2): devices/serializers.py + views.py fields:")
foreach ($f in 'devices\serializers.py', 'devices\views.py', 'devices\models.py') {
    $p = Join-Path $Backend $f
    if (Test-Path $p) { Select-String -Path $p -Pattern 'serializers\.\w+Field|models\.\w+Field|^class |def ' | ForEach-Object { $inv.Add("  ${f}:" + $_.LineNumber + ": " + $_.Line.Trim()) } }
}
$invBackend = Join-Path $Backend 'p112_step1_backend_inventory.txt'
Write-Text $invBackend (($inv -join "`r`n") + "`r`n")
Write-Host "Backend inventory written: $invBackend"

# --- A2 flutter
$lib = Join-Path $Flutter 'lib'
if (-not (Test-Path $lib)) { throw "Flutter lib/ not found under $Flutter" }
$files = Get-ChildItem $lib -Recurse -Filter *.dart -File | Where-Object { $_.FullName -notmatch '\.(g|freezed)\.dart$' -and $_.FullName -notmatch '\\l10n\\' }
function Get-Group($f) {
    $rel = $f.FullName.Substring($lib.Length + 1)
    $parts = $rel -split '\\'
    if ($parts[0] -eq 'features' -and $parts.Count -gt 2) { return 'features/' + $parts[1] }
    if ($parts.Count -gt 1) { return $parts[0] }
    return 'root'
}
$Q = '[''"]'
$literalPatterns = [ordered]@{
    'Text(literal)'      = 'Text\(\s*(const\s+)?' + $Q
    'Text(interpolated)' = 'Text\(\s*(const\s+)?[''"][^''"]*\$'
    'hintText'           = 'hintText:\s*' + $Q
    'labelText'          = 'labelText:\s*' + $Q
    'helper/errorText'   = '(helperText|errorText):\s*' + $Q
    'tooltip'            = 'tooltip:\s*' + $Q
    'SnackBar'           = 'SnackBar\('
    'semanticLabel'      = '(semanticLabel|semanticsLabel):\s*' + $Q
}
$rtlPatterns = [ordered]@{
    'EdgeInsets.only(l/r)'  = 'EdgeInsets\.only\([^)]*\b(left|right)\s*:'
    'EdgeInsets.fromLTRB'   = 'EdgeInsets\.fromLTRB'
    'Alignment L/R'         = 'Alignment\.(centerLeft|centerRight|topLeft|topRight|bottomLeft|bottomRight)'
    'TextAlign.left/right'  = 'TextAlign\.(left|right)'
    'Positioned(l/r)'       = 'Positioned\([^)]*\b(left|right)\s*:'
    'BorderRadius.only'     = 'BorderRadius\.only\('
    'directional icons'     = 'Icons\.(arrow_back|arrow_forward|arrow_back_ios|arrow_forward_ios|chevron_left|chevron_right|send|reply)\b'
}
$stats = @{}      # group -> pattern -> count
$perFile = @()    # detail rows
foreach ($f in $files) {
    $text = [System.IO.File]::ReadAllText($f.FullName)
    $g = Get-Group $f
    if (-not $stats.ContainsKey($g)) { $stats[$g] = @{} }
    $row = [ordered]@{ File = $f.FullName.Substring($lib.Length + 1); Group = $g; Literals = 0; Rtl = 0 }
    foreach ($k in $literalPatterns.Keys) {
        $c = [regex]::Matches($text, $literalPatterns[$k]).Count
        if (-not $stats[$g].ContainsKey($k)) { $stats[$g][$k] = 0 }
        $stats[$g][$k] += $c
        if ($k -ne 'Text(interpolated)' -and $k -ne 'SnackBar') { $row.Literals += $c }
    }
    foreach ($k in $rtlPatterns.Keys) {
        $c = [regex]::Matches($text, $rtlPatterns[$k]).Count
        if (-not $stats[$g].ContainsKey($k)) { $stats[$g][$k] = 0 }
        $stats[$g][$k] += $c
        if ($k -ne 'BorderRadius.only') { $row.Rtl += $c }
    }
    $perFile += New-Object psobject -Property $row
}
$fl = New-Object System.Collections.Generic.List[string]
$fl.Add("P-112 STEP 1 - FLUTTER INVENTORY of user-facing literals ($(Get-Date -Format 'yyyy-MM-dd HH:mm'))")
$fl.Add("Files scanned: $($files.Count) (lib/, excluding *.g.dart, *.freezed.dart, l10n/)")
$fl.Add("Note: Text(literal) already includes dialog titles/buttons/app bars written as Text('...'). Text(interpolated) is a subset that needs ICU placeholders/plurals.")
$fl.Add("")
$fl.Add("[1] Literal counts per group")
$cols = @($literalPatterns.Keys)
$fl.Add(("{0,-28}" -f 'group') + (($cols | ForEach-Object { "{0,18}" -f $_ }) -join ''))
$totals = @{}
foreach ($g in ($stats.Keys | Sort-Object)) {
    $line = "{0,-28}" -f $g
    foreach ($k in $cols) { $v = $stats[$g][$k]; $line += "{0,18}" -f $v; if (-not $totals.ContainsKey($k)) { $totals[$k] = 0 }; $totals[$k] += $v }
    $fl.Add($line)
}
$tl = "{0,-28}" -f 'TOTAL'
foreach ($k in $cols) { $tl += "{0,18}" -f $totals[$k] }
$fl.Add($tl)
$fl.Add("")
$fl.Add("[2] RTL-hygiene hits per group (left/right API usage that must become Directional)")
$rcols = @($rtlPatterns.Keys)
$fl.Add(("{0,-28}" -f 'group') + (($rcols | ForEach-Object { "{0,22}" -f $_ }) -join ''))
foreach ($g in ($stats.Keys | Sort-Object)) {
    $line = "{0,-28}" -f $g
    foreach ($k in $rcols) { $line += "{0,22}" -f $stats[$g][$k] }
    $fl.Add($line)
}
$fl.Add("")
$fl.Add("[3] Files in the STEP 8 scope (core/, features/auth, routing, root) with counts")
$scope = $perFile | Where-Object { $_.Group -in @('core', 'features/auth', 'routing', 'root') -and ($_.Literals -gt 0 -or $_.Rtl -gt 0) } | Sort-Object Group, File
foreach ($r in $scope) { $fl.Add(("  {0,-70} literals={1,-4} rtl={2}" -f $r.File, $r.Literals, $r.Rtl)) }
$fl.Add("")
$fl.Add("[4] Existing formatting / locale code (must be centralised in AppFormatters later)")
$fmt = $files | Select-String -Pattern 'DateFormat|NumberFormat|package:intl|Accept-Language|Locale\(|\blocale:|supportedLocales|localizationsDelegates|Directionality|TextDirection'
if ($fmt) { foreach ($h in $fmt) { $fl.Add("  " + $h.Path.Substring($lib.Length + 1) + ":" + $h.LineNumber + ": " + $h.Line.Trim()) } } else { $fl.Add("  (none)") }
$fl.Add("")
$fl.Add("[5] pubspec.yaml: flutter_localizations / intl / generate flag")
Select-String -Path (Join-Path $Flutter 'pubspec.yaml') -Pattern 'flutter_localizations|^\s*intl:|generate:|sdk: flutter' | ForEach-Object { $fl.Add("  pubspec.yaml:" + $_.LineNumber + ": " + $_.Line.Trim()) }
$invFlutter = Join-Path $Flutter 'p112_step1_flutter_inventory.txt'
Write-Text $invFlutter (($fl -join "`r`n") + "`r`n")
Write-Host "Flutter inventory written: $invFlutter"
Write-Host ("Flutter totals: Text(literal)={0}  hintText={1}  labelText={2}  tooltip={3}  SnackBar={4}" -f $totals['Text(literal)'], $totals['hintText'], $totals['labelText'], $totals['tooltip'], $totals['SnackBar'])

# ------------------------------------------------------------------
# PART B + C - backend changes
# ------------------------------------------------------------------
Write-Host "`n=== PART B/C: backend edits ===" -ForegroundColor Cyan

# --- resolve migration names (highest existing + 1)
function Get-LastMigration([string]$app) {
    $dir = Join-Path $Backend "$app\migrations"
    $last = Get-ChildItem $dir -Filter '0*.py' | Sort-Object Name | Select-Object -Last 1
    if (-not $last) { throw "No migrations found for $app" }
    return $last.BaseName
}
function Find-Existing([string]$app, [string]$suffix) {
    $dir = Join-Path $Backend "$app\migrations"
    $m = Get-ChildItem $dir -Filter "0*_$suffix.py" | Select-Object -First 1
    if ($m) { return $m.BaseName }
    return $null
}
function Next-Number([string]$lastName) { '{0:D4}' -f ([int]($lastName.Substring(0, 4)) + 1) }

$accMig = Find-Existing 'accounts' 'user_preferred_language'
if (-not $accMig) { $accDep = Get-LastMigration 'accounts'; $accMig = (Next-Number $accDep) + '_user_preferred_language' } else { $accDep = '(already created)' }

$catMig1 = Find-Existing 'categories' 'category_localized_names'
$catMig2 = Find-Existing 'categories' 'copy_name_to_name_en'
if (-not $catMig1) {
    $catDep = Get-LastMigration 'categories'
    $n1 = Next-Number $catDep
    $catMig1 = $n1 + '_category_localized_names'
    $catMig2 = (Next-Number $catMig1) + '_copy_name_to_name_en'
} else {
    $catDep = '(already created)'
    if (-not $catMig2) { throw "categories migration 1 exists but the data migration is missing; fix by hand" }
}
Write-Host "accounts migration   : $accMig (depends on $accDep)"
Write-Host "categories migration : $catMig1 (depends on $catDep) then $catMig2"

$tokens = @{
    '__ACC_MIG__' = $accMig; '__ACC_DEP__' = $accDep
    '__CAT_MIG1__' = $catMig1; '__CAT_DEP__' = $catDep
    '__CAT_MIG2__' = $catMig2; '__CAT_MIG1_NAME__' = $catMig1; '__CAT_MIG2_MODULE__' = $catMig2
}

# --- plan
$Edits = New-Object System.Collections.Generic.List[hashtable]
$Appends = New-Object System.Collections.Generic.List[hashtable]
$NewFiles = New-Object System.Collections.Generic.List[hashtable]
function Add-Edit([string]$Rel, [string]$Marker, [string]$Old, [string]$New) { $Edits.Add(@{ Rel = $Rel; Marker = $Marker; Old = $Old; New = $New }) }
function Add-Append([string]$Rel, [string]$Marker, [string]$Text) { $Appends.Add(@{ Rel = $Rel; Marker = $Marker; Text = $Text }) }
function Add-New([string]$Rel, [string]$Content) { $NewFiles.Add(@{ Rel = $Rel; Content = $Content }) }

$m = @'
LANGUAGE_CHOICES = [
'@
$o = @'
    following_count = models.PositiveIntegerField(default=0)
'@
$n = @'
    following_count = models.PositiveIntegerField(default=0)

    # Part P-112: language for the UI and for server-rendered text such
    # as push notifications. Default "ar" per the Phase 23 overview.
    # Exposed on GET/PATCH /api/v1/auth/me/.
    LANGUAGE_AR = "ar"
    LANGUAGE_EN = "en"
    LANGUAGE_CHOICES = [
        (LANGUAGE_AR, "Arabic"),
        (LANGUAGE_EN, "English"),
    ]
    preferred_language = models.CharField(
        max_length=2,
        choices=LANGUAGE_CHOICES,
        default=LANGUAGE_AR,
        help_text="UI and notification language (ar or en).",
    )
'@
Add-Edit 'accounts/models.py' $m $o $n

$m = @'
MePreferencesSerializer,
'@
$o = @'
from accounts.serializers import LoginSerializer, LogoutSerializer, RegisterSerializer
'@
$n = @'
from accounts.serializers import (
    LoginSerializer,
    LogoutSerializer,
    MePreferencesSerializer,
    RegisterSerializer,
)
'@
Add-Edit 'accounts/views.py' $m $o $n

$m = @'
def patch(self, request, *args, **kwargs):
        """
        PATCH /api/v1/auth/me/
'@
$o = @'
                "is_staff": user.is_staff,
            },
            status=status.HTTP_200_OK,
        )
'@
$n = @'
                "is_staff": user.is_staff,
                "preferred_language": user.preferred_language,
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request, *args, **kwargs):
        """
        PATCH /api/v1/auth/me/ (Part P-112)

        Lets the caller change their own preferences. Today that is only
        ``preferred_language`` (``ar`` or ``en``). Responds with the same
        shape as GET so the client can refresh its state in one call.
        """
        serializer = MePreferencesSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        user = request.user
        changed = []
        for field, value in serializer.validated_data.items():
            setattr(user, field, value)
            changed.append(field)
        if changed:
            user.save(update_fields=changed + ["updated_at"])

        return self.get(request, *args, **kwargs)
'@
Add-Edit 'accounts/views.py' $m $o $n

$m = @'
Language (Part P-112)
'@
$o = @'
    fieldsets = DjangoUserAdmin.fieldsets + (
'@
$n = @'
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Language (Part P-112)", {"fields": ("preferred_language",)}),
'@
Add-Edit 'accounts/admin.py' $m $o $n

$m = @'
"preferred_language"}
'@
$o = @'
            set(registered.keys()) | {"is_moderator", "is_staff"},
'@
$n = @'
            set(registered.keys()) | {"is_moderator", "is_staff", "preferred_language"},
'@
Add-Edit 'accounts/tests/test_me.py' $m $o $n

$m = @'
name_ar = models.CharField
'@
$o = @'
    name = models.CharField(max_length=100)
'@
$n = @'
    # ``name`` is kept as the English/fallback value so nothing that
    # already reads it breaks (Part P-112). ``name_en`` / ``name_ar`` are
    # the per-language values; the API picks one from Accept-Language.
    name = models.CharField(max_length=100)
    name_en = models.CharField(max_length=100, blank=True, default="")
    name_ar = models.CharField(max_length=100, blank=True, default="")
'@
Add-Edit 'categories/models.py' $m $o $n

$m = @'
def localized_name(self, language):
'@
$o = @'
    def save(self, *args, **kwargs):
        if not self.slug:
'@
$n = @'
    def localized_name(self, language):
        """Name in ``language`` (``ar`` / ``en``), falling back to English."""
        english = self.name_en or self.name
        if language == "ar":
            return self.name_ar or english
        return english

    def save(self, *args, **kwargs):
        # Part P-112: keep the legacy ``name`` column and ``name_en`` in
        # step when only one of them is provided.
        if not self.name_en and self.name:
            self.name_en = self.name
        elif not self.name and self.name_en:
            self.name = self.name_en
        if not self.slug:
'@
Add-Edit 'categories/models.py' $m $o $n

$m = @'
def localized_category_name(row, language):
'@
$o = @'
from categories.models import Category
'@
$n = @'
from categories.models import Category
from core.i18n import DEFAULT_LANGUAGE, LANGUAGE_AR


def localized_category_name(row, language):
    """Pick the display name from a ``.values()`` row (Part P-112).

    Arabic falls back to English, and English falls back to the legacy
    ``name`` column, so a category without translations still shows up.
    """
    english = row.get("name_en") or row["name"]
    if language == LANGUAGE_AR:
        return row.get("name_ar") or english
    return english
'@
Add-Edit 'categories/services.py' $m $o $n

$m = @'
def build_category_tree(language=DEFAULT_LANGUAGE):
'@
$o = @'
def build_category_tree():
'@
$n = @'
def build_category_tree(language=DEFAULT_LANGUAGE):
'@
Add-Edit 'categories/services.py' $m $o $n

$m = @'
"name_ar", "name_en", "slug"
'@
$o = @'
        .values("id", "name", "slug", "parent_id")
'@
$n = @'
        .values("id", "name", "name_ar", "name_en", "slug", "parent_id")
'@
Add-Edit 'categories/services.py' $m $o $n

$m = @'
localized_category_name(row, language),
'@
$o = @'
            "name": row["name"],
'@
$n = @'
            "name": localized_category_name(row, language),
'@
Add-Edit 'categories/services.py' $m $o $n

$m = @'
from core.i18n import
'@
$o = @'
from categories.services import build_category_tree
'@
$n = @'
from categories.services import build_category_tree
from core.i18n import get_request_language
'@
Add-Edit 'categories/views.py' $m $o $n

$m = @'
def category_tree_cache_key(language):
'@
$o = @'
CATEGORY_TREE_CACHE_TTL_SECONDS = 3600  # ~1h, per architecture Section 16
'@
$n = @'
CATEGORY_TREE_CACHE_TTL_SECONDS = 3600  # ~1h, per architecture Section 16

# Part P-112: one cache entry per language, so Arabic and English clients
# never receive each other's names. English keeps the original key.
CATEGORY_TREE_CACHE_KEYS = (
    CATEGORY_TREE_CACHE_KEY,
    f"{CATEGORY_TREE_CACHE_KEY}:ar",
)


def category_tree_cache_key(language):
    if language == "ar":
        return CATEGORY_TREE_CACHE_KEYS[1]
    return CATEGORY_TREE_CACHE_KEYS[0]
'@
Add-Edit 'categories/views.py' $m $o $n

$m = @'
language = get_request_language(request)
'@
$o = @'
        tree = cache_get_or_set(
            CATEGORY_TREE_CACHE_KEY,
            build_category_tree,
'@
$n = @'
        language = get_request_language(request)
        tree = cache_get_or_set(
            category_tree_cache_key(language),
            lambda: build_category_tree(language),
'@
Add-Edit 'categories/views.py' $m $o $n

$m = @'
response["Vary"] = "Accept-Language"
'@
$o = @'
        return Response(tree)
'@
$n = @'
        response = Response(tree)
        response["Vary"] = "Accept-Language"
        return response
'@
Add-Edit 'categories/views.py' $m $o $n

$m = @'
CATEGORY_TREE_CACHE_KEYS
'@
$o = @'
from categories.views import CATEGORY_TREE_CACHE_KEY
'@
$n = @'
from categories.views import CATEGORY_TREE_CACHE_KEYS
'@
Add-Edit 'categories/signals.py' $m $o $n

$m = @'
cache.delete_many(CATEGORY_TREE_CACHE_KEYS)
'@
$o = @'
    cache.delete(CATEGORY_TREE_CACHE_KEY)
'@
$n = @'
    cache.delete_many(CATEGORY_TREE_CACHE_KEYS)
'@
Add-Edit 'categories/signals.py' $m $o $n

$m = @'
from django import forms
'@
$o = @'
from django.contrib import admin
'@
$n = @'
from django import forms
from django.contrib import admin
'@
Add-Edit 'categories/admin.py' $m $o $n

$m = @'
class CategoryAdminForm(forms.ModelForm):
'@
$o = @'
@admin.register(Category)
'@
$n = @'
class CategoryAdminForm(forms.ModelForm):
    """Lets staff enter the English and Arabic names (Part P-112).

    ``name`` stays the English/fallback column, so it is no longer
    required on its own: when ``name_en`` is given, ``name`` follows it.
    """

    class Meta:
        model = Category
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].required = False
        self.fields["name"].help_text = "English / fallback name (follows English)."

    def clean(self):
        cleaned = super().clean()
        name = (cleaned.get("name") or "").strip()
        name_en = (cleaned.get("name_en") or "").strip()
        english = name_en or name
        if not english:
            raise forms.ValidationError("Enter at least the English name.")
        cleaned["name"] = english
        cleaned["name_en"] = english
        return cleaned


@admin.register(Category)
'@
Add-Edit 'categories/admin.py' $m $o $n

$m = @'
form = CategoryAdminForm
'@
$o = @'
    list_display = ("name", "parent", "is_active", "slug", "created_at")
'@
$n = @'
    form = CategoryAdminForm
    list_display = ("name_en", "name_ar", "parent", "is_active", "slug", "created_at")
'@
Add-Edit 'categories/admin.py' $m $o $n

$m = @'
("name", "name_ar", "name_en", "slug")
'@
$o = @'
    search_fields = ("name", "slug")
'@
$n = @'
    search_fields = ("name", "name_ar", "name_en", "slug")
'@
Add-Edit 'categories/admin.py' $m $o $n

$m = @'
class MePreferencesSerializer
'@
$t = @'
class MePreferencesSerializer(serializers.Serializer):
    """
    Validates PATCH /api/v1/auth/me/ (Part P-112).

    Only the fields a user may change about themselves live here. Role
    flags such as ``is_staff`` or ``account_type`` are deliberately not
    declared, so a forged value in the request body is ignored.
    """

    preferred_language = serializers.ChoiceField(
        choices=[value for value, _label in User.LANGUAGE_CHOICES],
        required=False,
    )
'@
Add-Append 'accounts/serializers.py' $m $t

$c = @'
"""
Language resolution helpers (Part P-112).

The mobile client sends ``Accept-Language`` on every request (Dio
interceptor, Flutter side). Server code that must return localized
content (category names today, anything else later) asks this module
which of the two supported languages to use instead of parsing the
header itself.

Rules, deliberately small and predictable:

* Only Arabic (``ar``) and English (``en``) are supported.
* Region subtags are ignored (``ar-EG`` -> ``ar``).
* Quality values are honoured, highest first; ``q=0`` means "not
  acceptable" and is skipped.
* Anything unsupported, missing or malformed falls back to English.
"""

LANGUAGE_AR = "ar"
LANGUAGE_EN = "en"
SUPPORTED_LANGUAGES = (LANGUAGE_AR, LANGUAGE_EN)
DEFAULT_LANGUAGE = LANGUAGE_EN


def parse_accept_language(header):
    """Return the primary language tags of ``header``, best first."""
    if not header:
        return []

    weighted = []
    for position, part in enumerate(str(header).split(",")):
        pieces = part.strip().split(";")
        tag = pieces[0].strip().lower()
        if not tag or tag == "*":
            continue

        quality = 1.0
        for param in pieces[1:]:
            name, _, value = param.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0

        if quality <= 0:
            continue
        weighted.append((-quality, position, tag.split("-")[0]))

    weighted.sort()
    return [tag for _quality, _position, tag in weighted]


def get_request_language(request, default=DEFAULT_LANGUAGE):
    """Pick ``ar`` or ``en`` for a DRF/Django request."""
    header = request.META.get("HTTP_ACCEPT_LANGUAGE", "")
    for tag in parse_accept_language(header):
        if tag in SUPPORTED_LANGUAGES:
            return tag
    return default
'@
Add-New 'core/i18n.py' $c

$c = @'
from django.test import RequestFactory

from core.i18n import get_request_language, parse_accept_language


def _request(header=None):
    extra = {}
    if header is not None:
        extra["HTTP_ACCEPT_LANGUAGE"] = header
    return RequestFactory().get("/", **extra)


def test_missing_header_falls_back_to_english():
    assert get_request_language(_request()) == "en"


def test_arabic_is_selected():
    assert get_request_language(_request("ar")) == "ar"


def test_region_subtag_is_ignored():
    assert get_request_language(_request("ar-EG")) == "ar"
    assert get_request_language(_request("en-US")) == "en"


def test_quality_values_decide_the_winner():
    assert get_request_language(_request("en;q=0.5, ar;q=0.9")) == "ar"
    assert get_request_language(_request("ar;q=0.2, en;q=0.8")) == "en"


def test_zero_quality_is_skipped():
    assert get_request_language(_request("ar;q=0, en")) == "en"


def test_unsupported_language_falls_back_to_english():
    assert get_request_language(_request("fr-FR, de;q=0.8")) == "en"


def test_wildcard_and_garbage_are_ignored():
    assert get_request_language(_request("*")) == "en"
    assert get_request_language(_request(";;;,,,")) == "en"
    assert get_request_language(_request("ar;q=abc")) == "en"


def test_first_supported_language_wins_when_unsupported_comes_first():
    assert get_request_language(_request("fr, ar;q=0.9, en;q=0.8")) == "ar"


def test_parse_returns_primary_tags_best_first():
    assert parse_accept_language("ar-EG,ar;q=0.9,en;q=0.8") == ["ar", "ar", "en"]
    assert parse_accept_language("") == []
    assert parse_accept_language(None) == []
'@
Add-New 'core/tests/test_i18n.py' $c

$c = @'
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "__ACC_DEP__"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="preferred_language",
            field=models.CharField(
                choices=[("ar", "Arabic"), ("en", "English")],
                default="ar",
                help_text="UI and notification language (ar or en).",
                max_length=2,
            ),
        ),
    ]
'@
Add-New 'accounts/migrations/__ACC_MIG__.py' $c

$c = @'
"""Tests for User.preferred_language and GET/PATCH /api/v1/auth/me/ (P-112)."""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()

pytestmark = pytest.mark.django_db

ME_URL = "/api/v1/auth/me/"


@pytest.fixture
def user():
    return User.objects.create_user(
        username="lang@example.com",
        email="lang@example.com",
        password="StrongPass123!",
    )


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


def test_new_user_defaults_to_arabic(user):
    assert user.preferred_language == "ar"


def test_me_get_exposes_preferred_language(client):
    response = client.get(ME_URL)

    assert response.status_code == 200
    assert response.json()["preferred_language"] == "ar"


def test_me_get_still_returns_the_original_fields(client, user):
    body = client.get(ME_URL).json()

    assert body["id"] == user.id
    assert body["email"] == "lang@example.com"
    assert body["account_type"] == "customer"
    assert body["is_moderator"] is False
    assert body["is_staff"] is False


def test_patch_changes_language_and_persists(client, user):
    response = client.patch(ME_URL, {"preferred_language": "en"}, format="json")

    assert response.status_code == 200
    assert response.json()["preferred_language"] == "en"
    user.refresh_from_db()
    assert user.preferred_language == "en"

    back = client.patch(ME_URL, {"preferred_language": "ar"}, format="json")
    assert back.status_code == 200
    user.refresh_from_db()
    assert user.preferred_language == "ar"


def test_patch_rejects_unsupported_language(client, user):
    response = client.patch(ME_URL, {"preferred_language": "fr"}, format="json")

    assert response.status_code == 400
    user.refresh_from_db()
    assert user.preferred_language == "ar"


def test_patch_ignores_fields_the_user_may_not_change(client, user):
    response = client.patch(
        ME_URL,
        {
            "preferred_language": "en",
            "is_staff": True,
            "is_moderator": True,
            "account_type": "business",
        },
        format="json",
    )

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.preferred_language == "en"
    assert user.is_staff is False
    assert user.is_moderator is False
    assert user.account_type == "customer"


def test_patch_with_empty_body_changes_nothing(client, user):
    response = client.patch(ME_URL, {}, format="json")

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.preferred_language == "ar"


def test_patch_requires_authentication():
    response = APIClient().patch(ME_URL, {"preferred_language": "en"}, format="json")

    assert response.status_code == 401
'@
Add-New 'accounts/tests/test_preferred_language.py' $c

$c = @'
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("categories", "__CAT_DEP__"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="name_en",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="category",
            name="name_ar",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
'@
Add-New 'categories/migrations/__CAT_MIG1__.py' $c

$c = @'
from django.db import migrations
from django.db.models import F


def copy_name_to_name_en(apps, schema_editor):
    """Part P-112: every existing category keeps its name as the English one."""
    Category = apps.get_model("categories", "Category")
    Category.objects.filter(name_en="").update(name_en=F("name"))


class Migration(migrations.Migration):

    dependencies = [
        ("categories", "__CAT_MIG1_NAME__"),
    ]

    operations = [
        # Reverse is a no-op: ``name`` was never touched, and dropping the
        # column in the previous migration discards ``name_en`` anyway.
        migrations.RunPython(copy_name_to_name_en, migrations.RunPython.noop),
    ]
'@
Add-New 'categories/migrations/__CAT_MIG2__.py' $c

$c = @'
"""Localized category names (Part P-112)."""

import importlib

import pytest
from django.apps import apps as global_apps
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient

from categories.models import Category
from categories.views import CATEGORY_TREE_CACHE_KEYS

pytestmark = pytest.mark.django_db

ARABIC_FASHION = "\u0623\u0632\u064a\u0627\u0621"
ARABIC_MEN = "\u0631\u062c\u0627\u0644\u064a"


@pytest.fixture(autouse=True)
def _clear_tree_caches():
    cache.delete_many(CATEGORY_TREE_CACHE_KEYS)
    yield
    cache.delete_many(CATEGORY_TREE_CACHE_KEYS)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def tree_url():
    return reverse("categories:category-tree")


def _make_fashion_tree():
    fashion = Category.objects.create(
        name="Fashion", name_en="Fashion", name_ar=ARABIC_FASHION
    )
    Category.objects.create(
        name="Men", name_en="Men", name_ar=ARABIC_MEN, parent=fashion
    )
    return fashion


def test_arabic_header_returns_arabic_names(api_client, tree_url):
    _make_fashion_tree()

    body = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar").json()

    assert body[0]["name"] == ARABIC_FASHION
    assert body[0]["children"][0]["name"] == ARABIC_MEN


def test_english_header_returns_english_names(api_client, tree_url):
    _make_fashion_tree()

    body = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="en").json()

    assert body[0]["name"] == "Fashion"
    assert body[0]["children"][0]["name"] == "Men"


def test_missing_header_falls_back_to_english(api_client, tree_url):
    _make_fashion_tree()

    body = api_client.get(tree_url).json()

    assert body[0]["name"] == "Fashion"


def test_region_and_quality_in_header_are_understood(api_client, tree_url):
    _make_fashion_tree()

    body = api_client.get(
        tree_url, HTTP_ACCEPT_LANGUAGE="ar-EG,ar;q=0.9,en;q=0.8"
    ).json()

    assert body[0]["name"] == ARABIC_FASHION


def test_unsupported_language_falls_back_to_english(api_client, tree_url):
    _make_fashion_tree()

    body = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="fr-FR").json()

    assert body[0]["name"] == "Fashion"


def test_arabic_falls_back_to_english_when_translation_is_missing(api_client, tree_url):
    Category.objects.create(name="Shoes", name_en="Shoes")

    body = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar").json()

    assert body[0]["name"] == "Shoes"


def test_category_without_name_en_still_shows_its_legacy_name(api_client, tree_url):
    category = Category.objects.create(name="Legacy")
    Category.objects.filter(pk=category.pk).update(name_en="")

    english = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="en").json()
    arabic = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar").json()

    assert english[0]["name"] == "Legacy"
    assert arabic[0]["name"] == "Legacy"


def test_languages_do_not_leak_through_the_cache(api_client, tree_url):
    _make_fashion_tree()

    first_ar = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar").json()
    first_en = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="en").json()
    second_ar = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar").json()

    assert first_ar[0]["name"] == ARABIC_FASHION
    assert first_en[0]["name"] == "Fashion"
    assert second_ar == first_ar


def test_editing_a_category_invalidates_both_language_caches(api_client, tree_url):
    fashion = _make_fashion_tree()
    api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar")
    api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="en")

    fashion.name_ar = "\u0645\u0644\u0627\u0628\u0633"
    fashion.save()

    ar = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar").json()
    en = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="en").json()
    assert ar[0]["name"] == "\u0645\u0644\u0627\u0628\u0633"
    assert en[0]["name"] == "Fashion"


def test_response_varies_on_accept_language(api_client, tree_url):
    response = api_client.get(tree_url, HTTP_ACCEPT_LANGUAGE="ar")

    assert "Accept-Language" in response["Vary"]


def test_response_shape_is_unchanged(api_client, tree_url):
    _make_fashion_tree()

    node = api_client.get(tree_url).json()[0]

    assert set(node.keys()) == {"id", "name", "slug", "children"}


def test_save_fills_name_en_from_name():
    category = Category.objects.create(name="Kids")

    assert category.name == "Kids"
    assert category.name_en == "Kids"
    assert category.name_ar == ""


def test_save_fills_name_from_name_en():
    category = Category.objects.create(name_en="Bags")

    assert category.name == "Bags"
    assert category.slug == "bags"


def test_localized_name_method():
    category = Category(name="Fashion", name_en="Fashion", name_ar=ARABIC_FASHION)

    assert category.localized_name("ar") == ARABIC_FASHION
    assert category.localized_name("en") == "Fashion"
    assert category.localized_name("fr") == "Fashion"
    assert Category(name="X").localized_name("ar") == "X"


def test_data_migration_copies_name_into_name_en():
    category = Category.objects.create(name="Shoes")
    Category.objects.filter(pk=category.pk).update(name_en="")

    module = importlib.import_module("categories.migrations.__CAT_MIG2_MODULE__")
    module.copy_name_to_name_en(global_apps, None)

    category.refresh_from_db()
    assert category.name_en == "Shoes"
    assert category.name == "Shoes"


def test_data_migration_does_not_overwrite_an_existing_name_en():
    category = Category.objects.create(name="Shoes", name_en="Footwear")

    module = importlib.import_module("categories.migrations.__CAT_MIG2_MODULE__")
    module.copy_name_to_name_en(global_apps, None)

    category.refresh_from_db()
    assert category.name_en == "Footwear"
    assert category.name == "Shoes"
'@
Add-New 'categories/tests/test_localized_names.py' $c


# --- PASS 1: verify everything before touching anything
Write-Host "`nPass 1: verifying anchors..." -ForegroundColor Cyan
$problems = @()
foreach ($e in $Edits) {
    $p = Join-Path $Backend $e.Rel
    if (-not (Test-Path $p)) { $problems += "missing file: $($e.Rel)"; continue }
    $raw = Read-Text $p
    if ($raw.Contains((Convert-Nl $e.Marker (Get-Nl $raw)))) { continue }
    $old = Convert-Nl $e.Old (Get-Nl $raw)
    $i1 = $raw.IndexOf($old, [StringComparison]::Ordinal)
    if ($i1 -lt 0) { $problems += "anchor not found in $($e.Rel): " + $e.Old.Split("`n")[0].Trim(); continue }
    if ($raw.IndexOf($old, $i1 + 1, [StringComparison]::Ordinal) -ge 0) { $problems += "anchor ambiguous in $($e.Rel): " + $e.Old.Split("`n")[0].Trim() }
}
foreach ($a in $Appends) {
    if (-not (Test-Path (Join-Path $Backend $a.Rel))) { $problems += "missing file: $($a.Rel)" }
}
if ($problems.Count -gt 0) {
    $problems | ForEach-Object { Write-Host "  PROBLEM: $_" -ForegroundColor Red }
    Stop-Transcript | Out-Null
    throw "Pass 1 failed - nothing was modified. Send me the lines above."
}
Write-Host "  all anchors OK" -ForegroundColor Green

# --- backup (outside the repo)
$BackupDir = Join-Path (Split-Path $Backend -Parent) ('p112_backups\step1_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
$touched = @($Edits | ForEach-Object { $_.Rel }) + @($Appends | ForEach-Object { $_.Rel }) | Select-Object -Unique
foreach ($rel in $touched) {
    $dst = Join-Path $BackupDir $rel
    New-Item -ItemType Directory -Path (Split-Path $dst -Parent) -Force | Out-Null
    Copy-Item (Join-Path $Backend $rel) $dst
}
Write-Host "Backups: $BackupDir"

# --- PASS 2: apply
Write-Host "`nPass 2: applying..." -ForegroundColor Cyan
foreach ($e in $Edits) {
    $p = Join-Path $Backend $e.Rel
    $raw = Read-Text $p
    $nl = Get-Nl $raw
    if ($raw.Contains((Convert-Nl $e.Marker $nl))) { Write-Host "  skip (already applied) $($e.Rel)"; continue }
    $new = $raw.Replace((Convert-Nl $e.Old $nl), (Convert-Nl $e.New $nl))
    Write-Text $p $new
    Write-Host "  edited  $($e.Rel)"
}
foreach ($a in $Appends) {
    $p = Join-Path $Backend $a.Rel
    $raw = Read-Text $p
    $nl = Get-Nl $raw
    if ($raw.Contains($a.Marker)) { Write-Host "  skip (already applied) $($a.Rel)"; continue }
    $text = (Convert-Nl $a.Text $nl).Trim("`r", "`n")
    Write-Text $p ($raw.TrimEnd("`r", "`n") + $nl + $nl + $nl + $text + $nl)
    Write-Host "  appended $($a.Rel)"
}
foreach ($f in $NewFiles) {
    $rel = $f.Rel
    $content = $f.Content
    foreach ($k in $tokens.Keys) { $rel = $rel.Replace($k, $tokens[$k]); $content = $content.Replace($k, $tokens[$k]) }
    $p = Join-Path $Backend $rel
    $body = (Convert-Nl $content "`r`n").Trim("`r", "`n") + "`r`n"
    if (Test-Path $p) { Write-Host "  skip (file already exists, not overwritten) $rel"; continue }
    Write-Text $p $body
    Write-Host "  created $rel"
}

# --- summary + next commands
Write-Host "`n=== STEP 1 files applied ===" -ForegroundColor Green
Write-Host "Backend inventory : $invBackend"
Write-Host "Flutter inventory : $invFlutter"
Write-Host "Evidence log      : $(Join-Path $Backend 'p112_step1_evidence.txt')"
Write-Host "Backups           : $BackupDir"
Write-Host "`nNow run the checks from the message (docker compose exec web ...)." -ForegroundColor Yellow
Write-Host "Accounts migration name   : $accMig"
Write-Host "Categories migration names: $catMig1 , $catMig2"
Stop-Transcript | Out-Null