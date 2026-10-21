param([string]$Name='Whale Companion')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
$taskDesktop=[Environment]::GetFolderPath('Desktop')
$taskDestination=Join-Path $taskDesktop ($Name+'.lnk')
if(Test-Path -LiteralPath $taskDestination){throw 'A shortcut with this name already exists; use a different -Name.'}
$taskShell=New-Object -ComObject WScript.Shell
$taskShortcut=$taskShell.CreateShortcut($taskDestination)
$taskShortcut.TargetPath=Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe'
$taskShortcut.Arguments='-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "'+(Join-Path $taskRoot 'launch-whale.ps1')+'"'
$taskShortcut.WorkingDirectory=$taskRoot
$taskShortcut.IconLocation=(Join-Path $env:WINDIR 'System32/shell32.dll')+',137'
$taskShortcut.Description='Clash, Google connectivity, ChatGPT, then local services. Reuse running processes.'
$taskShortcut.Save()
Write-Output $taskDestination
