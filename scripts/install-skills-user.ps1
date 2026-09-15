param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SourceRoot = Join-Path $RepoRoot ".codex\skills"
$CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".codex" }
$DestRoot = Join-Path $CodexHome "skills"

if (-not (Test-Path $SourceRoot)) {
    throw "Source skill root not found: $SourceRoot"
}

New-Item -ItemType Directory -Path $DestRoot -Force | Out-Null

$skillDirs = Get-ChildItem -Path $SourceRoot -Directory

foreach ($dir in $skillDirs) {
    $dest = Join-Path $DestRoot $dir.Name

    if (Test-Path $dest) {
        if (-not $Force) {
            Write-Host "SKIP existing: $($dir.Name)  (use -Force to replace)"
            continue
        }
        Remove-Item -Path $dest -Recurse -Force
    }

    Copy-Item -Path $dir.FullName -Destination $dest -Recurse -Force
    Write-Host "INSTALLED: $($dir.Name)"
}

Write-Host ""
Write-Host "Destination: $DestRoot"
Write-Host "Open a NEW Codex session before testing skill discovery."
