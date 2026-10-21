$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskConfig = Get-Content -Raw -LiteralPath 'settings.local.json' | ConvertFrom-Json
try {
    $taskHealth = Invoke-RestMethod -Uri ('http://127.0.0.1:' + $taskConfig.port + '/health') -TimeoutSec 2
    if ($taskHealth.status -eq 'ok' -and $null -ne $taskHealth.channels) {
        Write-Host ('Local voice service is already running: http://127.0.0.1:' + $taskConfig.port)
        exit 0
    }
} catch { }
if (-not (Test-Path -LiteralPath $taskConfig.python)) { throw 'Local runtime is missing. See README.md.' }
& $taskConfig.python (Join-Path $PSScriptRoot 'voice_server.py') --config (Join-Path $PSScriptRoot 'settings.local.json')
