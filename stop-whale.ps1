param([switch]$StopServices)
$ErrorActionPreference='Stop'
$taskSettings=Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $PSScriptRoot 'settings.local.json') | ConvertFrom-Json
$taskPython=$taskSettings.python
if(-not[IO.Path]::IsPathRooted($taskPython)){$taskPython=Join-Path $PSScriptRoot $taskPython}
$taskArgs=@('-X','utf8',(Join-Path $PSScriptRoot 'runtime_supervisor.py'),'--root',$PSScriptRoot,'--stop')
if($StopServices){$taskArgs+='--stop-services'}
& $taskPython @taskArgs
if($LASTEXITCODE-ne0){throw '未能写入后台停止请求。'}
Write-Output $(if($StopServices){'正在停止本安装的后台服务；有任务时会等待，日常QQ不会关闭。'}else{'后台自动恢复已暂停；已有程序继续运行。双击一键启动可恢复。'})
