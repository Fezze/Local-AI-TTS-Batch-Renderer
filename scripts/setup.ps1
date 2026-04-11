param(
  [string]$Python = "python",
  [switch]$Dev
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path ".venv")) {
  & $Python -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($Dev) {
  & .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
}
