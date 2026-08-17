$script:Core = '__WINDOWS_WORKSPACE__\scripts\ops\backup-projects-core.sh'
$script:Daily = '__WINDOWS_WORKSPACE__\.rag\isolatedbackup-push.sh'

Describe 'Projects backup transfer estimates' -Tag Backup {
  It 'requests rsync statistics before parsing the recovery transfer size' {
    $core = Get-Content -LiteralPath $script:Core -Raw
    $core | Should Match 'recovery_estimate_output=\$\(LC_ALL=C rsync -aSni --delete --stats'
  }
}

Describe 'Backup partial cleanup and retention safety' -Tag Backup {
  It 'keeps only four weekly and three monthly Projects snapshots' {
    $core = Get-Content -LiteralPath $script:Core -Raw
    $core | Should Match 'WEEKLY_KEEP=4'
    $core | Should Match 'MONTHLY_KEEP=3'
  }

  It 'cleans an unpromoted Projects staging directory on failure' {
    $core = Get-Content -LiteralPath $script:Core -Raw
    $core | Should Match 'PARTIAL_ACTIVE=1'
    $core | Should Match 'rm -rf -- "\$PARTIAL_PATH"'
    $core | Should Not Match 'mv "\$PARTIAL_PATH" "\$\{PARTIAL_PATH\}\.FAILED"'
  }

  It 'does not treat the recovery bundle as Projects-source drift' {
    $core = Get-Content -LiteralPath $script:Core -Raw
    $core | Should Match "--exclude='/recovery/'"
  }

  It 'stages daily snapshots and excludes every VHDX location' {
    $daily = Get-Content -LiteralPath $script:Daily -Raw
    $daily | Should Match 'PARTIAL=.*\.partial-'
    $daily | Should Match "--exclude='\*\*/\*\.vhdx'"
    $daily | Should Match 'rm -rf -- "\$PARTIAL"'
    $daily | Should Match '\.backup-complete'
    $daily | Should Match ([regex]::Escape("mv '`$PARTIAL' '`$FINAL'"))
  }
}
