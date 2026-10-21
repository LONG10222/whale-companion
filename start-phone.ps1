$ErrorActionPreference = 'Stop'
$taskKit = $PSScriptRoot
$taskPython = 'C:/Users/EXAMPLE/.codex/.chatgpt-projects/SET_YOUR_CODEX_PROJECT_ID/work/voice-runtime/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) {
    throw '手机助手运行环境不存在，请检查原项目目录。'
}
$taskRunning = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 18766 -State Listen -ErrorAction SilentlyContinue
if (-not $taskRunning) {
    Start-Process -FilePath $taskPython -ArgumentList @('-m','uvicorn','phone_server:app','--host','127.0.0.1','--port','18766','--no-access-log') -WorkingDirectory $taskKit -WindowStyle Hidden -RedirectStandardOutput "$taskKit/cache/phone-launch.stdout.log" -RedirectStandardError "$taskKit/cache/phone-launch.stderr.log"
}
Write-Output '手机助手：http://127.0.0.1:18766/mobile'
Write-Output '使用与语音测试页相同的本地访问令牌，也可在页面选择 settings.local.json 连接。'
