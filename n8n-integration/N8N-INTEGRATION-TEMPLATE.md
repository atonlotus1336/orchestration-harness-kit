# n8n Integration Template (M13 kit component)

This kit ships the INTEGRATION SHAPE only. It contains no endpoint, no hostname, no
credentials, and assumes no defaults — absence of a binding means the integration is
OFF (fail-closed).

## 1. Bind your endpoint (local-only overlay)

Create `bindings.local.yaml` next to your kit root (it is never exported, never synced,
never committed):

```yaml
version: n8n-bindings-v1
slots:
  n8n_endpoint: "https://YOUR-N8N-HOST"        # your n8n base URL
  vps_host: "YOUR-N8N-HOST"                    # host for reachability checks
  n8n_api_key_file: "/path/to/your/n8n-key"    # file containing an n8n API key (JWT)
  critical_workflows:                          # workflows whose silence should alarm you
    - name_match: "your-workflow-name"         # case-insensitive substring match
      max_age_hours: 48                        # newest successful run must be younger
```

Generate the API key in your n8n instance: Settings → n8n API → Create key. Store it in
a file OUTSIDE any synced/exported tree and reference it via `n8n_api_key_file`.

## 2. Verify before marking live

```
powershell -File verify-n8n-binding.ps1            # Windows
bash verify-n8n-binding.sh                         # POSIX
```

Exit codes — act on them, do not improvise:
- `0 LIVE` — endpoint answered, API healthy → integration may be marked live.
- `3 UNREACHABLE` — outage or wrong endpoint → do NOT mark live; retry later.
- `1 UNHEALTHY` — endpoint up but API failing → check the key file and the n8n service.
- `4 BINDING_MISSING` — no binding → nothing runs (this is intentional).

## 3. Standing health probe (optional, recommended)

If you run the surface-graph pattern from this kit, register your n8n link as an
`external: true` surface whose probe distinguishes THREE failure classes (never
conflate them):
1. host unreachable → external outage (acknowledgeable, expiring),
2. host up / service port dead → service failure (real RED),
3. API healthy / critical workflow silent → the silent killer (real RED).

A reference staged probe implementing this split ships with the source system as
`scripts/probe-vps-n8n.ps1` (reads the same bindings slots).
