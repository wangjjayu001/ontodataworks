$ErrorActionPreference = 'Stop'
$bridgeRoot = Split-Path -Parent $PSScriptRoot
$bridgePidFile = Join-Path $bridgeRoot 'local/codex-bridge.pid'
if (-not (Test-Path -LiteralPath $bridgePidFile)) {
    Write-Output 'No managed Codex bridge PID exists.'
    exit 0
}
$bridgePid = [int](Get-Content -LiteralPath $bridgePidFile)
$bridgeProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$bridgePid" -ErrorAction SilentlyContinue
if ($bridgeProcess -and $bridgeProcess.CommandLine -like '*codex_bridge.py*' -and $bridgeProcess.CommandLine -notlike '*Stop-CodexBridge*') {
    Stop-Process -Id $bridgePid
    Remove-Item -LiteralPath $bridgePidFile
    Write-Output 'Codex bridge stopped. Active model requests must be reconciled by the Agent.'
} else {
    throw 'PID no longer identifies the managed Codex bridge; no process was stopped.'
}
