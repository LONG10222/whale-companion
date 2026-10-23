#requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$Root)
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath($Root)
$taskSettings=Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $taskRoot 'settings.local.json') | ConvertFrom-Json
$taskPython=[IO.Path]::GetFullPath($taskSettings.python)
if (-not $taskPython.StartsWith($taskRoot.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) {throw 'Updater environment must belong to this installation.'}
$taskHasher=[Security.Cryptography.SHA256]::Create()
try {$taskKey=([BitConverter]::ToString($taskHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskRoot.ToLowerInvariant())))).Replace('-','').Substring(0,12)} finally {$taskHasher.Dispose()}
$taskName='WhaleCompanion-Update-'+$taskKey
$taskArgs='-X utf8 "'+(Join-Path $taskRoot 'scripts\update_worker.py')+'" --root "'+$taskRoot+'"'
$taskAction=New-ScheduledTaskAction -Execute $taskPython -Argument $taskArgs -WorkingDirectory $taskRoot
$taskOld=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($taskOld) {
  $taskOldAction=@($taskOld.Actions)[0]
  if ($taskOldAction.Arguments -ne $taskArgs -or -not $taskOldAction.Execute.StartsWith($taskRoot.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) {throw 'Unrelated update task; left unchanged.'}
  if ($taskOld.State -eq 'Running') {exit 0}
  Set-ScheduledTask -TaskName $taskName -Action $taskAction | Out-Null
} else {
  $taskPrincipal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
  $taskOptions=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::FromMinutes(30)) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  Register-ScheduledTask -TaskName $taskName -Action $taskAction -Principal $taskPrincipal -Settings $taskOptions -Description 'Owner-requested Whale Companion update and automatic recovery.' | Out-Null
}
Start-ScheduledTask -TaskName $taskName
