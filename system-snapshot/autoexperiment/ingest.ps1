[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$ManifestPath,
  [Parameter(Mandatory=$true)][string]$OutputPath,
  [string]$SourceTiersPath='__WINDOWS_WORKSPACE__\.planning\autoexperiment-control-plane\contracts\source-tiers.yaml',
  [switch]$Json
)
$ErrorActionPreference='Stop'
$manifest=Get-Content -LiteralPath $ManifestPath -Raw|ConvertFrom-Json
$contract=Get-Content -LiteralPath $SourceTiersPath -Raw|ConvertFrom-Json
$tiers=@{};foreach($t in @($contract.tiers)){$tiers[[string]$t.id]=$t}
$roots=@($manifest.registered_roots|ForEach-Object{[System.IO.Path]::GetFullPath([string]$_).TrimEnd('\')})
if($roots.Count -eq 0){throw 'No registered ingestion roots were declared.'}
$records=New-Object System.Collections.Generic.List[object]
foreach($source in @($manifest.sources)){
  foreach($f in @('source_id','uri_or_path','tier','privacy_class','authorized')){if($source.PSObject.Properties.Name -notcontains $f -or $null -eq $source.$f){throw "Source field missing: $f"}}
  if(-not $tiers.ContainsKey([string]$source.tier)){throw "Unknown source tier: $($source.tier)"}
  $tier=$tiers[[string]$source.tier];$reasons=New-Object System.Collections.Generic.List[string]
  if(-not [bool]$source.authorized){$reasons.Add('source-not-authorized')}
  if([string]$source.privacy_class -notin @($tier.privacy_classes)){$reasons.Add('privacy-class-not-allowed')}
  if($source.tier -eq 'T4-community-indigenous'){
    $communityOk=($source.PSObject.Properties.Name -contains 'community_authorization') -and [bool]$source.community_authorization -and ($source.PSObject.Properties.Name -contains 'care_terms_path') -and (Test-Path -LiteralPath ([string]$source.care_terms_path) -PathType Leaf)
    if(-not $communityOk){$reasons.Add('community-authorization-or-CARE-terms-missing')}
  }
  $path=[string]$source.uri_or_path;$isLocal=Test-Path -LiteralPath $path -PathType Leaf
  if($isLocal){
    $full=[System.IO.Path]::GetFullPath($path);$inside=@($roots|Where-Object{$full -eq $_ -or $full.StartsWith($_+'\')}).Count -gt 0
    if(-not $inside){$reasons.Add('source-outside-registered-roots')}
    $hash=(Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash;$text=Get-Content -LiteralPath $full -Raw
  }else{
    $text=if($source.PSObject.Properties.Name -contains 'content') {[string]$source.content}else{''}
    if(-not $text){$reasons.Add('source-content-unavailable')}
    $bytes=[Text.Encoding]::UTF8.GetBytes($text);$sha=[Security.Cryptography.SHA256]::Create();$hash=([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','')
  }
  $injection=[bool]($text -match '(?i)(ignore\s+(all\s+)?previous|system\s+prompt|developer\s+message|execute\s+(this\s+)?command|tool[_ -]?call|exfiltrat|<script\b)')
  if($injection){$reasons.Add('injection-like-content')}
  $quarantine=if($injection -or $tier.quarantine -in @('required','locked')){'quarantined'}else{'clear'}
  $eligible=$reasons.Count -eq 0 -and $quarantine -ne 'locked'
  $sourceKind=if($isLocal){'local-file'}else{'external-handle'}
  $records.Add([pscustomobject][ordered]@{source_id=$source.source_id;uri_or_path=$path;retrieved_at=(Get-Date -Format o);sha256=$hash;tier=$source.tier;privacy_class=$source.privacy_class;quarantine_state=$quarantine;eligible=$eligible;raw_content_copied=$false;content_bytes=([Text.Encoding]::UTF8.GetByteCount($text));source_kind=$sourceKind;reasons=$reasons.ToArray()})
}
$dir=Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath));if(-not(Test-Path -LiteralPath $dir)){New-Item -ItemType Directory -Force -Path $dir|Out-Null}
foreach($r in $records){Add-Content -LiteralPath $OutputPath -Encoding UTF8 -Value ($r|ConvertTo-Json -Depth 6 -Compress)}
$report=[pscustomobject][ordered]@{schema_version='1.0.0';records=$records.ToArray();raw_content_store_written=$false;broad_scan=$false}
if($Json){$report|ConvertTo-Json -Depth 8}else{$report}
