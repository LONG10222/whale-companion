param([string]$Root=$PSScriptRoot)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath($Root)
$taskDirectory=Join-Path $taskRoot 'logs/runtime'
New-Item -ItemType Directory -Path $taskDirectory -Force | Out-Null
$taskControl=Join-Path $taskRoot 'configs/runtime-control.local.json'
$taskStatus=Join-Path $taskDirectory 'host-status.json'
$taskRetryFile=Join-Path $taskDirectory 'host-retry.json'
$taskEvents=Join-Path $taskDirectory 'host-events.jsonl'
function Write-HostEvent([string]$Event,$Values){
    if((Test-Path -LiteralPath $taskEvents) -and (Get-Item -LiteralPath $taskEvents).Length-gt2MB){
        for($taskIndex=3;$taskIndex-ge1;$taskIndex--){
            $taskSource=if($taskIndex-eq1){$taskEvents}else{$taskEvents+'.'+($taskIndex-1)}
            if(Test-Path -LiteralPath $taskSource){Move-Item -LiteralPath $taskSource -Destination ($taskEvents+'.'+$taskIndex) -Force}
        }
    }
    @{at=[DateTime]::UtcNow.ToString('o');event=$Event;values=$Values} | ConvertTo-Json -Depth 4 -Compress | Add-Content -LiteralPath $taskEvents -Encoding UTF8
}
function Test-RequestedStop {
    if(-not(Test-Path -LiteralPath $taskControl)){return $false}
    return (Get-Content -LiteralPath $taskControl -Raw -Encoding UTF8 | ConvertFrom-Json).desired-eq'stopped'
}
function Write-HostState([string]$State,[int]$ChildPid){
    @{state=$State;pid=$PID;child_pid=$ChildPid;checked_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath ($taskStatus+'.tmp') -Encoding UTF8
    Move-Item -LiteralPath ($taskStatus+'.tmp') -Destination $taskStatus -Force
}
try{
    Write-HostEvent 'host_started' @{pid=$PID}
    $taskConfig=Get-Content -LiteralPath (Join-Path $taskRoot 'settings.local.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $taskPython=[string]$taskConfig.python
    if(-not[IO.Path]::IsPathRooted($taskPython)){$taskPython=Join-Path $taskRoot $taskPython}
    $taskAttempts=@()
    if(Test-Path -LiteralPath $taskRetryFile){
        $taskSaved=Get-Content -LiteralPath $taskRetryFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if($taskSaved.version-eq2){$taskAttempts=@($taskSaved.failures | ForEach-Object {[DateTime]::Parse($_).ToUniversalTime()})}
    }
    while(-not(Test-RequestedStop)){
        $taskNow=[DateTime]::UtcNow
        $taskAttempts=@($taskAttempts | Where-Object {($taskNow-$_).TotalSeconds-lt900})
        if($taskAttempts.Count-ge3){Write-HostState 'restart_cooldown' 0;Start-Sleep -Seconds 5;continue}
        $taskArgs='-X utf8 "'+(Join-Path $taskRoot 'runtime_supervisor.py')+'" --root "'+$taskRoot+'"'
        $taskChild=Start-Process -FilePath $taskPython -ArgumentList $taskArgs -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskDirectory 'supervisor.stdout.log') -RedirectStandardError (Join-Path $taskDirectory 'supervisor.stderr.log')
        # Keep a process handle before exit; otherwise PowerShell can lose ExitCode.
        $null=$taskChild.Handle
        Write-HostEvent 'supervisor_started' @{pid=$taskChild.Id}
        while(-not$taskChild.WaitForExit(5000)){Write-HostState 'running' $taskChild.Id}
        $taskChild.Refresh()
        Write-HostEvent 'supervisor_exited' @{pid=$taskChild.Id;exit_code=$taskChild.ExitCode}
        $taskChild.Dispose()
        if(Test-RequestedStop){break}
        $taskAttempts+=@([DateTime]::UtcNow)
        @{version=2;failures=@($taskAttempts | ForEach-Object {$_.ToString('o')})} | ConvertTo-Json | Set-Content -LiteralPath ($taskRetryFile+'.tmp') -Encoding UTF8
        Move-Item -LiteralPath ($taskRetryFile+'.tmp') -Destination $taskRetryFile -Force
        Write-HostState 'restart_wait' 0
        for($taskWait=0;$taskWait-lt10 -and -not(Test-RequestedStop);$taskWait++){Start-Sleep -Seconds 1}
    }
    Write-HostState 'stopped' 0
    Write-HostEvent 'host_stopped' @{}
}catch{
    # Do not log configuration or raw exception messages containing local credentials.
    Write-HostEvent 'host_failed' @{error_type=$_.Exception.GetType().Name}
    Write-HostState 'failed' 0
    exit 1
}
