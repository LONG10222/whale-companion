param([switch]$CheckOnly,[switch]$NoOpen,[switch]$NoGui,[switch]$LoadFunctionsOnly,[switch]$ResumeOnly)
$ErrorActionPreference='Stop'
$script:TaskRoot=$PSScriptRoot
$script:LauncherSteps=New-Object System.Collections.Generic.List[object]
function Get-LauncherConfig {
    $taskFile=Join-Path $script:TaskRoot 'configs/launcher.local.json'
    if(-not(Test-Path -LiteralPath $taskFile)){throw '请先配置 configs/launcher.local.json。'}
    return Get-Content -Raw -Encoding UTF8 -LiteralPath $taskFile | ConvertFrom-Json
}
function Add-LaunchStep([string]$Name,[string]$State,[string]$Detail){
    $script:LauncherSteps.Add([pscustomobject]@{at=[DateTime]::UtcNow.ToString('o');name=$Name;state=$State;detail=$Detail})
    if($script:StatusList){$script:StatusList.Items.Add(($Name+'：'+$Detail))|Out-Null;$script:StatusList.TopIndex=$script:StatusList.Items.Count-1;[Windows.Forms.Application]::DoEvents()}
}
function Get-RunningClash([string]$Path){
    $taskFullPath=[IO.Path]::GetFullPath($Path)
    return @(Get-CimInstance Win32_Process -Filter "Name='clash-verge.exe'" | Where-Object {$_.ExecutablePath -and [string]::Equals($_.ExecutablePath,$taskFullPath,[StringComparison]::OrdinalIgnoreCase)})
}
function Get-RunningChatGPT([string]$PackagePrefix){
    return @(Get-CimInstance Win32_Process -Filter "Name='ChatGPT.exe'" | Where-Object {$_.ExecutablePath -and $_.ExecutablePath.IndexOf($PackagePrefix,[StringComparison]::OrdinalIgnoreCase)-ge 0})
}
function Test-GoogleConnection([string]$Url){
    Add-Type -AssemblyName System.Net.Http
    $taskHandler=New-Object System.Net.Http.HttpClientHandler
    $taskHandler.UseProxy=$true;$taskHandler.Proxy=[System.Net.WebRequest]::GetSystemWebProxy();$taskHandler.AllowAutoRedirect=$false
    $taskClient=New-Object System.Net.Http.HttpClient($taskHandler);$taskClient.Timeout=[TimeSpan]::FromSeconds(8)
    try{
        $taskReply=$taskClient.GetAsync($Url).GetAwaiter().GetResult()
        try{return [int]$taskReply.StatusCode -eq 204}finally{$taskReply.Dispose()}
    }catch{return $false}finally{$taskClient.Dispose();$taskHandler.Dispose()}
}
function Test-LocalService([int]$Port,[string]$Token){
    $taskSocket=New-Object System.Net.Sockets.TcpClient
    try{$taskSocket.Connect('127.0.0.1',$Port)}catch{return 'missing'}finally{$taskSocket.Dispose()}
    try{
        $taskHeaders=@{Authorization='Bearer '+$Token}
        $taskEndpoint=if($Port-eq18765){'/health'}else{'/qq/status'}
        $taskReply=Invoke-RestMethod -Uri ('http://127.0.0.1:'+$Port+$taskEndpoint) -Headers $taskHeaders -TimeoutSec 5 -UseBasicParsing
        if($Port-eq18765 -and $taskReply.status-eq'ok' -and $null-ne$taskReply.worker_loaded){return 'running'}
        if($Port-eq18766 -and $null-ne$taskReply.allowed_private_ids -and $null-ne$taskReply.groups_enabled){return 'running'}
    }catch{}
    return 'occupied'
}
function Test-RuntimeReady {
    try{
        foreach($taskName in @('host-status.json','status.json')){
            $taskStatus=Get-Content -LiteralPath (Join-Path $script:TaskRoot ('logs/runtime/'+$taskName)) -Raw -Encoding UTF8 | ConvertFrom-Json
            if($taskStatus.state-ne'running'){return $false}
            $taskAt=if($taskStatus.checked_at-is[DateTime]){$taskStatus.checked_at.ToUniversalTime()}else{[DateTimeOffset]::Parse([string]$taskStatus.checked_at,[Globalization.CultureInfo]::InvariantCulture).UtcDateTime}
            if(([DateTime]::UtcNow-$taskAt).TotalSeconds-gt20){return $false}
            if(-not(Get-Process -Id $taskStatus.pid -ErrorAction SilentlyContinue)){return $false}
        }
        return $true
    }catch{return $false}
}
function Invoke-WhaleStartup($Config){
    $taskClash=@(Get-RunningClash $Config.clash_exe)
    if($taskClash.Count){Add-LaunchStep 'Clash' 'skipped' '已运行，跳过启动。'}
    elseif($CheckOnly){throw 'Clash未运行；检查模式不会启动程序。'}
    else{
        if(-not(Test-Path -LiteralPath $Config.clash_exe)){throw '找不到Clash，请检查启动配置中的软件位置。'}
        Start-Process -FilePath $Config.clash_exe -WindowStyle Hidden
        Add-LaunchStep 'Clash' 'started' '已启动，正在等待联网。'
    }
    $taskConnected=$false;$taskUntil=[DateTime]::UtcNow.AddSeconds([int]$Config.network_wait_seconds)
    do{
        if(Test-GoogleConnection $Config.google_probe_url){$taskConnected=$true;break}
        if($CheckOnly){break}
        if($script:StatusList){[Windows.Forms.Application]::DoEvents()}
        Start-Sleep -Milliseconds 1500
    }while([DateTime]::UtcNow-lt$taskUntil)
    if(-not$taskConnected){throw 'Google尚未接通。请在Clash中检查当前节点或系统代理，再双击重试；尚未启动后续程序。'}
    Add-LaunchStep '联网检查' 'ready' 'Google HTTPS连接已通过。'
    $taskApp=@(Get-RunningChatGPT $Config.chatgpt_package_prefix)
    if($taskApp.Count){Add-LaunchStep 'ChatGPT' 'skipped' '已运行，跳过启动。'}
    elseif($CheckOnly){throw 'ChatGPT未运行；检查模式不会启动程序。'}
    else{
        Start-Process -FilePath 'explorer.exe' -ArgumentList ('shell:AppsFolder\'+$Config.chatgpt_app_id) -WindowStyle Hidden
        $taskReady=$false
        for($taskTry=0;$taskTry-lt30;$taskTry++){if(@(Get-RunningChatGPT $Config.chatgpt_package_prefix).Count){$taskReady=$true;break};Start-Sleep -Milliseconds 500;if($script:StatusList){[Windows.Forms.Application]::DoEvents()}}
        if(-not$taskReady){throw 'ChatGPT尚未启动，请检查应用安装状态。'}
        Add-LaunchStep 'ChatGPT' 'started' '应用已启动。'
    }
    $taskSettings=Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $script:TaskRoot 'settings.local.json') | ConvertFrom-Json
    foreach($taskPort in @(18765,18766)){
        $taskState=Test-LocalService $taskPort $taskSettings.api_token
        if($taskState-eq'occupied'){throw ('本机端口'+$taskPort+'被其他程序占用，未启动重复服务。')}
        if($taskState-eq'running'){Add-LaunchStep ('本地服务 '+$taskPort) 'skipped' '已就绪，跳过启动。'}
        elseif($CheckOnly){throw ('本地服务'+$taskPort+'未运行。')}
        else{Add-LaunchStep ('本地服务 '+$taskPort) 'pending' '准备启动。'}
    }
    if(-not$CheckOnly){
        & (Join-Path $script:TaskRoot 'start-qq.ps1') -EnsureOnly:$ResumeOnly | Out-Null
        Add-LaunchStep '后台维护' 'pending' '已交给Windows独立运行，正在核对后台心跳。'
    }
    foreach($taskPort in @(18765,18766)){
        $taskReady=$false
        for($taskTry=0;$taskTry-lt45;$taskTry++){if((Test-LocalService $taskPort $taskSettings.api_token)-eq'running'){$taskReady=$true;break};Start-Sleep -Milliseconds 500;if($script:StatusList){[Windows.Forms.Application]::DoEvents()}}
        if(-not$taskReady){throw ('本地服务'+$taskPort+'未就绪，请查看本机启动日志。')}
    }
    $taskRuntimeReady=$false
    for($taskTry=0;$taskTry-lt40;$taskTry++){
        if(Test-RuntimeReady){$taskRuntimeReady=$true;break}
        if($CheckOnly){break}
        Start-Sleep -Milliseconds 500
        if($script:StatusList){[Windows.Forms.Application]::DoEvents()}
    }
    if(-not$taskRuntimeReady){throw '网页服务仍可使用，但后台维护未就绪或已进入故障等待；请查看本机 logs/runtime 记录。'}
    Add-LaunchStep '后台维护' 'ready' '独立后台心跳正常；关闭此窗口不影响后台。'
    Add-LaunchStep '机器人' 'ready' '本地程序已就绪，QQ会自动恢复连接；必要时在页面扫码。'
    if(-not$NoOpen -and -not$CheckOnly){Start-Process -FilePath 'explorer.exe' -ArgumentList $Config.control_url -WindowStyle Hidden}
}
if($LoadFunctionsOnly){return}
if($ResumeOnly){
    $resumeDesired=Join-Path $script:TaskRoot 'configs/runtime-control.local.json'
    if(-not(Test-Path -LiteralPath $resumeDesired) -or (Get-Content -LiteralPath $resumeDesired -Raw -Encoding UTF8 | ConvertFrom-Json).desired-ne'running'){return}
}
$script:StatusList=$null;$taskForm=$null;$taskLocked=$false;$taskMutex=New-Object Threading.Mutex($false,'Local\WhaleCompanionLauncherV1')
try{
    $taskLocked=$taskMutex.WaitOne(0)
    if(-not$taskLocked){return}
    if(-not$NoGui -and -not$CheckOnly){
        Add-Type -AssemblyName System.Windows.Forms
        $taskForm=New-Object Windows.Forms.Form;$taskForm.Text='鲸娅 · 一键启动';$taskForm.Width=620;$taskForm.Height=350;$taskForm.StartPosition='CenterScreen'
        $taskLabel=New-Object Windows.Forms.Label;$taskLabel.Text='正在检查Clash、联网状态和机器人程序……';$taskLabel.AutoSize=$true;$taskLabel.Left=20;$taskLabel.Top=20;$taskForm.Controls.Add($taskLabel)
        $script:StatusList=New-Object Windows.Forms.ListBox;$script:StatusList.Left=20;$script:StatusList.Top=55;$script:StatusList.Width=560;$script:StatusList.Height=210;$taskForm.Controls.Add($script:StatusList)
        $taskForm.Show();[Windows.Forms.Application]::DoEvents()
    }
    Invoke-WhaleStartup (Get-LauncherConfig)
    if($taskLabel){$taskLabel.Text='启动完成。已运行的程序都已跳过。';[Windows.Forms.Application]::DoEvents();Start-Sleep -Milliseconds 1800}
}catch{
    Add-LaunchStep '启动' 'failed' $_.Exception.Message
    if($taskForm){$taskLabel.Text='尚未全部启动，请查看原因。';[Windows.Forms.Application]::Run($taskForm)}
    if($CheckOnly -or $NoGui){throw}
}finally{
    $taskLog=Join-Path $script:TaskRoot 'logs/launcher';New-Item -ItemType Directory -Path $taskLog -Force | Out-Null
    $script:LauncherSteps | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $taskLog 'latest.json') -Encoding UTF8
    if($taskForm){$taskForm.Dispose()}
    if($taskLocked){$taskMutex.ReleaseMutex()};$taskMutex.Dispose()
}
if($CheckOnly){$script:LauncherSteps|ConvertTo-Json -Depth 4}
