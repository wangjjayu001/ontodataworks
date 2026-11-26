param(
    [string]$PythonPath = "",
    [int]$Port = 19191,
    [switch]$ReloadConfig
)
$ErrorActionPreference = 'Stop'
$bridgeRoot = Split-Path -Parent $PSScriptRoot
$bridgeLocal = Join-Path $bridgeRoot 'local'
New-Item -ItemType Directory -Path $bridgeLocal -Force | Out-Null
$bridgePythonFile = Join-Path $bridgeLocal 'codex-bridge-python.txt'
if (-not $PythonPath -and (Test-Path -LiteralPath $bridgePythonFile)) {
    $PythonPath = (Get-Content -LiteralPath $bridgePythonFile -Raw).Trim()
}
if (-not $PythonPath) { $PythonPath = 'python' }
$bridgePythonCheck = & $PythonPath -c 'import sys; print(sys.version_info >= (3,12))' 2>$null
if ($LASTEXITCODE -ne 0 -or ($bridgePythonCheck -join '').Trim() -ne 'True') {
    throw 'A working Python 3.12+ is required. Start once with -PythonPath pointing to the installed Python executable.'
}
$PythonPath | Set-Content -LiteralPath $bridgePythonFile
$bridgeContractJson = & $PythonPath -c "import json,sys;sys.path.insert(0,sys.argv[1]);from app.model_runtime import MODEL_CONTRACT_VERSION,stage_schema_hashes;print(json.dumps({'model_contract_version':MODEL_CONTRACT_VERSION,'stage_schema_hashes':stage_schema_hashes()}))" (Join-Path $bridgeRoot 'backend')
if ($LASTEXITCODE -ne 0 -or -not $bridgeContractJson) {
    throw 'Could not read the backend model contract. Verify the selected Python environment.'
}
$bridgeExpectedContract = ($bridgeContractJson -join '') | ConvertFrom-Json
$bridgePidFile = Join-Path $bridgeLocal 'codex-bridge.pid'
function Wait-BridgeModels {
    $bridgeTokenFile = Join-Path $bridgeLocal 'codex-bridge.token'
    for($bridgeAttempt=0;$bridgeAttempt -lt 15;$bridgeAttempt++){
        try {
            $bridgeToken = (Get-Content -LiteralPath $bridgeTokenFile -Raw).Trim()
            $bridgeCatalog = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/models" -Headers @{Authorization="Bearer $bridgeToken"} -TimeoutSec 25
            if($bridgeCatalog.model_contract_version -ne $bridgeExpectedContract.model_contract_version){
                throw "Codex bridge contract mismatch: running $($bridgeCatalog.model_contract_version), expected $($bridgeExpectedContract.model_contract_version)."
            }
            foreach($bridgeStage in $bridgeExpectedContract.stage_schema_hashes.PSObject.Properties.Name){
                if($bridgeCatalog.stage_schema_hashes.$bridgeStage -ne $bridgeExpectedContract.stage_schema_hashes.$bridgeStage){
                    throw "Codex bridge schema mismatch for stage '$bridgeStage'."
                }
            }
            if(@($bridgeCatalog.models).Count -gt 0){
                Write-Output "Codex CLI model catalog ready ($(@($bridgeCatalog.models).Count) models; $($bridgeExpectedContract.model_contract_version))."
                return
            }
        } catch {
            if($_.Exception.Message -like 'Codex bridge contract mismatch:*' -or $_.Exception.Message -like 'Codex bridge schema mismatch*'){throw}
            $bridgeLastError=$_.Exception.Message
        }
        Start-Sleep -Seconds 1
    }
    throw "Codex bridge/model catalog did not become ready. Inspect local/codex-bridge.stderr.log and restart a stale bridge after active requests finish. Last check: $bridgeLastError"
}
if (Test-Path -LiteralPath $bridgePidFile) {
    $bridgePid = [int](Get-Content -LiteralPath $bridgePidFile)
    $bridgeExisting = Get-CimInstance Win32_Process -Filter "ProcessId=$bridgePid" -ErrorAction SilentlyContinue
    if ($bridgeExisting -and $bridgeExisting.CommandLine -like '*codex_bridge.py*') {
        if($ReloadConfig){
            & (Join-Path $PSScriptRoot 'Stop-CodexBridge.ps1')
            if($LASTEXITCODE -ne 0){throw 'Could not stop the existing Codex bridge for configuration reload.'}
        }else{
            Wait-BridgeModels
            Write-Output 'Codex bridge is already running and ready. Use -ReloadConfig to reload CC Switch settings.'
            exit 0
        }
    }
}
$bridgeProcess = Start-Process -FilePath $PythonPath -ArgumentList @('scripts/codex_bridge.py','--host','127.0.0.1','--port',"$Port") -WorkingDirectory $bridgeRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $bridgeLocal 'codex-bridge.stdout.log') -RedirectStandardError (Join-Path $bridgeLocal 'codex-bridge.stderr.log')
$bridgeProcess.Id | Set-Content -LiteralPath $bridgePidFile
Wait-BridgeModels
Write-Output 'Codex bridge started on loopback. Token and PID remain in ignored local storage.'
