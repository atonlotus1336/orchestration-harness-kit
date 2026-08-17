[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$CampaignPath,
  [Parameter(Mandatory = $true)][string]$StatePath,
  [switch]$Json
)
$ErrorActionPreference='Stop'
$campaign=Get-Content -LiteralPath $CampaignPath -Raw|ConvertFrom-Json
$required=@('campaign_id','builder_id','verifier_id','builder_context_id','verifier_context_id','frozen_contract_path','frozen_contract_sha256','max_transitions','no_progress_limit','wall_clock_seconds','coupled_package','signals')
foreach($field in $required){if($campaign.PSObject.Properties.Name -notcontains $field -or $null -eq $campaign.$field){throw "Campaign field missing: $field"}}
if([string]$campaign.builder_id -eq [string]$campaign.verifier_id){throw 'Builder and verifier must be separate.'}
if([string]$campaign.builder_context_id -eq [string]$campaign.verifier_context_id){throw 'Verifier must receive a fresh context.'}
$actualHash=(Get-FileHash -LiteralPath ([string]$campaign.frozen_contract_path) -Algorithm SHA256).Hash
if($actualHash -ne [string]$campaign.frozen_contract_sha256){throw 'Campaign contract drift detected.'}

$started=Get-Date
$phase='entry'
$history=New-Object System.Collections.Generic.List[object]
$transitions=0;$noProgress=0;$stopReason='complete'
$terminal=$false
while(-not $terminal){
  if($transitions -ge [int]$campaign.max_transitions){$stopReason='max-transitions';break}
  if($noProgress -ge [int]$campaign.no_progress_limit){$stopReason='no-progress';break}
  if(((Get-Date)-$started).TotalSeconds -ge [int]$campaign.wall_clock_seconds){$stopReason='wall-clock';break}
  $actualHash=(Get-FileHash -LiteralPath ([string]$campaign.frozen_contract_path) -Algorithm SHA256).Hash
  if($actualHash -ne [string]$campaign.frozen_contract_sha256){$stopReason='contract-drift';break}
  $from=$phase;$reason='';$progress=$true
  switch($phase){
    'entry'{$phase='clarity';$reason='entry-gate-passed'}
    'clarity'{if([bool]$campaign.signals.clarity_pass){$phase='reproduction';$reason='goal-and-boundary-clear'}else{$phase='clarity';$reason='clarity-loop';$progress=$false}}
    'reproduction'{if([bool]$campaign.signals.reproduced){$phase='verifier-checkpoint';$reason='baseline-reproduced'}else{$phase='clarity';$reason='reproduction-return-to-clarity'}}
    'verifier-checkpoint'{if([bool]$campaign.signals.verifier_ready){$phase='falsification';$reason='external-verifier-ready'}else{$phase='verifier-checkpoint';$reason='verifier-not-ready';$progress=$false}}
    'falsification'{if([bool]$campaign.signals.falsifiable){$phase='kernel';$reason='falsifiable-hypothesis'}else{$phase='clarity';$reason='hypothesis-return-to-clarity'}}
    'kernel'{if([bool]$campaign.signals.kernel_complete){$phase='independent-verification';$reason='kernel-finished'}else{$phase='kernel';$reason='bounded-kernel-loop';$progress=$false}}
    'independent-verification'{if([bool]$campaign.signals.independent_verified){$phase='integration';$reason='independent-verifier-passed'}else{$phase='falsification';$reason='verification-feedback'}}
    'integration'{
      if([bool]$campaign.coupled_package -and -not [bool]$campaign.signals.ablation_pass){$phase='integration';$reason='ablation-required';$progress=$false}
      elseif([bool]$campaign.signals.integration_pass){$phase='exit-clarity';$reason='integration-passed'}
      else{$phase='falsification';$reason='integration-feedback'}
    }
    'exit-clarity'{if([bool]$campaign.signals.exit_clear){$phase='complete';$reason='exit-record-complete';$terminal=$true}else{$phase='exit-clarity';$reason='exit-clarity-loop';$progress=$false}}
    default{throw "Unknown campaign phase: $phase"}
  }
  $transitions++
  if($progress){$noProgress=0}else{$noProgress++}
  $history.Add([pscustomobject][ordered]@{sequence=$transitions;from=$from;to=$phase;reason=$reason;builder_context_id=$campaign.builder_context_id;verifier_context_id=$campaign.verifier_context_id;timestamp=(Get-Date -Format o)})
}
$status=if($phase -eq 'complete'){'complete'}else{'stopped'}
$state=[pscustomobject][ordered]@{
  schema_version='1.0.0';campaign_id=$campaign.campaign_id;status=$status;phase=$phase;stop_reason=$stopReason
  transitions=$transitions;no_progress_rounds=$noProgress;builder_id=$campaign.builder_id;verifier_id=$campaign.verifier_id
  fresh_verifier_context=([string]$campaign.builder_context_id -ne [string]$campaign.verifier_context_id)
  coupled_package=[bool]$campaign.coupled_package;ablation_pass=[bool]$campaign.signals.ablation_pass
  started_at=$started.ToString('o');completed_at=(Get-Date -Format o);history=$history.ToArray()
  exit_record=[ordered]@{final_phase=$phase;stop_reason=$stopReason;bounded=($transitions -le [int]$campaign.max_transitions);independently_verified=[bool]$campaign.signals.independent_verified;integration_pass=[bool]$campaign.signals.integration_pass;exit_clear=[bool]$campaign.signals.exit_clear}
}
$dir=Split-Path -Parent ([System.IO.Path]::GetFullPath($StatePath));if(-not(Test-Path -LiteralPath $dir)){New-Item -ItemType Directory -Force -Path $dir|Out-Null}
$state|ConvertTo-Json -Depth 10|Set-Content -LiteralPath $StatePath -Encoding UTF8
if($Json){$state|ConvertTo-Json -Depth 10}else{$state}

