[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$CheckpointPath,
  [string]$SchedulesPath='__WINDOWS_WORKSPACE__\.planning\autoexperiment-control-plane\contracts\schedules.yaml',
  [string]$KillSwitchPath='',
  [switch]$Json
)
$ErrorActionPreference='Stop'
$s=Get-Content -LiteralPath $SchedulesPath -Raw|ConvertFrom-Json
$checkpoint=Get-Content -LiteralPath $CheckpointPath -Raw|ConvertFrom-Json
if(-not $KillSwitchPath){$KillSwitchPath=[string]$s.kill_switch_path}
$chain=@($s.chains|Where-Object{$_.id -eq $checkpoint.kind})|Select-Object -First 1
if(-not $chain){throw "No cadence chain for checkpoint kind: $($checkpoint.kind)"}
$reasons=New-Object System.Collections.Generic.List[string]
if($s.activation -notin @('manual_report_only','automatic_report_only','automatic_sandbox_audit')){$reasons.Add('activation-contract-invalid')}
if(Test-Path -LiteralPath $KillSwitchPath){$reasons.Add('kill-switch-active')}
if(-not [bool]$checkpoint.eligible -or -not [bool]$checkpoint.dispatch){$reasons.Add('checkpoint-ineligible')}
if([bool]$checkpoint.duplicate){$reasons.Add('duplicate-checkpoint')}
$eligible=$reasons.Count -eq 0
$automatic=$s.activation -in @('automatic_report_only','automatic_sandbox_audit')
$sandboxAudit=$s.activation -eq 'automatic_sandbox_audit'
$status=if($eligible){if($sandboxAudit){'sandbox-audit-ready'}elseif($automatic){'report-scan-ready'}else{'dry-run-ready'}}else{'blocked'};$scope=if($eligible){@($checkpoint.eligible_scope)}else{@()}
$report=[pscustomobject][ordered]@{schema_version='1.0.0';status=$status;live_task_enabled=$automatic;mutation_policy=if($sandboxAudit){'sandbox-only'}else{'report-only'};dispatch_trigger='verified_checkpoint_event_only';checkpoint_id=$checkpoint.checkpoint_id;kind=$checkpoint.kind;eligible_scope=$scope;approval=[ordered]@{audit_dispatch=$chain.audit_dispatch_approval;candidate_package=$chain.candidate_package_approval;production_implementation=$chain.production_implementation_approval};budget=[ordered]@{max_campaigns=$chain.max_campaigns;max_iterations_per_campaign=$chain.max_iterations_per_campaign;wall_clock_seconds=$chain.wall_clock_seconds;minimum_coverage_portfolios=$chain.minimum_coverage_portfolios};backup_reference=[ordered]@{task=$chain.backup_task;day=$chain.backup_day;time=$chain.backup_time};reasons=$reasons.ToArray()}
if($Json){$report|ConvertTo-Json -Depth 8}else{$report}
