#requires -Version 5.1
[CmdletBinding()]
param([switch]$StartOnly, [switch]$CheckOnly, [switch]$NoBrowser, [switch]$NoShortcut)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$env:PYTHONUTF8 = '1'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskRuntime = Join-Path $taskRoot '.runtime'
$taskCache = Join-Path $taskRoot '.installer-cache'
$taskHashProvider = [Security.Cryptography.SHA256]::Create()
try { $taskKey = ([BitConverter]::ToString($taskHashProvider.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskRoot.ToLowerInvariant())))).Replace('-','').Substring(0,12) }
finally { $taskHashProvider.Dispose() }
$taskMutex = New-Object Threading.Mutex($false, ('Local\WhaleSetup-' + $taskKey))
$taskAcquired = $false

function Assert-ChildPath([string]$Candidate, [string]$Parent) {
  $taskFull = [IO.Path]::GetFullPath($Candidate)
  $taskPrefix = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
  if (-not $taskFull.StartsWith($taskPrefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'Setup path is outside this installation.' }
  # A junction in any existing parent would redirect writes outside our directory.
  $taskPart = $taskFull
  while ($taskPart -and $taskPart.Length -ge $taskPrefix.Length) {
    if (Test-Path -LiteralPath $taskPart) {
      $taskItem = Get-Item -LiteralPath $taskPart -Force
      if (($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Setup directory contains a directory link. Choose an ordinary folder.' }
    }
    $taskPart = [IO.Path]::GetDirectoryName($taskPart)
  }
  return $taskFull
}

function Get-VerifiedFile($Artifact) {
  $taskUri = [Uri]$Artifact.url
  if ($taskUri.Scheme -ne 'https' -or $taskUri.Host -notin @('www.python.org','github.com')) { throw 'Unapproved download origin.' }
  if ($Artifact.sha256 -notmatch '^[a-f0-9]{64}$') { throw 'Download checksum is missing.' }
  $taskTarget = Assert-ChildPath (Join-Path $taskCache ($Artifact.sha256 + '.zip')) $taskRoot
  if (Test-Path -LiteralPath $taskTarget) {
    if ((Get-FileHash -LiteralPath $taskTarget -Algorithm SHA256).Hash.ToLowerInvariant() -eq $Artifact.sha256) { return $taskTarget }
    throw 'A previously verified archive was changed. Move that archive out of .installer-cache and retry.'
  }
  $taskPartial = $taskTarget + '.partial'
  for ($taskAttempt = 1; $taskAttempt -le 3; $taskAttempt++) {
    Write-Host ('Downloading ' + $Artifact.version + ' (attempt ' + $taskAttempt + '/3)...')
    $taskCurl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($taskCurl) {
      & $taskCurl.Source --fail --location --proto '=https' --proto-redir '=https' --connect-timeout 30 --max-time 1800 --retry 2 --continue-at - --output $taskPartial $Artifact.url
      $taskSuccess = $LASTEXITCODE -eq 0
    } else {
      try { Invoke-WebRequest -UseBasicParsing -Uri $Artifact.url -OutFile $taskPartial -TimeoutSec 1800; $taskSuccess = $true } catch { $taskSuccess = $false }
    }
    if ($taskSuccess) { break }
    if ($taskAttempt -eq 3) { throw 'Download interrupted. Run Install.cmd again to resume. Check your network or system proxy.' }
    Start-Sleep -Seconds 2
  }
  if ((Get-Item -LiteralPath $taskPartial).Length -ne [long]$Artifact.bytes -or (Get-FileHash -LiteralPath $taskPartial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Artifact.sha256) {
    # Retain a mismatching download for diagnosis; never run or append to it again.
    Move-Item -LiteralPath $taskPartial -Destination ($taskPartial + '.invalid-' + [Guid]::NewGuid().ToString('N'))
    throw 'Download verification failed. No downloaded program was executed. Retry Install.cmd.'
  }
  Move-Item -LiteralPath $taskPartial -Destination $taskTarget
  return $taskTarget
}

function Expand-VerifiedArchive([string]$Archive, [string]$Destination) {
  $taskDestination = Assert-ChildPath $Destination $taskRoot
  $taskStage = Assert-ChildPath ($taskDestination + '.staging-' + [Guid]::NewGuid().ToString('N')) $taskRoot
  New-Item -ItemType Directory -Path $taskStage -Force | Out-Null
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $taskZip = [IO.Compression.ZipFile]::OpenRead($Archive)
  try {
    $taskTotal = [long]0
    foreach ($taskEntry in $taskZip.Entries) {
      $taskTotal += $taskEntry.Length
      if ($taskTotal -gt 1500MB -or $taskEntry.FullName.Contains(':') -or $taskEntry.FullName -match '(^|[/\\])\.\.([/\\]|$)') { throw 'Archive layout is not accepted.' }
      if ((($taskEntry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw 'Archive contains a symbolic link.' }
      $taskOut = Assert-ChildPath (Join-Path $taskStage $taskEntry.FullName) $taskStage
      if ($taskEntry.Name -eq '') { New-Item -ItemType Directory -Path $taskOut -Force | Out-Null; continue }
      New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($taskOut)) -Force | Out-Null
      [IO.Compression.ZipFileExtensions]::ExtractToFile($taskEntry,$taskOut,$false)
    }
  } finally { $taskZip.Dispose() }
  if (Test-Path -LiteralPath $taskDestination) { throw 'An existing runtime is incomplete. Keep its data and move only the incomplete runtime folder before retrying.' }
  Move-Item -LiteralPath $taskStage -Destination $taskDestination
}

function Start-SetupHost([string]$Python) {
  $taskName = 'WhaleCompanion-Setup-' + $taskKey
  $taskScript = Join-Path $taskRoot 'scripts\beginner_setup.py'
  $taskArgs = '-X utf8 "' + $taskScript + '" --root "' + $taskRoot + '"'
  $taskAction = New-ScheduledTaskAction -Execute $Python -Argument $taskArgs -WorkingDirectory $taskRoot
  $taskPrincipal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
  $taskOptions = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
  $taskExisting = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  if ($taskExisting) {
    $taskActual = @($taskExisting.Actions)[0]
    if ($taskActual.Arguments -ne $taskArgs -or -not $taskActual.Execute.StartsWith($taskRoot.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($taskActual.Execute) -ne 'pythonw.exe') { throw 'An unrelated task already uses this setup name. Setup has left it unchanged.' }
    if ($taskActual.Execute -ne $Python) {
      if ($taskExisting.State -eq 'Running') { throw 'The previous setup host is still running; waiting for the upgrade handoff.' }
      Set-ScheduledTask -TaskName $taskName -Action $taskAction | Out-Null
    }
    if ($taskExisting.State -ne 'Running') { Start-ScheduledTask -TaskName $taskName }
  } else {
    Register-ScheduledTask -TaskName $taskName -Action $taskAction -Principal $taskPrincipal -Settings $taskOptions -Description 'Whale Companion local setup and dedicated runtime; starts only on request.' | Out-Null
    Start-ScheduledTask -TaskName $taskName
  }
}

try {
  if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'This installer currently supports Windows 10/11 x64 only.' }
  $taskAcquired = $taskMutex.WaitOne(0)
  if (-not $taskAcquired) { Write-Host 'Setup is already running for this installation.'; exit 0 }
  if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'phone_server.py'))) { throw 'Extract the complete release folder before running Install.cmd.' }
  $taskManifest = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $taskRoot 'configs\distribution.json') | ConvertFrom-Json
  $taskPython = Join-Path $taskRuntime 'python\python.exe'
  $taskVenvPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
  $taskEnvironmentFile = Join-Path $taskRoot 'data\setup\python-environment.local.json'
  if (Test-Path -LiteralPath $taskEnvironmentFile) {
    $taskEnvironment = Get-Content -Raw -Encoding UTF8 -LiteralPath $taskEnvironmentFile | ConvertFrom-Json
    $taskVenvPython = Assert-ChildPath $taskEnvironment.python $taskRoot
    if ([IO.Path]::GetFileName($taskVenvPython) -ne 'python.exe' -or -not (Test-Path -LiteralPath $taskVenvPython)) { throw 'The upgraded environment is missing. Existing user data has been kept.' }
  }
  $taskQQNode = Join-Path $taskRuntime 'qq\node.exe'
  if ($CheckOnly) {
    [pscustomobject]@{ Platform='windows-x64'; PythonReady=(Test-Path -LiteralPath $taskPython); EnvironmentReady=(Test-Path -LiteralPath $taskVenvPython); QQRuntimeReady=(Test-Path -LiteralPath $taskQQNode); RuntimeDirectory=$taskRuntime } | ConvertTo-Json
    exit 0
  }
  $taskInstallMarker=Join-Path $taskRoot 'data\setup\installation.local.json'
  if (-not (Test-Path -LiteralPath $taskInstallMarker)) {
    foreach ($taskPersonal in @('settings.local.json','qq.local.json','agent.local.json','configs\qq-client.local.json')) {
      if (Test-Path -LiteralPath (Join-Path $taskRoot $taskPersonal)) {throw 'Existing personal installation detected. Use its original launcher or extract a clean release folder; no settings were adopted.'}
    }
    foreach ($taskStateDir in @('data\qq','data\qq-client')) {
      $taskStatePath=Join-Path $taskRoot $taskStateDir
      if ((Test-Path -LiteralPath $taskStatePath) -and (Get-ChildItem -LiteralPath $taskStatePath -Force | Select-Object -First 1)) {throw 'Existing account data detected. Keep it in place and use the original installation.'}
    }
  }
  foreach ($taskDir in @($taskRuntime,$taskCache,(Join-Path $taskRoot 'data'),(Join-Path $taskRoot 'logs'))) {
    [void](Assert-ChildPath $taskDir $taskRoot)
    New-Item -ItemType Directory -Path $taskDir -Force | Out-Null
  }
  # Credentials and account data are readable only by this Windows user and SYSTEM.
  foreach ($taskRelative in @('data','logs','.runtime','configs','cache','workspace','artifacts','assets\stickers')) {
    $taskDir=Assert-ChildPath (Join-Path $taskRoot $taskRelative) $taskRoot
    New-Item -ItemType Directory -Path $taskDir -Force | Out-Null
    $taskOwnerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $taskDir /inheritance:r /grant:r ('*'+$taskOwnerSid+':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict private data directory permissions.' }
  }
  if (-not (Test-Path -LiteralPath $taskPython)) {
    if ($StartOnly) { throw 'First run Install.cmd to prepare this computer.' }
    $taskArchive = Get-VerifiedFile $taskManifest.python
    Expand-VerifiedArchive $taskArchive (Join-Path $taskRuntime 'python')
  }
  if (-not (Test-Path -LiteralPath $taskVenvPython)) {
    Write-Host 'Preparing the private Python environment...'
    & $taskPython -m venv (Join-Path $taskRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the private environment.' }
  }
  $taskLock = Join-Path $taskRoot 'requirements-bootstrap.lock'
  $taskLockHash = (Get-FileHash -LiteralPath $taskLock -Algorithm SHA256).Hash
  $taskStamp = Join-Path $taskRuntime 'dependencies.sha256'
  if (-not (Test-Path -LiteralPath $taskStamp) -or (Get-Content -Raw -LiteralPath $taskStamp).Trim() -ne $taskLockHash) {
    Write-Host 'Installing verified application dependencies. No GPU model is downloaded...'
    & $taskVenvPython -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes --index-url https://pypi.org/simple -r $taskLock
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation did not finish. Run Install.cmd again; completed downloads are cached.' }
    Set-Content -LiteralPath $taskStamp -Value $taskLockHash -Encoding ASCII
  }
  if (-not (Test-Path -LiteralPath $taskQQNode)) {
    if ($StartOnly) { throw 'First run Install.cmd to prepare the dedicated QQ runtime.' }
    $taskArchive = Get-VerifiedFile $taskManifest.qq
    Expand-VerifiedArchive $taskArchive (Join-Path $taskRuntime 'qq')
  }
  & $taskVenvPython -X utf8 (Join-Path $taskRoot 'scripts\beginner_setup.py') --root $taskRoot --prepare
  if ($LASTEXITCODE -ne 0) { throw 'Could not prepare local settings. Existing configuration was kept.' }
  if(Test-Path -LiteralPath (Join-Path $taskRoot 'data/setup/update-progress.local.json')) {
    & $taskVenvPython -X utf8 (Join-Path $taskRoot 'scripts/recover-update.py') --root $taskRoot
    if($LASTEXITCODE) { throw 'Interrupted upgrade needs inspection; backups and current data are preserved.' }
  }
  Start-SetupHost ($taskVenvPython.Replace('python.exe','pythonw.exe'))
  $taskState = Join-Path $taskRoot 'data\setup\host.local.json'
  $taskReady = $null
  $taskConfirmed = $false
  for ($taskWait=0; $taskWait -lt 45; $taskWait++) {
    if (Test-Path -LiteralPath $taskState) {
      try {
        $taskReady = Get-Content -Raw -Encoding UTF8 -LiteralPath $taskState | ConvertFrom-Json
        $taskResponse = Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:' + $taskReady.port + '/health') -TimeoutSec 2
        $taskHealth = $taskResponse.Content | ConvertFrom-Json
        if ($taskHealth.instance -eq $taskReady.instance) { $taskConfirmed = $true; break }
      } catch { $taskReady = $null }
    }
    Start-Sleep -Seconds 1
  }
  if (-not $taskReady -or -not $taskConfirmed) { throw 'The local setup page did not become ready. Check logs/setup/ for details.' }
  $taskLink = 'http://127.0.0.1:' + $taskReady.port + '/#' + $taskReady.launch_token
  if (-not $NoBrowser) { Start-Process $taskLink }
  Write-Host ('Ready. Local setup page: http://127.0.0.1:' + $taskReady.port + '/')
  $taskDesktop = [Environment]::GetFolderPath('Desktop')
  if ($taskDesktop -and -not $NoShortcut) {
    $taskShell = New-Object -ComObject WScript.Shell
    $taskShortcutPath = Join-Path $taskDesktop 'Whale Companion.lnk'
    if (Test-Path -LiteralPath $taskShortcutPath) {
      $taskOldShortcut = $taskShell.CreateShortcut($taskShortcutPath)
      if ($taskOldShortcut.TargetPath -ne (Join-Path $taskRoot 'Start.cmd')) { $taskShortcutPath = Join-Path $taskDesktop ('Whale Companion ' + $taskKey + '.lnk') }
    }
    $taskShortcut = $taskShell.CreateShortcut($taskShortcutPath)
    $taskShortcut.TargetPath = Join-Path $taskRoot 'Start.cmd'
    $taskShortcut.WorkingDirectory = $taskRoot
    $taskShortcut.Description = 'Open Whale Companion; reuse existing services.'
    $taskShortcut.Save()
  }
} catch { Write-Host ('Setup stopped: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }
finally { if ($taskAcquired) { $taskMutex.ReleaseMutex() }; $taskMutex.Dispose() }
