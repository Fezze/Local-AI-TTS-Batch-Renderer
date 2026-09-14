param(
  [string]$Python = "python",
  [switch]$Dev
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$VenvReady = $false
if (Test-Path ".venv/Scripts/python.exe") {
  try {
    & .\.venv\Scripts\python.exe -V *> $null
    $VenvReady = ($LASTEXITCODE -eq 0)
  } catch {
    $VenvReady = $false
  }
}
if ((Test-Path ".venv") -and -not $VenvReady) {
  Remove-Item -Recurse -Force .venv
}
if (-not $VenvReady) {
  & $Python -m venv .venv
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($Dev) {
  & .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
exit 0
