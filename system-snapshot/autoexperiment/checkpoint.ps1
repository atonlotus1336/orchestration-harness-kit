[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$EvidencePath,
  [Parameter(Mandatory=$true)][string]$StatePath,
  [int]$MaxAgeHours=0,
  [switch]$Json
)
$ErrorActionPreference='Stop'
$e=Get-Content -LiteralPath $EvidencePath -Raw|ConvertFrom-Json
$required=@('backup_id','kind','status','completed_at','manifest_path','manifest_sha256','included_surfaces','excluded_surfaces','restore_status','eligible_scope')
foreach($f in $required){if($e.PSObject.Properties.Name -notcontains $f -or $null -eq $e.$f -or (($e.$f -is [string])-and [string]::IsNullOrWhiteSpace([string]$e.$f))){throw "Backup evidence field missing: $f"}}
if($e.kind -notin @('daily','weekly','monthly')){throw "Unknown backup kind: $($e.kind)"}
if($MaxAgeHours -le 0){$MaxAgeHours=switch($e.kind){'daily'{48}'weekly'{240}'monthly'{800}}}
$existing=@()
if(Test-Path -LiteralPath $StatePath){$existing=@(Get-Content -LiteralPath $StatePath|Where-Object{$_ -match '\S'}|ForEach-Object{$_|ConvertFrom-Json})}
$prior=@($existing|Where-Object{$_.backup_id -eq $e.backup_id})|Select-Object -First 1
if($prior){$result=[pscustomobject][ordered]@{schema_version='1.0.0';backup_id=$e.backup_id;kind=$e.kind;eligible=[bool]$prior.eligible;dispatch=[bool]$prior.dispatch;duplicate=$true;reason='already-processed';checkpoint_id=$prior.checkpoint_id};if($Json){$result|ConvertTo-Json -Depth 6}else{$result};exit 0}
$reasons=New-Object System.Collections.Generic.List[string]
if($e.status -ne 'green'){$reasons.Add('backup-not-green')}
if($e.restore_status -ne 'green'){$reasons.Add('restore-not-green')}
$manifestOk=$false
if(Test-Path -LiteralPath ([string]$e.manifest_path) -PathType Leaf){$manifestOk=(Get-FileHash -LiteralPath ([string]$e.manifest_path) -Algorithm SHA256).Hash -eq [string]$e.manifest_sha256}
if(-not $manifestOk){$reasons.Add('manifest-missing-or-mismatch')}
try{$age=((Get-Date)-[datetimeoffset]::Parse([string]$e.completed_at).LocalDateTime).TotalHours;if($age -gt $MaxAgeHours -or $age -lt -1){$reasons.Add('checkpoint-stale-or-future')}}catch{$reasons.Add('invalid-completion-time');$age=$null}
$included=@($e.included_surfaces);$excluded=@($e.excluded_surfaces);$scope=@($e.eligible_scope)
if($scope.Count -eq 0){$reasons.Add('empty-eligible-scope')}
$outOfScope=@($scope|Where-Object{$_ -notin $included -or $_ -in $excluded})
if($outOfScope.Count){$reasons.Add('eligible-scope-not-backed-up')}
$eligible=$reasons.Count -eq 0
$checkpointId='checkpoint-'+([string]$e.backup_id)
$recordScope=if($eligible){$scope}else{@()}
$record=[pscustomobject][ordered]@{schema_version='1.0.0';checkpoint_id=$checkpointId;backup_id=$e.backup_id;kind=$e.kind;eligible=$eligible;dispatch=$eligible;duplicate=$false;status=$e.status;restore_status=$e.restore_status;completed_at=$e.completed_at;age_hours=$age;manifest_path=$e.manifest_path;manifest_sha256=$e.manifest_sha256;included_surfaces=$included;excluded_surfaces=$excluded;eligible_scope=$recordScope;reasons=$reasons.ToArray();recorded_at=(Get-Date -Format o)}
$dir=Split-Path -Parent ([System.IO.Path]::GetFullPath($StatePath));if(-not(Test-Path -LiteralPath $dir)){New-Item -ItemType Directory -Force -Path $dir|Out-Null}
Add-Content -LiteralPath $StatePath -Encoding UTF8 -Value ($record|ConvertTo-Json -Depth 8 -Compress)
if($Json){$record|ConvertTo-Json -Depth 8}else{$record}
