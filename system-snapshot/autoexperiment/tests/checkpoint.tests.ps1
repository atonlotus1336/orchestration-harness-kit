$script:RepoRoot='__WINDOWS_WORKSPACE__'
$script:Checkpoint=Join-Path $script:RepoRoot 'scripts\autoexperiment\checkpoint.ps1'
$script:Scheduler=Join-Path $script:RepoRoot 'scripts\autoexperiment\scheduler.ps1'
$script:Schedules=Join-Path $script:RepoRoot '.planning\autoexperiment-control-plane\contracts\schedules.yaml'

function New-BackupEvidence {
  param([string]$Root,[string]$Id='backup-1',[string]$Kind='daily',[string]$Status='green',[string]$Restore='green',[datetime]$Completed=(Get-Date),[bool]$Mismatch=$false,[bool]$BadScope=$false)
  New-Item -ItemType Directory -Force -Path $Root|Out-Null
  $manifest=Join-Path $Root 'manifest.json';Set-Content -LiteralPath $manifest -Encoding UTF8 -Value '{"backup":"fixture"}'
  $hash=(Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash;if($Mismatch){$hash='0'*64}
  $scope=if($BadScope){@('surface-secret')}else{@('surface-a')}
  $e=[ordered]@{backup_id=$Id;kind=$Kind;status=$Status;completed_at=$Completed.ToString('o');manifest_path=$manifest;manifest_sha256=$hash;included_surfaces=@('surface-a','surface-b');excluded_surfaces=@('surface-secret');restore_status=$Restore;eligible_scope=$scope}
  $path=Join-Path $Root 'evidence.json';$e|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $path -Encoding UTF8
  [pscustomobject]@{Evidence=$path;State=(Join-Path $Root 'checkpoints.jsonl')}
}

function Invoke-CheckpointFixture($Fixture){
  $output=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Checkpoint -EvidencePath $Fixture.Evidence -StatePath $Fixture.State -Json
  (($output-join "`n")|ConvertFrom-Json)
}

Describe 'Recovery checkpoint adapter' -Tag Checkpoint {
  It 'emits one eligible checkpoint only from green backup and restore evidence' {
    $f=New-BackupEvidence (Join-Path $TestDrive 'green');$r=Invoke-CheckpointFixture $f
    $r.eligible|Should Be $true;$r.dispatch|Should Be $true;@($r.eligible_scope)[0]|Should Be 'surface-a'
  }
  It 'is idempotent for one backup id' {
    $f=New-BackupEvidence (Join-Path $TestDrive 'duplicate');$null=Invoke-CheckpointFixture $f;$r=Invoke-CheckpointFixture $f
    $r.duplicate|Should Be $true;@(Get-Content -LiteralPath $f.State|Where-Object{$_ -match '\S'}).Count|Should Be 1
  }
  It 'blocks failed or partial backup status' {
    $f=New-BackupEvidence (Join-Path $TestDrive 'failed') -Status 'partial';$r=Invoke-CheckpointFixture $f
    $r.eligible|Should Be $false;(@($r.reasons) -contains 'backup-not-green')|Should Be $true
  }
  It 'blocks failed restore verification' {
    $f=New-BackupEvidence (Join-Path $TestDrive 'restore') -Restore 'red';(Invoke-CheckpointFixture $f).eligible|Should Be $false
  }
  It 'blocks stale evidence' {
    $f=New-BackupEvidence (Join-Path $TestDrive 'stale') -Completed (Get-Date).AddDays(-4);$r=Invoke-CheckpointFixture $f
    $r.eligible|Should Be $false;(@($r.reasons) -contains 'checkpoint-stale-or-future')|Should Be $true
  }
  It 'blocks manifest mismatch and scope not present in the backup' {
    $f=New-BackupEvidence (Join-Path $TestDrive 'mismatch') -Mismatch $true -BadScope $true;$r=Invoke-CheckpointFixture $f
    $r.eligible|Should Be $false;(@($r.reasons) -contains 'manifest-missing-or-mismatch')|Should Be $true;(@($r.reasons) -contains 'eligible-scope-not-backed-up')|Should Be $true
  }
}

function New-CheckpointForSchedule {
  param([string]$Root,[string]$Kind)
  $f=New-BackupEvidence $Root -Kind $Kind;$r=Invoke-CheckpointFixture $f
  $path=Join-Path $Root 'checkpoint.json';$r|ConvertTo-Json -Depth 8|Set-Content -LiteralPath $path -Encoding UTF8
  $path
}
function Invoke-ScheduleFixture([string]$CheckpointPath,[string]$Kill=''){
  $args=@('-NoProfile','-ExecutionPolicy','Bypass','-File',$script:Scheduler,'-CheckpointPath',$CheckpointPath,'-SchedulesPath',$script:Schedules,'-Json');if($Kill){$args+=@('-KillSwitchPath',$Kill)}
  $output=& powershell.exe @args;(($output-join "`n")|ConvertFrom-Json)
}

Describe 'Recovery-driven sandbox-audit cadence' -Tag Cadence {
  It 'defines daily, Saturday weekly, and Monday monthly backups at 11:59 AM' {
    $s=Get-Content -LiteralPath $script:Schedules -Raw|ConvertFrom-Json
    $s.activation|Should Be 'automatic_sandbox_audit';@($s.chains).Count|Should Be 3
    @($s.chains|Where-Object{$_.backup_time -eq '11:59 AM'}).Count|Should Be 3
    ($s.chains|Where-Object{$_.id -eq 'weekly'}).backup_day|Should Be 'Saturday'
    ($s.chains|Where-Object{$_.id -eq 'monthly'}).backup_day|Should Be 'Monday'
    @($s.chains|Where-Object{[bool]$_.improvement_clock_trigger}).Count|Should Be 0
  }
  It 'produces only a sandbox-audit daily dispatch from a verified checkpoint event' {
    $path=New-CheckpointForSchedule (Join-Path $TestDrive 'daily') 'daily';$r=Invoke-ScheduleFixture $path
    $r.status|Should Be 'sandbox-audit-ready';$r.live_task_enabled|Should Be $true;$r.mutation_policy|Should Be 'sandbox-only';$r.dispatch_trigger|Should Be 'verified_checkpoint_event_only';$r.budget.wall_clock_seconds|Should Be 3600
    $r.approval.candidate_package|Should Be 'automatic-gsd-package';$r.approval.production_implementation|Should Be 'manual-confirmation-required'
  }
  It 'requires nine-way coverage in the weekly budget and caps monthly at 24 hours' {
    $weekly=Invoke-ScheduleFixture (New-CheckpointForSchedule (Join-Path $TestDrive 'weekly') 'weekly')
    $monthly=Invoke-ScheduleFixture (New-CheckpointForSchedule (Join-Path $TestDrive 'monthly') 'monthly')
    $weekly.budget.minimum_coverage_portfolios|Should Be 9;$monthly.budget.wall_clock_seconds|Should Be 86400
  }
  It 'blocks dispatch when the independent kill switch is present' {
    $root=Join-Path $TestDrive 'killed';$path=New-CheckpointForSchedule $root 'daily';$kill=Join-Path $root 'DISABLED';Set-Content -LiteralPath $kill -Value 'disabled'
    $r=Invoke-ScheduleFixture $path $kill;$r.status|Should Be 'blocked';(@($r.reasons) -contains 'kill-switch-active')|Should Be $true
  }
  It 'leaves every existing backup entry point byte-identical' {
    $files=@('__WINDOWS_WORKSPACE__\scripts\ops\register-vps-backup-tasks.ps1','__WINDOWS_WORKSPACE__\scripts\ops\backup-vault.ps1','__WINDOWS_WORKSPACE__\scripts\ops\backup-projects.ps1')
    $before=@($files|ForEach-Object{(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash})
    $path=New-CheckpointForSchedule (Join-Path $TestDrive 'unchanged') 'weekly';$null=Invoke-ScheduleFixture $path
    $after=@($files|ForEach-Object{(Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash})
    @(Compare-Object $before $after).Count|Should Be 0
  }
}
