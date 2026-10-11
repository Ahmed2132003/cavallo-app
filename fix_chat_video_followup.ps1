<#
.SYNOPSIS
  Cavallo - chat video Flutter follow-up fixes. Run it AFTER
  apply_chat_video_fix.ps1, from anywhere. ASCII-only script.

.DESCRIPTION
  Fixes the two repo guard tests that failed after the first script:
  1. test/core/widgets/cavallo_app_bar_test.dart: chat_video_viewer_screen.dart
     built a plain AppBar; it now uses CavalloAppBar (black bar via a local
     AppBarTheme).
  2. test/core/theme/status_tokens_test.dart: only black/white/white24/
     white54/transparent are allowed. The viewer now uses those, and two
     older reel files (reel_video_player.dart, reel_detail_screen.dart) that
     already broke the guard use allowed values too.

  Same safety rules: validates every edit first, keeps *.bak_chatvideo2
  backups, safe to re-run.
#>
[CmdletBinding()]
param(
    [string]$FlutterRoot = 'D:\Cavallo\social_commerce_app',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Read-TextFile {
    param([string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    if ($hasBom) {
        $text = [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
    } else {
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    }
    $crlf = $text.Contains("`r`n")
    $norm = $text.Replace("`r`n", "`n")
    return [pscustomobject]@{ Text = $norm; Crlf = $crlf; Bom = $hasBom }
}

function Write-TextFile {
    param([string]$Path, [string]$Text, [bool]$Crlf, [bool]$Bom)
    if ($Crlf) { $Text = $Text.Replace("`n", "`r`n") }
    $enc = New-Object System.Text.UTF8Encoding($Bom)
    [System.IO.File]::WriteAllText($Path, $Text, $enc)
}

function Get-RootPath {
    param([string]$Root)
    return $FlutterRoot
}

# Special characters are written as {{U+XXXX}} so this script stays
# pure ASCII; they are decoded to real characters at run time.
function Decode-Text {
    param([string]$Text)
    return [regex]::Replace($Text, '\{\{U\+([0-9A-F]{4})\}\}', {
        param($m)
        [string][char][Convert]::ToInt32($m.Groups[1].Value, 16)
    })
}


$Edits = @(
  @{
    Root = 'Flutter'
    File = 'lib/features/chat/presentation/chat_video_viewer_screen.dart'
    Marker = @'
import '../../../core/widgets/cavallo_app_bar.dart';
'@
    Old = @'
import '../../../core/l10n/l10n_context.dart';
'@
    New = @'
import '../../../core/l10n/l10n_context.dart';
import '../../../core/widgets/cavallo_app_bar.dart';
'@
  }
  @{
    Root = 'Flutter'
    File = 'lib/features/chat/presentation/chat_video_viewer_screen.dart'
    Marker = @'
appBar: const CavalloAppBar(),
'@
    Old = @'
    return Scaffold(
      key: const ValueKey('chatVideoViewer'),
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        elevation: 0,
      ),
      body: SafeArea(child: _buildBody(context)),
    );
'@
    New = @'
    // The viewer is always dark: force a black app bar around the shared
    // CavalloAppBar so the Cavallo logo stays on every screen.
    return Theme(
      data: Theme.of(context).copyWith(
        appBarTheme: const AppBarTheme(
          backgroundColor: Colors.black,
          foregroundColor: Colors.white,
          elevation: 0,
        ),
      ),
      child: Scaffold(
        key: const ValueKey('chatVideoViewer'),
        backgroundColor: Colors.black,
        appBar: const CavalloAppBar(),
        body: SafeArea(child: _buildBody(context)),
      ),
    );
'@
  }
  @{
    Root = 'Flutter'
    File = 'lib/features/chat/presentation/chat_video_viewer_screen.dart'
    Marker = @'
const Icon(Icons.error_outline, color: Colors.white54, size: 48),
'@
    Old = @'
const Icon(Icons.error_outline, color: Colors.white70, size: 48),
'@
    New = @'
const Icon(Icons.error_outline, color: Colors.white54, size: 48),
'@
  }
  @{
    Root = 'Flutter'
    File = 'lib/features/chat/presentation/chat_video_viewer_screen.dart'
    Marker = @'
bufferedColor: Colors.white54,
                    backgroundColor: Colors.white24,
'@
    Old = @'
                    bufferedColor: Colors.white30,
                    backgroundColor: Colors.white12,
'@
    New = @'
                    bufferedColor: Colors.white54,
                    backgroundColor: Colors.white24,
'@
  }
  @{
    Root = 'Flutter'
    File = 'lib/features/chat/presentation/chat_video_viewer_screen.dart'
    Marker = @'
style: const TextStyle(color: Colors.white, fontSize: 12),
'@
    Old = @'
style: const TextStyle(color: Colors.white70, fontSize: 12),
'@
    New = @'
style: const TextStyle(color: Colors.white, fontSize: 12),
'@
  }
  @{
    Root = 'Flutter'
    File = 'lib/features/content/presentation/reel_video_player.dart'
    Marker = @'
bufferedColor: Colors.white54,
'@
    Old = @'
bufferedColor: Colors.white38,
'@
    New = @'
bufferedColor: Colors.white54,
'@
  }
  @{
    Root = 'Flutter'
    File = 'lib/features/content/presentation/reel_video_player.dart'
    Marker = @'
const Icon(Icons.error_outline, color: Colors.white54, size: 40),
'@
    Old = @'
const Icon(Icons.error_outline, color: Colors.white70, size: 40),
'@
    New = @'
const Icon(Icons.error_outline, color: Colors.white54, size: 40),
'@
  }
  @{
    Root = 'Flutter'
    File = 'lib/features/content/presentation/reel_detail_screen.dart'
    Marker = @'
Colors.black.withValues(alpha: 0.54)
'@
    Old = @'
    const shadow = [Shadow(blurRadius: 4, color: Colors.black54)];
'@
    New = @'
    final shadow = [
      Shadow(blurRadius: 4, color: Colors.black.withValues(alpha: 0.54)),
    ];
'@
  }
)

$NewFiles = @()

# ---------------------------------------------------------------------------
# Phase 0: sanity
# ---------------------------------------------------------------------------
foreach ($r in @($FlutterRoot)) {
    if (-not (Test-Path -LiteralPath $r)) {
        throw "Folder not found: $r  (use -FlutterRoot)"
    }
}
try {
    $branch = (& git -C $FlutterRoot rev-parse --abbrev-ref HEAD 2>$null)
    if ($branch -and ($branch -ne 'part-111')) {
        Write-Warning "Flutter repo is on branch '$branch'. This script was written against branch 'part-111'."
    }
} catch { }

# ---------------------------------------------------------------------------
# Phase 1: validate + compute every edit IN MEMORY (nothing is written yet)
# ---------------------------------------------------------------------------
$plans = [ordered]@{}
$problems = New-Object System.Collections.ArrayList
$applied = 0
$skipped = 0

foreach ($e in $Edits) {
    $full = Join-Path (Get-RootPath $e.Root) ($e.File.Replace('/', '\'))
    if (-not $plans.Contains($full)) {
        if (-not (Test-Path -LiteralPath $full)) {
            [void]$problems.Add("Missing file: $full")
            continue
        }
        $f = Read-TextFile $full
        $plans[$full] = [pscustomobject]@{
            Path = $full; Rel = $e.File; Text = $f.Text; Crlf = $f.Crlf; Bom = $f.Bom; Changed = $false
        }
    }
    $plan = $plans[$full]
    $old = (Decode-Text $e.Old).Replace("`r`n", "`n")
    $new = (Decode-Text $e.New).Replace("`r`n", "`n")
    $marker = (Decode-Text $e.Marker).Replace("`r`n", "`n")

    if ($plan.Text.Contains($marker)) {
        $skipped++
        continue
    }
    $count = ([regex]::Matches($plan.Text, [regex]::Escape($old))).Count
    if ($count -ne 1) {
        $firstLine = ($old -split "`n")[0]
        [void]$problems.Add("$($e.File): anchor found $count times (expected 1). First line: $firstLine")
        continue
    }
    $plan.Text = $plan.Text.Replace($old, $new)
    $plan.Changed = $true
    $applied++
}

$newPlans = New-Object System.Collections.ArrayList
foreach ($nf in $NewFiles) {
    $full = Join-Path (Get-RootPath $nf.Root) ($nf.File.Replace('/', '\'))
    $content = (Decode-Text $nf.Content).Replace("`r`n", "`n") + "`n"
    $state = 'create'
    if (Test-Path -LiteralPath $full) {
        $existing = Read-TextFile $full
        if ($existing.Text -eq $content) { $state = 'same' } else { $state = 'overwrite' }
    }
    [void]$newPlans.Add([pscustomobject]@{ Path = $full; Rel = $nf.File; Content = $content; State = $state })
}

if ($problems.Count -gt 0) {
    Write-Host ''
    Write-Host 'NOTHING WAS CHANGED. These edits could not be matched:' -ForegroundColor Red
    foreach ($p in $problems) { Write-Host "  - $p" -ForegroundColor Red }
    Write-Host ''
    Write-Host 'Make sure apply_chat_video_fix.ps1 was applied first (on branch part-111).' -ForegroundColor Yellow
    Write-Host 'Send me the file(s) above (or run: git status / git pull) and I will adjust the script.' -ForegroundColor Yellow
    throw 'Validation failed.'
}

Write-Host ''
Write-Host "Validation OK: $applied edit(s) to apply, $skipped already applied, $($newPlans.Count) new file(s)." -ForegroundColor Green

if ($DryRun) {
    Write-Host 'DryRun: nothing written.' -ForegroundColor Yellow
    return
}

# ---------------------------------------------------------------------------
# Phase 2: write (backup first)
# ---------------------------------------------------------------------------
foreach ($plan in $plans.Values) {
    if (-not $plan.Changed) { continue }
    $bak = $plan.Path + '.bak_chatvideo2'
    if (-not (Test-Path -LiteralPath $bak)) { Copy-Item -LiteralPath $plan.Path -Destination $bak }
    Write-TextFile -Path $plan.Path -Text $plan.Text -Crlf $plan.Crlf -Bom $plan.Bom
    Write-Host "  edited   $($plan.Rel)"
}

foreach ($np in $newPlans) {
    if ($np.State -eq 'same') {
        Write-Host "  same     $($np.Rel)"
        continue
    }
    $dir = Split-Path -Parent $np.Path
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    if ($np.State -eq 'overwrite') {
        $bak = $np.Path + '.bak_chatvideo2'
        if (-not (Test-Path -LiteralPath $bak)) { Copy-Item -LiteralPath $np.Path -Destination $bak }
    }
    Write-TextFile -Path $np.Path -Text $np.Content -Crlf $false -Bom $false
    Write-Host "  created  $($np.Rel)"
}

Write-Host ''
Write-Host 'DONE. Next steps:' -ForegroundColor Green
Write-Host "  cd $FlutterRoot"
Write-Host '  flutter analyze'
Write-Host '  flutter test test/core/theme/status_tokens_test.dart test/core/widgets/cavallo_app_bar_test.dart'
Write-Host '  flutter test test/features/chat'
Write-Host '  flutter test --concurrency=2'
Write-Host '  Backups of every edited file: *.bak_chatvideo22'