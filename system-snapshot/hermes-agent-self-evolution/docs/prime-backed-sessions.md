# Prime-Backed Sessions in Hermes OS

Prime is an optional session engine behind the existing Hermes control plane. It is not
an LLM provider and does not run another Hermes backend.

## Authority and activation

- NUCBOX owns the Hermes gateway, managed Prime MCP process, durable ledgers, capability
  policy, model route, memory/RAG access, and Prime worker lifecycle.
- The __OWNER_HANDLE__ desktop is a thin Hermes OS client over the existing dashboard tunnel.
  It does not host a second backend or a second copy of shared Hermes state.
- `workspace` is centrally set to `prime-default`, so a new session recommends Prime.
  Other eligible profiles remain `prime-available`; `telegram` remains `native-only`.
- Every Prime start still requires `explicit_handoff=true`. Existing chats remain native
  until handed off. Policy changes take effect only after the relevant Hermes surface is
  closed and reopened or an explicitly supported profile reload is used.
- Native Hermes remains available. Global `enabled=false` or a profile `native-only`
  override disables new Prime handoffs without disabling native chat.

## Hermes OS control sequence

1. Call `prime_session_route` to display the recommended and available engines.
2. For a deliberate Prime handoff, call `prime_session_start` with the existing Hermes
   profile context, a canonical project/session identity, workspace, prompt, loop limits,
   and `explicit_handoff=true`. Provider and model are derived from Hermes and cannot be
   overridden by this call.
3. Poll `prime_session_events` with the returned job ID, exact Hermes session ID, and the
   previous `next_cursor`. `prime_session_status` returns the current sanitized snapshot.
4. Use `prime_session_steer` for bounded guidance. Use `prime_session_approve` only after
   reviewing a pending request; it binds the request, job, profile, nonce, and decision.
5. `prime_session_interrupt` queues cancellation without affecting native Hermes.
6. `prime_session_detach` disconnects the UI view but leaves the backend job running.
   `prime_session_reattach` requires the same profile-owned job and Hermes session ID.

The status surface never returns the prompt, workspace path, raw Prime trace, provider
credential, or Hermes secret file. Detailed traces remain Prime-owned; Hermes retains
only bounded status, outcome, approvals, and opaque references.

## Recovery and failure behavior

- A desktop disconnect is harmless to the NUCBOX-owned job. Reattach using the durable
  job ID and original Hermes session ID.
- After a managed MCP/backend restart, use the existing Prime recovery reconciler. It
  reattaches only a single exact job/session/runtime match; missing, duplicate, expired,
  or mismatched workers are aborted or marked orphaned.
- If Prime is unavailable, incompatible, disabled, or at its admission limit, keep or
  open the session in native Hermes. Never substitute another provider silently.
- Emergency disable: set the central Prime policy `enabled` to `false`, render the managed
  profile overlays, then restart the relevant Hermes surface. This does not remove native
  Hermes, global MCP, memory, RAG, or toolsets.
