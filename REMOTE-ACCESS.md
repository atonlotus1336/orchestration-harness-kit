# Remote Access — reaching your harness from other devices

Your harness has one hub (the OpenAI-compatible gateway plus the Workspace
web UI) and many possible doors. This guide maps the doors, what each
requires, and the security rules that keep them safe. Vendor-neutral:
Tailscale is the recommended private-network overlay, but any WireGuard/VPN
or LAN that gives devices stable private addresses works the same way.

## The access-surface map

| Door | Device | Talks to | Requires |
|---|---|---|---|
| Local browser | the host PC | Workspace UI (`:3000`) | nothing extra |
| Phone/laptop browser | any device on your private overlay | Workspace UI (`:3000`) | overlay app on both devices + wide bind (below) |
| OpenAI-compatible chat app | phone/laptop | gateway (`:8642/v1`) | overlay + gateway API key |
| Messenger (any) | anywhere | adapter → gateway | see `modules/messaging/` |
| Desktop admin app | a trusted PC | dashboard (`:9119`) | SSH tunnel ONLY — see rule 3 |

## Setting up the phone-browser path (the 10-minute version)

1. **Overlay on both ends.** Install your overlay (e.g. Tailscale) on the
   host PC and the phone, signed into the SAME account. Verify the phone's
   app lists the host machine by name — an empty machine list means wrong
   account, and everything after this will mysteriously fail.
2. **Host must listen beyond loopback.** The #1 cause of "connected but
   nothing answers": a service bound to `127.0.0.1` answers only itself.
   Chat/API surfaces (gateway, Workspace) must bind `0.0.0.0`. Verify on
   the host: `ss -tlnp | grep 8642` (Linux/WSL) — you want `0.0.0.0:8642`,
   not `127.0.0.1:8642`. RE-CHECK AFTER EVERY UPGRADE: updates have silently
   reset bind settings to loopback in the source system's history.
3. **Phone browser →** `http://<host-overlay-address>:3000`. Type `http://`
   explicitly (phones force https, which fails here) and beware the address
   bar turning it into a web search. Works → use "Add to Home Screen" for
   an app-like icon.

## Security rules (each one paid for in incidents)

1. **A private overlay is NOT authentication.** Every device on the overlay
   can knock on every wide-bound port. The gateway still requires its API
   key; keep it that way.
2. **Bind policy by surface class:** chat/API surfaces (gateway, Workspace)
   may bind wide WITH auth; admin surfaces (dashboard `:9119`) stay
   loopback-only forever, reached via SSH tunnel (`ssh -L 9119:127.0.0.1:9119`)
   from trusted PCs only — never from phones, never rebound wide.
3. **Verify from the REMOTE device.** A service can be perfectly healthy
   locally and unreachable remotely (wrong bind, firewall). Local green
   proves nothing about remote reach — and a host probing its own overlay
   address can false-negative, so the phone itself is the real test.
4. **Lost device = revoke it** in your overlay's admin console. That is the
   whole offboarding procedure; do it promptly.
5. **Two doors can be two brains.** A messenger adapter pointed at one
   backend and a browser pointed at another are separate instances with
   separate memory. Know which brain each door opens.

## Relationship to the roadmap

This guide supplies the network steps for the role-aware topology module in
`modules/topology/`. Render a disabled `vps` or `second-pc` role file there,
fill its local service addresses, then follow this guide. The solo PC remains
the only storage authority. Complete the final probe from the remote machine.
