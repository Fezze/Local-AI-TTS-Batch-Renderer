param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$OutputDir = "out",
    [switch]$ClearResume
)

$ErrorActionPreference = "Continue"

$projectRootResolved = (Resolve-Path $ProjectRoot).Path
$outputDirResolved = Join-Path $projectRootResolved $OutputDir
$repoTmp = Join-Path $projectRootResolved ".tmp"
$batchTmpRoot = Join-Path $env:TEMP "local-tts-batch"

Write-Host "Project root: $projectRootResolved"

# Stop python processes only from this repo venv.
Get-Process python,pythonw -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($projectRootResolved, [System.StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object {
        Write-Host "Stopping PID $($_.Id)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }

if (Test-Path -LiteralPath $repoTmp) {
    Write-Host "Removing repo temp: $repoTmp"
    Remove-Item -LiteralPath $repoTmp -Recurse -Force -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $batchTmpRoot) {
    Write-Host "Removing batch temp root: $batchTmpRoot"
    Remove-Item -LiteralPath $batchTmpRoot -Recurse -Force -ErrorAction SilentlyContinue
}

if (Test-Path -LiteralPath $outputDirResolved) {
    $legacyTmp = Join-Path $outputDirResolved "_tmp_runtime"
    if (Test-Path -LiteralPath $legacyTmp) {
        Write-Host "Removing legacy runtime temp: $legacyTmp"
        Remove-Item -LiteralPath $legacyTmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($ClearResume -and (Test-Path -LiteralPath $outputDirResolved)) {
    Write-Host "Removing resume checkpoints under: $outputDirResolved"
    Get-ChildItem -Path $outputDirResolved -Filter *.resume.json -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
}

Write-Host "Recovery completed."
