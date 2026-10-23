# Keep the runtime independent of the temporary launcher and terminal.
$ErrorActionPreference='Stop'
function Get-WhaleRuntimeTaskName([string]$Root) {
    $taskHash=[Security.Cryptography.SHA256]::Create()
    try{$taskBytes=$taskHash.ComputeHash([Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($Root).ToLowerInvariant()))}
    finally{$taskHash.Dispose()}
    return 'WhaleCompanion-Runtime-'+([BitConverter]::ToString($taskBytes).Replace('-','').Substring(0,12).ToLowerInvariant())
}
function Start-WhaleRuntime([string]$Root,[switch]$EnsureOnly) {
    $taskRoot=[IO.Path]::GetFullPath($Root)
    $taskName=Get-WhaleRuntimeTaskName $taskRoot
    $taskSettings=Get-Content -LiteralPath (Join-Path $taskRoot 'settings.local.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $taskPython=[string]$taskSettings.python
    if(-not[IO.Path]::IsPathRooted($taskPython)){$taskPython=Join-Path $taskRoot $taskPython}
    $taskPythonw=Join-Path (Split-Path -Parent $taskPython) 'pythonw.exe'
    $taskSupervisor=Join-Path $taskRoot 'runtime_supervisor.py'
    if(-not(Test-Path -LiteralPath $taskPythonw -PathType Leaf)){throw '缺少后台Python运行程序。'}
    if(-not(Test-Path -LiteralPath $taskSupervisor -PathType Leaf)){throw '缺少后台维护程序。'}
    $taskControl=Join-Path $taskRoot 'configs/runtime-control.local.json'
    if($EnsureOnly -and (Test-Path -LiteralPath $taskControl)){
        $taskDesired=Get-Content -LiteralPath $taskControl -Raw -Encoding UTF8 | ConvertFrom-Json
        if($taskDesired.desired-eq'stopped'){return 'paused'}
    }
    $taskLegacyArguments='-X utf8 "'+$taskSupervisor+'" --root "'+$taskRoot+'"'
    $taskHost=Join-Path $taskRoot 'runtime-host.ps1'
    if(-not(Test-Path -LiteralPath $taskHost -PathType Leaf)){throw '缺少独立后台宿主。'}
    $taskExecutable=Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
    $taskArguments='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$taskHost+'" -Root "'+$taskRoot+'"'
    $taskExisting=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $taskReplace=$false
    if($taskExisting){
        $taskActions=@($taskExisting.Actions)
        if($taskActions.Count-ne1){throw '同名后台任务属于其他配置，未覆盖。'}
        $taskMatches=[string]::Equals($taskActions[0].Execute,$taskExecutable,[StringComparison]::OrdinalIgnoreCase) -and $taskActions[0].Arguments-eq$taskArguments
        if(-not$taskMatches){
            $taskLegacy=[string]::Equals($taskActions[0].Execute,$taskPythonw,[StringComparison]::OrdinalIgnoreCase) -and $taskActions[0].Arguments-eq$taskLegacyArguments
            if(-not$taskLegacy){throw '同名后台任务属于其他配置，未覆盖。'}
            if($taskExisting.State-eq'Running'){throw '请先暂停旧版后台维护，再更新后台宿主；QQ程序无需退出。'}
            $taskReplace=$true
        }
    }
    if(-not$taskExisting -or $taskReplace){
        # On demand: preserve Clash -> Google -> ChatGPT in launch-whale.ps1.
        $taskAction=New-ScheduledTaskAction -Execute $taskExecutable -Argument $taskArguments -WorkingDirectory $taskRoot
        $taskPrincipal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
        $taskPolicy=New-ScheduledTaskSettingsSet -Hidden -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
        Register-ScheduledTask -TaskName $taskName -Action $taskAction -Principal $taskPrincipal -Settings $taskPolicy -Description '鲸娅本地后台。仅按需启动；维护本安装的服务，不管理日常QQ。' -Force | Out-Null
    }
    if(-not$EnsureOnly){
        @{desired='running';at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath ($taskControl+'.tmp') -Encoding UTF8
        Move-Item -LiteralPath ($taskControl+'.tmp') -Destination $taskControl -Force
    }
    if($taskExisting -and $taskExisting.State-eq'Running'){return 'running'}
    Start-ScheduledTask -TaskName $taskName
    return 'started'
}
