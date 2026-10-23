param([switch]$EnsureOnly)
$ErrorActionPreference='Stop'
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'data/setup/installation.local.json')) {
  & (Join-Path $PSScriptRoot 'scripts/bootstrap.ps1') -StartOnly -NoBrowser -NoShortcut
  exit $LASTEXITCODE
}

. (Join-Path $PSScriptRoot 'runtime-task.ps1')
$taskResult=Start-WhaleRuntime -Root $PSScriptRoot -EnsureOnly:$EnsureOnly
if($taskResult-eq'paused'){Write-Output '后台自动恢复已暂停；双击桌面一键启动可恢复。'}
else{Write-Output '后台已独立运行：http://127.0.0.1:18766/qq'}
