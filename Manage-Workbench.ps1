param(
    [ValidateSet('Initialize','Start','Restart','Stop','Status','Verify','Build')][string]$Action='Status',
    [switch]$WithoutModel
)
$ErrorActionPreference='Stop'
$workbenchDocker=$env:ONTODATAWORKS_DOCKER
if(-not $workbenchDocker){
    $workbenchCommand=Get-Command docker -ErrorAction SilentlyContinue
    if($workbenchCommand){$workbenchDocker=$workbenchCommand.Source}
}
if(-not $workbenchDocker){$workbenchDocker=Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin\docker.exe'}
$workbenchDesktop=$env:ONTODATAWORKS_DOCKER_DESKTOP
if(-not $workbenchDesktop){$workbenchDesktop=Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'}
$workbenchArgs=@('compose','--project-directory',$PSScriptRoot,'--env-file',(Join-Path $PSScriptRoot '.env'),'-f',(Join-Path $PSScriptRoot 'compose.yml'))
if (-not(Test-Path -LiteralPath $workbenchDocker)){throw 'Docker CLI not found. Install Docker Desktop or update the configured path.'}
function Test-WorkbenchModels {
    foreach($workbenchModelService in @('api','worker')){
        & $workbenchDocker @workbenchArgs exec -T $workbenchModelService python -c "from app.model_runtime import list_models; c=list_models(); assert c.get('models'), 'Empty CLI model catalog'; print('CLI models ready:', len(c['models']))"
        if($LASTEXITCODE -ne 0){throw "CLI model readiness failed in $workbenchModelService. The workbench is not fully ready."}
    }
}
if ($Action -in @('Initialize','Start','Restart')){
    $workbenchEngine=& $workbenchDocker info --format '{{.OSType}}' 2>$null
    if ($LASTEXITCODE -ne 0 -or ($workbenchEngine -join '').Trim() -ne 'linux'){
        if(-not(Test-Path -LiteralPath $workbenchDesktop)){throw 'Start Docker Desktop Linux engine or set ONTODATAWORKS_DOCKER_DESKTOP.'}
        Start-Process -FilePath $workbenchDesktop -WindowStyle Hidden
        $workbenchReady=$false
        for($workbenchAttempt=0;$workbenchAttempt -lt 60;$workbenchAttempt++){
            $workbenchEngine=& $workbenchDocker info --format '{{.OSType}}' 2>$null
            if($LASTEXITCODE -eq 0 -and ($workbenchEngine -join '').Trim() -eq 'linux'){$workbenchReady=$true;break}
            Start-Sleep -Seconds 2
        }
        if(-not $workbenchReady){throw 'Docker Linux engine did not become ready.'}
    }
    if($Action -eq 'Initialize'){
        & $workbenchDocker @workbenchArgs build api
        if($LASTEXITCODE -ne 0){throw 'API image build failed.'}
        & $workbenchDocker @workbenchArgs up -d --wait db
        if($LASTEXITCODE -ne 0){throw 'Database readiness failed.'}
        & $workbenchDocker @workbenchArgs run --rm --no-deps api python -m app.bootstrap
        if($LASTEXITCODE -ne 0){throw 'Schema initialization failed; inspect output without deleting the data volume.'}
        Write-Output 'OntoDataWorks database initialized. Start with or without the model runtime.'
        exit 0
    }
    if(-not $WithoutModel){
        & (Join-Path $PSScriptRoot 'scripts\Start-CodexBridge.ps1')
        if($LASTEXITCODE -ne 0){throw 'Codex bridge startup failed.'}
    }
    if($Action -eq 'Restart'){
        # Stop dependents before the database; up waits for database/API health.
        & $workbenchDocker @workbenchArgs stop
        if($LASTEXITCODE -ne 0){throw 'Ordered service stop failed.'}
    }
    & $workbenchDocker @workbenchArgs up -d --wait --wait-timeout 180
    if($LASTEXITCODE -ne 0){throw 'Independent workbench startup failed. Retain volumes and inspect logs.'}
    $workbenchHealth=Invoke-RestMethod -Uri 'http://127.0.0.1:19090/api/health' -TimeoutSec 10
    if($workbenchHealth.status -ne 'ok'){throw 'API readiness check failed.'}
    if(-not $WithoutModel){Test-WorkbenchModels}
    Write-Output 'OntoDataWorks ready: http://127.0.0.1:19090'
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
    if(-not $WithoutModel){Test-WorkbenchModels}
    Write-Output 'Selected frontend/API/database/model readiness checks passed; full feature acceptance is separate.'
}
