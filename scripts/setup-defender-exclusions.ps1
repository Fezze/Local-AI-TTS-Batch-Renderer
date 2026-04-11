param(
    [string]$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Require-Admin {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script as Administrator."
    }
}

function Add-PathExclusionIfMissing {
    param([string]$PathValue)
    $resolved = (Resolve-Path $PathValue -ErrorAction SilentlyContinue)
    if (-not $resolved) { return }
    $target = $resolved.Path
    $existing = @(Get-MpPreference).ExclusionPath
    if ($existing -contains $target) {
        Write-Host "Path exclusion exists: $target"
        return
    }
    Add-MpPreference -ExclusionPath $target
    Write-Host "Added path exclusion: $target"
}

function Add-ProcessExclusionIfMissing {
    param([string]$ProcessPath)
    if (-not (Test-Path $ProcessPath)) { return }
    $existing = @(Get-MpPreference).ExclusionProcess
    if ($existing -contains $ProcessPath) {
        Write-Host "Process exclusion exists: $ProcessPath"
        return
    }
    Add-MpPreference -ExclusionProcess $ProcessPath
    Write-Host "Added process exclusion: $ProcessPath"
}

Require-Admin

$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$paths = @(
    (Join-Path $ProjectRoot ".venv"),
    (Join-Path $ProjectRoot "models"),
    (Join-Path $ProjectRoot "out")
)

foreach ($p in $paths) { Add-PathExclusionIfMissing -PathValue $p }
Add-ProcessExclusionIfMissing -ProcessPath $venvPython

Write-Host ""
Write-Host "Current ExclusionPath:"
(Get-MpPreference).ExclusionPath | ForEach-Object { Write-Host " - $_" }
Write-Host ""
Write-Host "Current ExclusionProcess:"
(Get-MpPreference).ExclusionProcess | ForEach-Object { Write-Host " - $_" }
