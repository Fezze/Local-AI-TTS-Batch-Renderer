param(
  [switch]$SkipDoctor,
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Args
)

$ErrorActionPreference = "Stop"
if (-not $SkipDoctor) {
  & .\.venv\Scripts\python.exe .\scripts\doctor.py
}
& .\.venv\Scripts\python.exe .\run_tts_batch.py @Args
