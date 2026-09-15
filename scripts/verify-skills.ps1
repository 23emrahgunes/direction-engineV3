$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$SkillRoot = Join-Path $RepoRoot ".codex\skills"

if (-not (Test-Path $SkillRoot)) {
    throw "Skill root not found: $SkillRoot"
}

$files = Get-ChildItem -Path $SkillRoot -Filter "SKILL.md" -Recurse -File
if ($files.Count -eq 0) {
    throw "No SKILL.md files found."
}

$errors = @()
$names = @()

foreach ($file in $files) {
    $content = Get-Content -Raw -Path $file.FullName
    if (-not $content.StartsWith("---")) {
        $errors += "$($file.FullName): missing YAML frontmatter"
        continue
    }

    $nameMatch = [regex]::Match($content, "(?m)^name:\s*([a-z0-9-]+)\s*$")
    $descMatch = [regex]::Match($content, "(?m)^description:\s*(.+)\s*$")

    if (-not $nameMatch.Success) {
        $errors += "$($file.FullName): missing name"
        continue
    }
    if (-not $descMatch.Success) {
        $errors += "$($file.FullName): missing description"
        continue
    }

    $folderName = Split-Path -Leaf $file.DirectoryName
    $skillName = $nameMatch.Groups[1].Value

    if ($folderName -ne $skillName) {
        $errors += "$($file.FullName): folder '$folderName' != name '$skillName'"
    }

    $names += $skillName
}

$dupes = $names | Group-Object | Where-Object { $_.Count -gt 1 }
foreach ($dupe in $dupes) {
    $errors += "Duplicate skill name: $($dupe.Name)"
}

Write-Host ""
Write-Host "direction-engineV3 skills found: $($names.Count)"
$names | Sort-Object | ForEach-Object { Write-Host "  OK  $_" }

if ($errors.Count -gt 0) {
    Write-Host ""
    Write-Host "VALIDATION FAILED" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "  ERROR  $_" -ForegroundColor Red }
    exit 1
}

Write-Host ""
Write-Host "VALIDATION PASSED" -ForegroundColor Green
