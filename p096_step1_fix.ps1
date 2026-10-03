# =====================================================================
# P-096 / STEP 1 - FIX for the bug found by the sweep
#
# Bug: a 404 raised by DRF's generic get_object() (Django Http404) came
# out of core.exceptions.custom_exception_handler with code "ERROR"
# instead of "NOT_FOUND" (same for Django's own PermissionDenied ->
# should be PERMISSION_DENIED). Breaks the P-012 envelope contract.
#
# Run from: D:\Cavallo\scd-backend   (Windows PowerShell)
# MODIFIES : core\exceptions.py          (3 small replacements)
# CREATES  : core\tests\test_exceptions_django_errors.py
# =====================================================================

$ErrorActionPreference = "Stop"
$root = (Get-Location).Path
if (-not (Test-Path (Join-Path $root "manage.py"))) {
    throw "manage.py not found. cd D:\Cavallo\scd-backend first."
}
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# ---------------------------------------------------------------------
# 1) core/exceptions.py  (file uses CRLF)
# ---------------------------------------------------------------------
$path = Join-Path $root "core\exceptions.py"
$text = [System.IO.File]::ReadAllText($path)

if ($text.Contains("DjangoPermissionDenied")) {
    Write-Host "SKIP       core\exceptions.py (already patched)" -ForegroundColor Yellow
}
else {
    $nl = "`r`n"

    # 1a) imports - Django side
    $old1 = "from django.conf import settings$nl"
    $new1 = "from django.conf import settings$nl" +
        "from django.core.exceptions import PermissionDenied as DjangoPermissionDenied$nl" +
        "from django.http import Http404$nl"

    # 1b) imports - DRF side
    $old2 = "from rest_framework.exceptions import APIException, ValidationError$nl"
    $new2 = "from rest_framework.exceptions import APIException, NotFound, ValidationError$nl" +
        "from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied$nl"

    # 1c) normalise Django-native exceptions BEFORE DRF's handler runs
    $old3 = "    response = drf_exception_handler(exc, context)$nl"
    $new3 = "    # Part P-096: DRF's generic views (get_object()) raise Django's Http404,$nl" +
        "    # and Django code may raise django.core.exceptions.PermissionDenied.$nl" +
        "    # DRF's own handler turns both into proper responses, but this$nl" +
        "    # function reads `exc.get_codes()` below, which the Django classes$nl" +
        "    # lack, so they used to come out as code `ERROR`. Convert them to the$nl" +
        "    # equivalent DRF exceptions first so they map to NOT_FOUND /$nl" +
        "    # PERMISSION_DENIED like every other error (P-012 contract).$nl" +
        "    if isinstance(exc, Http404):$nl" +
        "        exc = NotFound()$nl" +
        "    elif isinstance(exc, DjangoPermissionDenied):$nl" +
        "        exc = DRFPermissionDenied()$nl$nl" +
        "    response = drf_exception_handler(exc, context)$nl"

    foreach ($pair in @(@($old1, "import-django"), @($old2, "import-drf"), @($old3, "handler-call"))) {
        $count = ([regex]::Matches($text, [regex]::Escape($pair[0]))).Count
        if ($count -ne 1) {
            throw "Expected exactly 1 match for '$($pair[1])' in core\exceptions.py, found $count. Nothing was written."
        }
    }

    $text = $text.Replace($old1, $new1).Replace($old2, $new2).Replace($old3, $new3)
    [System.IO.File]::WriteAllText($path, $text, $utf8NoBom)
    Write-Host "MODIFY     core\exceptions.py" -ForegroundColor Green
}

# ---------------------------------------------------------------------
# 2) core/tests/test_exceptions_django_errors.py  (new)
# ---------------------------------------------------------------------
$testContent = @'
"""
Regression tests for the P-096 fix in core.exceptions.

Django-native Http404 / PermissionDenied must come out of
custom_exception_handler in the standard P-012 envelope with the
NOT_FOUND / PERMISSION_DENIED codes, not the generic ERROR fallback.
"""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404

from core.exceptions import custom_exception_handler


def test_django_http404_maps_to_not_found_code():
    response = custom_exception_handler(Http404("No matching row."), {})

    assert response.status_code == 404
    assert response.data["error"]["code"] == "NOT_FOUND"
    assert response.data["error"]["fields"] == {}
    assert response.data["error"]["message"]


def test_django_permission_denied_maps_to_permission_denied_code():
    response = custom_exception_handler(DjangoPermissionDenied(), {})

    assert response.status_code == 403
    assert response.data["error"]["code"] == "PERMISSION_DENIED"
    assert response.data["error"]["fields"] == {}
    assert response.data["error"]["message"]
'@
$testPath = Join-Path $root "core\tests\test_exceptions_django_errors.py"
$testText = (($testContent -replace "`r`n", "`n") -replace "`n", "`r`n").TrimEnd() + "`r`n"
if (Test-Path $testPath) { Write-Host "OVERWRITE  core\tests\test_exceptions_django_errors.py" -ForegroundColor Yellow }
else { Write-Host "CREATE     core\tests\test_exceptions_django_errors.py" -ForegroundColor Green }
[System.IO.File]::WriteAllText($testPath, $testText, $utf8NoBom)

Write-Host ""
Write-Host "Done." -ForegroundColor Cyan