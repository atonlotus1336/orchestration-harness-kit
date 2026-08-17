$script:RepoRoot='__WINDOWS_WORKSPACE__';$script:Optimizer=Join-Path $script:RepoRoot 'scripts\autoexperiment\markdown-optimizer.ps1';$script:Reporter=Join-Path $script:RepoRoot 'scripts\autoexperiment\report.ps1'
function New-MarkdownReplayFixture([string]$Root,[bool]$DropAuthority=$false){
  $docs=Join-Path $Root 'docs';New-Item -ItemType Directory -Force -Path $docs|Out-Null
  $source=Join-Path $docs 'canonical.md';$candidate=Join-Path $Root 'candidate.md'
  Set-Content -LiteralPath $source -Encoding UTF8 -Value (('# Authority Rule`nEvidence outranks memory. Protected behavior remains unchanged. ')*30)
  $candidateText=if($DropAuthority){'Evidence outranks memory. Short.'}else{'Authority Rule. Evidence outranks memory. Protected behavior remains unchanged.'};Set-Content -LiteralPath $candidate -Encoding UTF8 -Value $candidateText
  $registry=Join-Path $Root 'registry.json';[pscustomobject]@{roots=@([ordered]@{path=$docs;class='canonical'})}|ConvertTo-Json -Depth 4|Set-Content -LiteralPath $registry -Encoding UTF8
  $replay=Join-Path $Root 'replay.json';[pscustomobject]@{tasks=@([ordered]@{id='authority-answer';source_path=$source;candidate_path=$candidate;required_terms=@('Evidence outranks memory','Protected behavior');authority_terms=@('Authority Rule');baseline_reads=2;candidate_reads=1})}|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $replay -Encoding UTF8
  [pscustomobject]@{Docs=$docs;Source=$source;Candidate=$candidate;Registry=$registry;Replay=$replay}
}
function Invoke-MarkdownFixture($f){$o=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Optimizer -RegistryPath $f.Registry -ReplayPath $f.Replay -AllowFixtureRoots -Json;(($o-join "`n")|ConvertFrom-Json)}
Describe 'G disk Markdown replay optimizer' {
  It 'accepts fewer reads and tokens only when answer and authority terms survive' {
    $f=New-MarkdownReplayFixture (Join-Path $TestDrive 'accept');$r=Invoke-MarkdownFixture $f
    $r.storage|Should Be 'G-disk-not-google-drive';$r.replays[0].decision|Should Be 'accept';$r.replays[0].token_reduction|Should BeGreaterThan 0;$r.files_modified|Should Be 0
  }
  It 'rejects a smaller candidate that loses authority or answer quality' {
    $f=New-MarkdownReplayFixture (Join-Path $TestDrive 'reject') -DropAuthority $true;$r=Invoke-MarkdownFixture $f
    $r.replays[0].decision|Should Be 'reject';$r.replays[0].quality_preserved|Should Be $false
  }
  It 'scans only registered roots and leaves Markdown byte-identical' {
    $root=Join-Path $TestDrive 'bounded';$f=New-MarkdownReplayFixture $root;$outside=Join-Path $root 'outside.md';Set-Content -LiteralPath $outside -Value 'not registered'
    $before=(Get-FileHash -LiteralPath $f.Source -Algorithm SHA256).Hash;$r=Invoke-MarkdownFixture $f;$after=(Get-FileHash -LiteralPath $f.Source -Algorithm SHA256).Hash
    @($r.files|Where-Object{$_.path -eq $outside}).Count|Should Be 0;$before|Should Be $after;$r.broad_scan|Should Be $false
  }
  It 'rejects non-G roots unless explicitly running fixtures' {
    $f=New-MarkdownReplayFixture (Join-Path $TestDrive 'drive-check');$o=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Optimizer -RegistryPath $f.Registry -ReplayPath $f.Replay -Json 2>&1
    $LASTEXITCODE|Should Not Be 0;($o-join "`n")|Should Match 'not on the G: disk'
  }
}
Describe 'Canonical weekly and monthly reports' {
  It 'reconciles the ledger and includes every required report section' {
    $root=Join-Path $TestDrive 'report';New-Item -ItemType Directory -Path $root|Out-Null;$ledger=Join-Path $root 'ledger.jsonl'
    @(@{status='keep'},@{status='discard'},@{status='crash'})|ForEach-Object{Add-Content -LiteralPath $ledger -Encoding UTF8 -Value ($_|ConvertTo-Json -Compress)}
    $coverage=Join-Path $root 'coverage.json';@{queue=@(@{position=1;portfolio_id='knowledge-retrieval';allocation='coverage';opportunity_id='o1'})}|ConvertTo-Json -Depth 5|Set-Content -LiteralPath $coverage -Encoding UTF8
    $out=Join-Path $root 'weekly.md';$json=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Reporter -Cadence Weekly -LedgerPath $ledger -CoveragePath $coverage -OutputPath $out;$r=(($json-join "`n")|ConvertFrom-Json);$text=Get-Content -LiteralPath $out -Raw
    $r.reconciled|Should Be $true;$r.records|Should Be 3
    foreach($section in @('Portfolio coverage and ranks','Results','Costs','Uncertainty','Next actions','Reconciliation')){$text.Contains($section)|Should Be $true}
  }
}
