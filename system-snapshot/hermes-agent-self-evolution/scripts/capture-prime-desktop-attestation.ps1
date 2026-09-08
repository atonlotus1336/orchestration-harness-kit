[CmdletBinding()]
param(
    [string]$ClientId = "thin-client",
    [string]$Endpoint = "http://__TAILSCALE_IPV4__:8642",
    [Parameter(Mandatory)][string]$ReportId,
    [string]$PublicKeyPath,
    [string]$PrivateKeyPath,
    [string]$ObservationPath,
    [string]$ChallengeOutputPath,
    [Parameter(Mandatory)][string]$OutputPath
)
$ErrorActionPreference = "Stop"
function Write-Utf8NoBom([string]$Path, [string]$Value) {
    [IO.File]::WriteAllText($Path, $Value, [Text.UTF8Encoding]::new($false))
}
function Convert-ToWslPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -notmatch '^([A-Za-z]):\\(.*)$') { throw "Expected an absolute Windows drive path" }
    return "/mnt/$($Matches[1].ToLowerInvariant())/$($Matches[2].Replace('\', '/'))"
}
$RepoWindows = Split-Path -Parent $PSScriptRoot
$RepoWsl = "__WSL_WORKSPACE__/hermes-agent-self-evolution"
$Enrollment = "$RepoWsl/contracts/prime-desktop-attesters.json"
$Store = "$RepoWsl/.git/phase01-gap-lifecycle/desktop-attestation-nonces.sqlite3"
$Python = (& wsl.exe -d __WSL_DISTRO__ --cd $RepoWsl -- bash scripts/sync-phase01-tools-env.sh --python).Trim()
$PrerequisitesReady = $PublicKeyPath -and $PrivateKeyPath -and $ObservationPath -and
    (Test-Path -LiteralPath $PublicKeyPath) -and (Test-Path -LiteralPath $PrivateKeyPath) -and (Test-Path -LiteralPath $ObservationPath)
$Fingerprint = $null
if ($PrerequisitesReady) {
    $keyMode = (Get-Acl -LiteralPath $PrivateKeyPath).Access | Where-Object { -not $_.IsInherited -and $_.IdentityReference -notmatch [regex]::Escape($env:USERNAME) }
    if ($keyMode) { throw "Private key has unexpected explicit ACL entries" }
    $Fingerprint = ((& ssh-keygen.exe -lf $PublicKeyPath -E sha256) -split '\s+')[1]
}
$beginArgs = @("-m", "prime_bridge.attestation", "begin-run", "--enrollment", $Enrollment, "--store", $Store, "--client-id", $ClientId, "--endpoint", $Endpoint, "--report-id", $ReportId)
if ($PrerequisitesReady) { $beginArgs += @("--prerequisites-ready", "--presented-fingerprint", $Fingerprint) }
$begin = (& wsl.exe -d __WSL_DISTRO__ --cd $RepoWsl -- $Python @beginArgs | ConvertFrom-Json)
if (-not $begin.challenge) {
    Write-Utf8NoBom $OutputPath ($begin.unsigned | ConvertTo-Json -Depth 8 -Compress)
    exit 2
}
if ($ChallengeOutputPath) {
    Write-Utf8NoBom $ChallengeOutputPath ($begin.challenge | ConvertTo-Json -Depth 8 -Compress)
}
$work = Join-Path $RepoWindows ".git\phase01-gap-lifecycle\desktop-attestation"
New-Item -ItemType Directory -Force -Path $work | Out-Null
$challengeWin = Join-Path $work "$ReportId.challenge.json"
$payloadWin = Join-Path $work "$ReportId.payload.json"
$canonicalWin = Join-Path $work "$ReportId.payload.bin"
Write-Utf8NoBom $challengeWin ($begin.challenge | ConvertTo-Json -Depth 8 -Compress)
$challengeWsl = Convert-ToWslPath $challengeWin
$observationWsl = Convert-ToWslPath $ObservationPath
$payloadWsl = Convert-ToWslPath $payloadWin
$canonicalWsl = Convert-ToWslPath $canonicalWin
$payloadJson = & wsl.exe -d __WSL_DISTRO__ --cd $RepoWsl -- $Python -m prime_bridge.attestation prepare-payload --challenge $challengeWsl --observation $observationWsl
Write-Utf8NoBom $payloadWin $payloadJson
& wsl.exe -d __WSL_DISTRO__ --cd $RepoWsl -- $Python -m prime_bridge.jcs $payloadWsl --output $canonicalWsl
& ssh-keygen.exe -Y sign -f $PrivateKeyPath -n hermes-prime-attester-v1 $canonicalWin | Out-Null
$signatureWsl = Convert-ToWslPath ($canonicalWin + ".sig")
$result = & wsl.exe -d __WSL_DISTRO__ --cd $RepoWsl -- $Python -m prime_bridge.attestation verify-run --enrollment $Enrollment --store $Store --payload $payloadWsl --signature $signatureWsl
Write-Utf8NoBom $OutputPath $result
