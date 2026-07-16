param(
  [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"
$ForwardArgs = @($args)
$ModelFreeCommand = ($ForwardArgs -contains "-h") -or ($ForwardArgs -contains "--help")
if (-not $ModelFreeCommand) {
  & .\.venv\Scripts\python.exe .\scripts\bootstrap_models.py @ForwardArgs
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
  if (-not $SkipDoctor) {
    & .\.venv\Scripts\python.exe .\scripts\doctor.py @ForwardArgs
    if ($LASTEXITCODE -ne 0) {
      exit $LASTEXITCODE
    }
  }
}
& .\.venv\Scripts\python.exe .\run_tts_batch.py @ForwardArgs
exit $LASTEXITCODE
