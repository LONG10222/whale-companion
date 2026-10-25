param([string]$Root=$PSScriptRoot,[switch]$CheckOnly)
$ErrorActionPreference='Stop'
$resumeRoot=[IO.Path]::GetFullPath($Root)
$resumeControl=Join-Path $resumeRoot 'configs/runtime-control.local.json'
if(-not(Test-Path -LiteralPath $resumeControl)){return}
if((Get-Content -LiteralPath $resumeControl -Raw -Encoding UTF8 | ConvertFrom-Json).desired-ne'running'){return}
$resumeProgress=Join-Path $resumeRoot 'data/setup/update-progress.local.json'
if(Test-Path -LiteralPath $resumeProgress){
    $resumePhase=(Get-Content -LiteralPath $resumeProgress -Raw -Encoding UTF8 | ConvertFrom-Json).phase
    if($resumePhase-in@('switching','validating','rolling_back','recovery_required')){return}
}
$resumeStatus=Join-Path $resumeRoot 'logs/runtime/host-status.json'
if(Test-Path -LiteralPath $resumeStatus){
    $resumeState=Get-Content -LiteralPath $resumeStatus -Raw -Encoding UTF8 | ConvertFrom-Json
    $resumeAt=if($resumeState.checked_at-is[DateTime]){$resumeState.checked_at.ToUniversalTime()}else{[DateTimeOffset]::Parse([string]$resumeState.checked_at,[Globalization.CultureInfo]::InvariantCulture).UtcDateTime}
    if($resumeState.state-eq'running' -and ([DateTime]::UtcNow-$resumeAt).TotalSeconds-lt90 -and (Get-Process -Id $resumeState.pid -ErrorAction SilentlyContinue)){return}
}
if($CheckOnly){Write-Output 'Recovery eligible; check mode did not start services.';return}
if(Test-Path -LiteralPath (Join-Path $resumeRoot 'configs/launcher.local.json')){
    # Reuse the same Clash -> Google -> ChatGPT gate as manual desktop startup.
    & (Join-Path $resumeRoot 'launch-whale.ps1') -NoOpen -NoGui -ResumeOnly
}else{
    # Portable installations do not require unrelated desktop applications.
    & (Join-Path $resumeRoot 'scripts/bootstrap.ps1') -StartOnly -NoBrowser -NoShortcut
}
