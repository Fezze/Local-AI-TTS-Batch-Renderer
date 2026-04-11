param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$Args
)

$ErrorActionPreference = "Stop"
& .\.venv\Scripts\python.exe .\md_to_audio.py @Args
