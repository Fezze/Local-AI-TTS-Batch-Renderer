param(
  [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"
$ForwardArgs = @($args)
$ModelFreeCommand = $false
foreach ($Argument in $ForwardArgs) {
  if ($Argument -eq "-h" -or $Argument -eq "--help" -or $Argument -eq "--list-chapters" -or $Argument -eq "--wav-to-mp3" -or $Argument.StartsWith("--wav-to-mp3=")) {
    $ModelFreeCommand = $true
    break
  }
}
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
& .\.venv\Scripts\python.exe .\md_to_audio.py @ForwardArgs
exit $LASTEXITCODE
