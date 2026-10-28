param([Parameter(ValueFromRemainingArguments=$true)][string[]]$LabArgs)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskLocalPython = Join-Path $PSScriptRoot '.venv-local/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskLocalPython)) {
    $taskLocalPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
}
if (-not (Test-Path -LiteralPath $taskLocalPython)) { throw 'Run setup-local.ps1 first.' }
& $taskLocalPython -B -m reel_lab @LabArgs
exit $LASTEXITCODE
