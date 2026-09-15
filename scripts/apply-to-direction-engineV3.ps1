param(
    [string]$Target = "C:\wamp64\www\direction-engineV3"
)

$ErrorActionPreference = "Stop"
$PackRoot = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path $Target)) {
    throw "Target does not exist: $Target"
}

$items = @(
    "AGENTS.md",
    "SKILLS_INDEX.md",
    "START_HERE.md",
    ".codex"
)

foreach ($item in $items) {
    $src = Join-Path $PackRoot $item
    $dst = Join-Path $Target $item
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination $dst -Recurse -Force
        Write-Host "COPIED: $item"
    }
}

$targetScripts = Join-Path $Target "scripts"
New-Item -ItemType Directory -Path $targetScripts -Force | Out-Null

Copy-Item (Join-Path $PSScriptRoot "verify-skills.ps1") $targetScripts -Force
Copy-Item (Join-Path $PSScriptRoot "install-skills-user.ps1") $targetScripts -Force

Write-Host ""
Write-Host "Applied skill pack to: $Target"
Write-Host "Next:"
Write-Host "  cd `"$Target`""
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\verify-skills.ps1"
