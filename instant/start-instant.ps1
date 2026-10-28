param([int]$Port = 8765, [string]$Creator = 'financewithsharan')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskCandidates = @(
    (Join-Path $PSScriptRoot '.venv-local/Scripts/python.exe'),
    (Join-Path $PSScriptRoot '.venv/Scripts/python.exe'),
    (Join-Path $PSScriptRoot '.venv-local/bin/python'),
    (Join-Path $PSScriptRoot '.venv/bin/python')
)
$taskPython = $null
$taskPythonArgs = @()
foreach ($taskCandidate in $taskCandidates) {
    if (Test-Path -LiteralPath $taskCandidate) {
        & $taskCandidate -B -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { $taskPython = $taskCandidate; break }
    }
}
if (-not $taskPython) {
    foreach ($taskName in @('py', 'python', 'python3')) {
        $taskCommand = Get-Command -Name $taskName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $taskCommand) { continue }
        $taskProbeArgs = @()
        if ($taskName -eq 'py') { $taskProbeArgs = @('-3') }
        try {
            & $taskCommand.Source @taskProbeArgs -B -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $taskPython = $taskCommand.Source; $taskPythonArgs = $taskProbeArgs; break }
        } catch { continue }
    }
}
if (-not $taskPython -and $env:USERPROFILE) {
    # Optional fallback for this development machine; ordinary Python installs
    # and project environments take precedence and require no Codex runtime.
    $taskBundled = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if (Test-Path -LiteralPath $taskBundled) {
        & $taskBundled -B -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>$null
        if ($LASTEXITCODE -eq 0) { $taskPython = $taskBundled }
    }
}
if (-not $taskPython) { throw 'Run setup-local.ps1 with a working Python 3.11+ interpreter.' }
$taskManifest = Join-Path $PSScriptRoot 'data/live-first-test/manifest.json'
if (Test-Path -LiteralPath $taskManifest -PathType Leaf) {
    & $taskPython @taskPythonArgs -B -m reel_lab library --creator $Creator
    if ($LASTEXITCODE -ne 0) { throw 'Could not import the saved evidence. Check the manifest and creator.' }
} else {
    & $taskPython @taskPythonArgs -B -m reel_lab bootstrap-demo
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the bundled demo. Existing libraries are preserved.' }
}
& $taskPython @taskPythonArgs -B -m reel_lab serve --port $Port
exit $LASTEXITCODE
