$script:Root='__WINDOWS_WORKSPACE__';$script:Control=Join-Path $script:Root 'scripts\autoexperiment\control-plane.ps1';$script:Promotion=Join-Path $script:Root 'scripts\autoexperiment\promotion.ps1';$script:Skill=Join-Path $script:Root 'skills-shared\autoexperiment\SKILL.md'
Describe 'Dormant cross-harness front door' -Tag Harness {
  It 'has one canonical skill source with portable metadata' {$text=Get-Content -LiteralPath $script:Skill -Raw;$text|Should Match '^---';$text|Should Match 'name: autoexperiment';$text|Should Match 'Codex, Claude Code, and Hermes'}
  It 'reports truthful automatic sandbox-audit state' {$o=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Control -Command status;$r=(($o-join "`n")|ConvertFrom-Json);$r.dispatch_activation|Should Be 'automatic_sandbox_audit';$r.skill_visibility|Should Be 'scheduled-sandbox-audit';$r.canonical_skill|Should Be $script:Skill}
  It 'explains G disk and GSD-only promotion boundaries' {$o=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Control -Command explain;$r=(($o-join "`n")|ConvertFrom-Json);$r.storage|Should Be 'G-disk-not-google-drive';$r.promotion|Should Be 'GSD-package-only'}
  It 'does not alter the active skill manifest or junction tier' {$manifest='__WINDOWS_WORKSPACE__\skills-active\ACTIVE-MANIFEST.txt';if(Test-Path -LiteralPath $manifest){(Get-Content -LiteralPath $manifest -Raw)|Should Not Match '(?m)^autoexperiment$'};Test-Path -LiteralPath '__WINDOWS_WORKSPACE__\skills-active\autoexperiment'|Should Be $false}
}
Describe 'GSD-only promotion package' -Tag Promotion {
  It 'renders complete evidence without production or history mutation' {
    $root=Join-Path $TestDrive 'winner';New-Item -ItemType Directory -Path $root|Out-Null;$paths=@{};foreach($n in @('evidence','patch','baseline','verifier','rollback')){$p=Join-Path $root ($n+'.txt');Set-Content -LiteralPath $p -Value $n;$paths[$n]=$p}
    $winner=[ordered]@{winner_id='winner-1';title='Safe improvement';outcome='Measured improvement';evidence_paths=@($paths.evidence);patch_path=$paths.patch;baseline_path=$paths.baseline;verifier_path=$paths.verifier;economics=@{benefit=2;cost=0};invariant_results=@(@{id='working-behavior-preserved';pass=$true});rollback_path=$paths.rollback;judge_decision='accept';ablation_status='not-coupled'};$wp=Join-Path $root 'winner.json';$winner|ConvertTo-Json -Depth 7|Set-Content -LiteralPath $wp -Encoding UTF8
    $o=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Promotion -WinnerPath $wp -OutputDirectory (Join-Path $root 'out') -Json;$r=(($o-join "`n")|ConvertFrom-Json)
    Test-Path -LiteralPath $r.brief|Should Be $true;$r.production_mutation|Should Be $false;$r.git_history_mutation|Should Be $false;$r.skill_activation|Should Be $false
  }
  It 'rejects an unaccepted or causally unresolved candidate' {
    $root=Join-Path $TestDrive 'rejected';New-Item -ItemType Directory -Path $root|Out-Null;$p=Join-Path $root 'x';Set-Content -LiteralPath $p -Value x
    $w=@{winner_id='x';title='x';outcome='x';evidence_paths=@($p);patch_path=$p;baseline_path=$p;verifier_path=$p;economics=@{};invariant_results=@();rollback_path=$p;judge_decision='reject';ablation_status='unresolved-interaction'};$wp=Join-Path $root 'w.json';$w|ConvertTo-Json -Depth 6|Set-Content -LiteralPath $wp
    $o=& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script:Promotion -WinnerPath $wp -OutputDirectory (Join-Path $root 'out') -Json 2>&1;$LASTEXITCODE|Should Not Be 0;($o-join "`n")|Should Match 'not accepted'
  }
}
