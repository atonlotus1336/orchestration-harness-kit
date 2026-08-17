[CmdletBinding()]
param(
  [ValidateSet('Weekly','Monthly')][string]$Cadence,
  [Parameter(Mandatory=$true)][string]$LedgerPath,
  [Parameter(Mandatory=$true)][string]$CoveragePath,
  [Parameter(Mandatory=$true)][string]$OutputPath
)
$ErrorActionPreference='Stop'
$records=@();if(Test-Path -LiteralPath $LedgerPath){$records=@(Get-Content -LiteralPath $LedgerPath|Where-Object{$_ -match '\S'}|ForEach-Object{$_|ConvertFrom-Json})}
$coverage=Get-Content -LiteralPath $CoveragePath -Raw|ConvertFrom-Json
$kept=@($records|Where-Object{$_.status -eq 'keep'}).Count;$discarded=@($records|Where-Object{$_.status -eq 'discard'}).Count;$crashed=@($records|Where-Object{$_.status -eq 'crash'}).Count
$lines=New-Object System.Collections.Generic.List[string]
$lines.Add("# AutoExperiment $Cadence Report")
$lines.Add('');$lines.Add("Generated: $(Get-Date -Format o)");$lines.Add("Ledger records: $($records.Count)")
$lines.Add('');$lines.Add('## Portfolio coverage and ranks');$lines.Add('')
foreach($q in @($coverage.queue)){$lines.Add("- $($q.position). $($q.portfolio_id) - $($q.allocation) - $($q.opportunity_id)")}
$lines.Add('');$lines.Add('## Results');$lines.Add('');$lines.Add("- Keep: $kept");$lines.Add("- Discard: $discarded");$lines.Add("- Crash: $crashed")
$lines.Add('');$lines.Add('## Costs');$lines.Add('');$lines.Add('- Cost factors remain separate in candidate evidence; no synthetic scalar score.')
$lines.Add('');$lines.Add('## Uncertainty');$lines.Add('');$lines.Add('- Unproven and unresolved candidates remain outside promotion.')
$lines.Add('');$lines.Add('## Next actions');$lines.Add('');$lines.Add('- Review ranked, reproducible, non-regressing winners for GSD handoff.')
$lines.Add('');$lines.Add('## Reconciliation');$lines.Add('');$lines.Add("records=$($records.Count);keep=$kept;discard=$discarded;crash=$crashed")
$dir=Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath));if(-not(Test-Path -LiteralPath $dir)){New-Item -ItemType Directory -Force -Path $dir|Out-Null}
$lines|Set-Content -LiteralPath $OutputPath -Encoding UTF8
[pscustomobject]@{cadence=$Cadence;output=$OutputPath;records=$records.Count;keep=$kept;discard=$discarded;crash=$crashed;reconciled=(($kept+$discarded+$crashed)-eq $records.Count)}|ConvertTo-Json
