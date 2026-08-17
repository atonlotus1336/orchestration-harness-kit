[CmdletBinding()]
param(
  [ValidateSet('status','scout','campaign','report','explain','promote')][string]$Command,
  [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments
)
$ErrorActionPreference='Stop'
$root='__WINDOWS_WORKSPACE__';$base=Join-Path $root 'scripts\autoexperiment';$planning=Join-Path $root '.planning\autoexperiment-control-plane'
switch($Command){
  'status'{
    $summaries=@(Get-ChildItem -LiteralPath $planning -Filter '*-SUMMARY.md' -File|Sort-Object Name)
    $schedule=Get-Content -LiteralPath (Join-Path $planning 'contracts\schedules.yaml') -Raw|ConvertFrom-Json
    $latest=if($summaries.Count){$summaries[-1].FullName}else{$null}
    [pscustomobject][ordered]@{schema_version='1.0.0';plans_completed=$summaries.Count;latest_summary=$latest;dispatch_activation=$schedule.activation;kill_switch=(Test-Path -LiteralPath ([string]$schedule.kill_switch_path));canonical_skill='__WINDOWS_WORKSPACE__\skills-shared\autoexperiment\SKILL.md';skill_visibility='scheduled-sandbox-audit'}|ConvertTo-Json -Depth 5
  }
  'explain'{[pscustomobject][ordered]@{policy=Join-Path $planning 'RESEARCH-POLICY.md';invariants=Join-Path $planning 'contracts\protected-invariants.yaml';source_tiers=Join-Path $planning 'contracts\source-tiers.yaml';storage='G-disk-not-google-drive';promotion='GSD-package-only'}|ConvertTo-Json}
  'scout'{& (Join-Path $base 'scout.ps1') @Arguments}
  'campaign'{& (Join-Path $base 'campaign.ps1') @Arguments}
  'report'{& (Join-Path $base 'report.ps1') @Arguments}
  'promote'{& (Join-Path $base 'promotion.ps1') @Arguments}
}
