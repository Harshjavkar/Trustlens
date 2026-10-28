param([string]$PythonExe = '', [switch]$WithSpeech, [switch]$WithOcr)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPythonArgs = @()
if (-not $PythonExe) {
    foreach ($taskName in @('py', 'python', 'python3')) {
        $taskCommand = Get-Command -Name $taskName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $taskCommand) { continue }
        $taskProbeArgs = @()
        if ($taskName -eq 'py') { $taskProbeArgs = @('-3') }
        try {
            & $taskCommand.Source @taskProbeArgs -B -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $PythonExe = $taskCommand.Source; $taskPythonArgs = $taskProbeArgs; break }
        } catch { continue }
    }
}
if (-not $PythonExe -and $env:USERPROFILE) {
    $taskBundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if (Test-Path -LiteralPath $taskBundledPython) { $PythonExe = $taskBundledPython }
}
if (-not $PythonExe) { throw 'Install Python 3.11+, or pass -PythonExe with its full executable path.' }
& $PythonExe @taskPythonArgs -B -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
$taskRuntime = Join-Path $PSScriptRoot '.venv-local'
$taskPython = Join-Path $taskRuntime 'Scripts/python.exe'
if (Test-Path -LiteralPath (Join-Path $taskRuntime 'bin/python')) { $taskPython = Join-Path $taskRuntime 'bin/python' }
if (-not (Test-Path -LiteralPath $taskPython)) {
    & $PythonExe @taskPythonArgs -m venv $taskRuntime
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the local environment.' }
    if (Test-Path -LiteralPath (Join-Path $taskRuntime 'bin/python')) { $taskPython = Join-Path $taskRuntime 'bin/python' }
}
& $taskPython -B -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'The existing local environment is not usable here. Create a fresh Python 3.11+ environment.' }
$taskExtras = @()
if ($WithSpeech) { $taskExtras += 'speech' }
if ($WithOcr) { $taskExtras += 'ocr' }
$taskInstallTarget = '.'
if ($taskExtras.Count) { $taskInstallTarget = '.[' + ($taskExtras -join ',') + ']' }
& $taskPython -m pip install --timeout 20 --retries 1 -e $taskInstallTarget
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; existing evidence is preserved.' }
& $taskPython -B -m reel_lab doctor
if ($LASTEXITCODE -ne 0) { throw 'Local environment verification failed.' }
Write-Host 'Ready. Run .\start-instant.ps1 to open the local app. No media or models were collected by setup.'
