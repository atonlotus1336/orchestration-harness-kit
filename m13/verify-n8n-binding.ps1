param(
  # M13.4 client binding verifier (kit-shareable). Confirms YOUR n8n endpoint answers
  # before the integration is marked live. Reads ONLY your local bindings overlay -
  # this kit ships no endpoint, no credentials, no defaults (fail-closed).
  # Exit codes: 0 = live and healthy | 3 = endpoint unreachable (retryable/outage)
  #             1 = endpoint answered but unhealthy | 4 = binding missing (fail-closed)
  [string]$BindingsPath = "bindings.local.yaml",
  [int]$TimeoutSec = 20
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $BindingsPath)) {
  Write-Host "VERIFY-N8N: BINDING_MISSING - create bindings.local.yaml with an n8n_endpoint slot (see N8N-INTEGRATION-TEMPLATE.md)"
  exit 4
}
$raw = Get-Content -Raw -LiteralPath $BindingsPath -Encoding UTF8
$endpoint = if ($raw -match '(?m)^\s*n8n_endpoint:\s*"?([^"\s#]+)"?') { $Matches[1] } else { "" }
$keyFile  = if ($raw -match '(?m)^\s*n8n_api_key_file:\s*"?([^"\s#]+)"?') { $Matches[1] } else { "" }
if (-not $endpoint) {
  Write-Host "VERIFY-N8N: BINDING_MISSING - n8n_endpoint slot is empty (fail-closed: no default endpoint is ever assumed)"
  exit 4
}

$uri = [System.Uri]$endpoint
$targetPort = if ($uri.Port -gt 0) { $uri.Port } else { 443 }
$tcpOk = $false
try {
  $client = New-Object System.Net.Sockets.TcpClient
  $async = $client.BeginConnect($uri.Host, $targetPort, $null, $null)
  if ($async.AsyncWaitHandle.WaitOne([TimeSpan]::FromSeconds($TimeoutSec)) -and $client.Connected) { $tcpOk = $true }
  $client.Close()
} catch { $tcpOk = $false }
if (-not $tcpOk) {
  Write-Host "VERIFY-N8N: UNREACHABLE - $($uri.Host):$targetPort did not answer (outage or wrong endpoint); integration must NOT be marked live"
  exit 3
}

$headers = @{}
if ($keyFile -and (Test-Path -LiteralPath $keyFile)) {
  $headers["X-N8N-API-KEY"] = (Get-Content -Raw -LiteralPath $keyFile).Trim()
}
try {
  [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
  $resp = Invoke-RestMethod -Uri ($endpoint.TrimEnd('/') + "/api/v1/workflows?limit=1") -Headers $headers -TimeoutSec $TimeoutSec
  Write-Host "VERIFY-N8N: LIVE - endpoint answered and API responded; integration may be marked live"
  exit 0
} catch {
  $code = $null; try { $code = [int]$_.Exception.Response.StatusCode } catch {}
  Write-Host "VERIFY-N8N: UNHEALTHY - endpoint answered but API failed (http=$code); check API key file and n8n service"
  exit 1
}
