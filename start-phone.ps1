$ErrorActionPreference = 'Stop'
$taskKit = $PSScriptRoot
$taskSettings = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $taskKit 'settings.local.json') | ConvertFrom-Json
$taskPython = $taskSettings.python
if (-not [System.IO.Path]::IsPathRooted($taskPython)) {$taskPython=Join-Path $taskKit $taskPython}
if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) {throw 'Configure the Python runtime with scripts/configure.py first.'}
New-Item -ItemType Directory -Path (Join-Path $taskKit 'cache') -Force | Out-Null
$taskRunning = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 18766 -State Listen -ErrorAction SilentlyContinue
if (-not $taskRunning) {
    Start-Process -FilePath $taskPython -ArgumentList @('-m','uvicorn','phone_server:app','--host','127.0.0.1','--port','18766','--no-access-log') -WorkingDirectory $taskKit -WindowStyle Hidden -RedirectStandardOutput "$taskKit/cache/phone-launch.stdout.log" -RedirectStandardError "$taskKit/cache/phone-launch.stderr.log"
}
Write-Output '手机助手：http://127.0.0.1:18766/mobile'
Write-Output 'The local page connects automatically from this computer.'
