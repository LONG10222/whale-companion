$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$taskRuntime = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'bot-runtime.local.json') | ConvertFrom-Json
& $taskRuntime.executable run --directory $taskRuntime.instance
