$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
$taskSettings=Get-Content -Raw -LiteralPath (Join-Path $taskRoot 'settings.local.json') | ConvertFrom-Json
$taskBotRuntime=Get-Content -Raw -LiteralPath (Join-Path $taskRoot 'bot-runtime.local.json') | ConvertFrom-Json
$taskVoicePython=$taskSettings.python
if (-not [System.IO.Path]::IsPathRooted($taskVoicePython)) {$taskVoicePython=Join-Path $taskRoot $taskVoicePython}
$taskLogs=Join-Path $taskRoot 'logs/qq'
New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
function Test-TaskPort([int]$Port) {
  $taskSocket=New-Object System.Net.Sockets.TcpClient
  try {$taskSocket.Connect('127.0.0.1',$Port);return $true} catch {return $false} finally {$taskSocket.Dispose()}
}
if (-not (Test-TaskPort 18765)) {
  Start-Process -FilePath $taskVoicePython -ArgumentList @('"'+(Join-Path $taskRoot 'voice_server.py')+'"','--config','"'+(Join-Path $taskRoot 'settings.local.json')+'"') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLogs 'voice.out.log') -RedirectStandardError (Join-Path $taskLogs 'voice.err.log')
}
if (-not (Test-TaskPort 18766)) {
  Start-Process -FilePath $taskVoicePython -ArgumentList @('-m','uvicorn','phone_server:app','--host','127.0.0.1','--port','18766','--no-access-log') -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLogs 'companion.out.log') -RedirectStandardError (Join-Path $taskLogs 'companion.err.log')
}
$taskClientConfigPath=Join-Path $taskRoot 'configs/qq-client.local.json'
if (Test-Path -LiteralPath $taskClientConfigPath) {
  $taskClient=Get-Content -Raw -LiteralPath $taskClientConfigPath | ConvertFrom-Json
  $taskOwnClient=Get-CimInstance Win32_Process -Filter "Name='QQ.exe'" | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($taskClient.client_root+'\',[System.StringComparison]::OrdinalIgnoreCase) }
  if (-not $taskOwnClient) {
    $env:DEEPSEEK_QQ_DATA_DIR=$taskClient.qq_data
    try {
      Start-Process -FilePath $taskClient.boot -WorkingDirectory $taskClient.client_root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLogs 'napcat.out.log') -RedirectStandardError (Join-Path $taskLogs 'napcat.err.log')
    } finally {Remove-Item Env:DEEPSEEK_QQ_DATA_DIR}
  }
}
Write-Host 'QQ page: http://127.0.0.1:18766/qq'
