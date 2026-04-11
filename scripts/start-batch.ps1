param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Args
)

$ErrorActionPreference = "Stop"
& .\.venv\Scripts\python.exe .\run_tts_batch.py @Args
