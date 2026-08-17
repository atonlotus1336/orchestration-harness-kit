[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$RegistryPath,
  [Parameter(Mandatory=$true)][string]$ReplayPath,
  [switch]$AllowFixtureRoots,
  [switch]$Json
)
$ErrorActionPreference='Stop'
$registry=Get-Content -LiteralPath $RegistryPath -Raw|ConvertFrom-Json
$replay=Get-Content -LiteralPath $ReplayPath -Raw|ConvertFrom-Json
$roots=New-Object System.Collections.Generic.List[object];$files=New-Object System.Collections.Generic.List[object]
foreach($entry in @($registry.roots)){
  if([string]$entry.class -notin @('canonical','authored','generated','vendor','archive')){throw "Unknown Markdown root class: $($entry.class)"}
  $root=[System.IO.Path]::GetFullPath([string]$entry.path).TrimEnd('\')
  if(-not $AllowFixtureRoots -and -not $root.StartsWith('G:\')){throw "Markdown root is not on the G: disk: $root"}
  if(-not(Test-Path -LiteralPath $root -PathType Container)){throw "Registered Markdown root is missing: $root"}
  if((Get-Item -LiteralPath $root).Attributes -band [System.IO.FileAttributes]::ReparsePoint){throw "Registered Markdown root cannot be a reparse point: $root"}
  $roots.Add([pscustomobject]@{path=$root;class=$entry.class})
  foreach($f in @(Get-ChildItem -LiteralPath $root -Filter '*.md' -File -Recurse|Where-Object{-not ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint)})){
    $text=Get-Content -LiteralPath $f.FullName -Raw;$tokens=[math]::Ceiling($text.Length/4)
    $proposal=if($tokens -gt 4000){'split-or-progressive-disclosure'}elseif($tokens -gt 1500){'add-index-or-summary'}else{'retain'}
    $files.Add([pscustomobject][ordered]@{path=$f.FullName;root=$root;class=$entry.class;characters=$text.Length;estimated_tokens=$tokens;proposal=$proposal})
  }
}
$decisions=New-Object System.Collections.Generic.List[object]
foreach($task in @($replay.tasks)){
  $source=[System.IO.Path]::GetFullPath([string]$task.source_path);$candidate=[System.IO.Path]::GetFullPath([string]$task.candidate_path)
  $sourceInside=@($roots|Where-Object{$source -eq $_.path -or $source.StartsWith($_.path+'\')}).Count -gt 0
  if(-not $sourceInside){throw "Replay source is outside registered roots: $source"}
  $sourceText=Get-Content -LiteralPath $source -Raw;$candidateText=Get-Content -LiteralPath $candidate -Raw
  $missingTerms=@(@($task.required_terms)|Where-Object{$candidateText -notmatch [regex]::Escape([string]$_)})
  $missingAuthority=@(@($task.authority_terms)|Where-Object{$candidateText -notmatch [regex]::Escape([string]$_)})
  $sourceTokens=[math]::Ceiling($sourceText.Length/4);$candidateTokens=[math]::Ceiling($candidateText.Length/4)
  $qualityPreserved=$missingTerms.Count -eq 0 -and $missingAuthority.Count -eq 0
  $fewer=([int]$task.candidate_reads -le [int]$task.baseline_reads) -and $candidateTokens -lt $sourceTokens
  $decision=if($qualityPreserved -and $fewer){'accept'}else{'reject'}
  $decisions.Add([pscustomobject][ordered]@{task_id=$task.id;decision=$decision;quality_preserved=$qualityPreserved;missing_terms=$missingTerms;missing_authority=$missingAuthority;baseline_reads=[int]$task.baseline_reads;candidate_reads=[int]$task.candidate_reads;baseline_tokens=$sourceTokens;candidate_tokens=$candidateTokens;token_reduction=$sourceTokens-$candidateTokens})
}
$report=[pscustomobject][ordered]@{schema_version='1.0.0';storage='G-disk-not-google-drive';registered_roots=$roots.ToArray();files=$files.ToArray();replays=$decisions.ToArray();files_modified=0;broad_scan=$false}
if($Json){$report|ConvertTo-Json -Depth 8}else{$report}
