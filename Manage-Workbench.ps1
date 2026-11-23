param([ValidateSet('Start','Restart','Stop','Status','Verify','Build')][string]$Action='Status')
$ErrorActionPreference='Stop'
$workbenchDocker=Join-Path (Split-Path -Parent $PSScriptRoot) 'runtime\DockerDesktop\resources\bin\docker.exe'
$workbenchDesktop=Join-Path (Split-Path -Parent $PSScriptRoot) 'runtime\DockerDesktop\Docker Desktop.exe'
$workbenchArgs=@('compose','--project-directory',$PSScriptRoot,'--env-file',(Join-Path $PSScriptRoot '.env'),'-f',(Join-Path $PSScriptRoot 'compose.yml'))
if (-not(Test-Path -LiteralPath $workbenchDocker)){throw 'Docker CLI not found. Install Docker Desktop or update the configured path.'}
function Test-WorkbenchModels {
    foreach($workbenchModelService in @('api','worker')){
        & $workbenchDocker @workbenchArgs exec -T $workbenchModelService python -c "from app.model_runtime import list_models; c=list_models(); assert c.get('models'), 'Empty CLI model catalog'; print('CLI models ready:', len(c['models']))"
        if($LASTEXITCODE -ne 0){throw "CLI model readiness failed in $workbenchModelService. The workbench is not fully ready."}
    }
}
if ($Action -in @('Start','Restart')){
    $workbenchEngine=& $workbenchDocker info --format '{{.OSType}}' 2>$null
    if ($LASTEXITCODE -ne 0 -or ($workbenchEngine -join '').Trim() -ne 'linux'){
        Start-Process -FilePath $workbenchDesktop -WindowStyle Hidden
        $workbenchReady=$false
        for($workbenchAttempt=0;$workbenchAttempt -lt 60;$workbenchAttempt++){
            $workbenchEngine=& $workbenchDocker info --format '{{.OSType}}' 2>$null
            if($LASTEXITCODE -eq 0 -and ($workbenchEngine -join '').Trim() -eq 'linux'){$workbenchReady=$true;break}
            Start-Sleep -Seconds 2
        }
        if(-not $workbenchReady){throw 'Docker Linux engine did not become ready.'}
    }
    & (Join-Path $PSScriptRoot 'scripts\Start-CodexBridge.ps1')
    if($LASTEXITCODE -ne 0){throw 'Codex bridge startup failed.'}
    if($Action -eq 'Restart'){
        # Stop dependents before the database; up waits for database/API health.
        & $workbenchDocker @workbenchArgs stop
        if($LASTEXITCODE -ne 0){throw 'Ordered service stop failed.'}
    }
    & $workbenchDocker @workbenchArgs up -d --wait --wait-timeout 180
    if($LASTEXITCODE -ne 0){throw 'Independent workbench startup failed. Retain volumes and inspect logs.'}
    $workbenchHealth=Invoke-RestMethod -Uri 'http://127.0.0.1:19090/api/health' -TimeoutSec 10
    if($workbenchHealth.status -ne 'ok'){throw 'API readiness check failed.'}
    Test-WorkbenchModels
    Write-Output 'Independent workbench ready: http://127.0.0.1:19090'
}elseif($Action -eq 'Stop'){
    & $workbenchDocker @workbenchArgs stop
    if($LASTEXITCODE -ne 0){throw 'Stop failed.'}
    & (Join-Path $PSScriptRoot 'scripts\Stop-CodexBridge.ps1')
}elseif($Action -eq 'Status'){
    & $workbenchDocker @workbenchArgs ps --all
    exit $LASTEXITCODE
}elseif($Action -eq 'Build'){
    & $workbenchDocker @workbenchArgs build api worker pipeline-worker
    if($LASTEXITCODE -ne 0){throw 'API build failed.'}
    Write-Output 'API image built. Build frontend separately before starting.'
}elseif($Action -eq 'Verify'){
    $workbenchHome=Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:19090/' -TimeoutSec 10
    $workbenchHealth=Invoke-RestMethod -Uri 'http://127.0.0.1:19090/api/health' -TimeoutSec 10
    if($workbenchHome.StatusCode -ne 200 -or $workbenchHealth.status -ne 'ok'){throw 'Basic readiness failed.'}
    Test-WorkbenchModels
    Write-Output 'Frontend, API/database and CLI model readiness passed; full acceptance evidence is in evidence/.'
}
