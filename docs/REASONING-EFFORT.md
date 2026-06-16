# MyVault Reasoning-Effort Convention

Purpose: one place that says **how to dial reasoning depth** across Claude, Codex, and
Hermes. Read-on-demand (not always-loaded). The intent — *spend more thinking on
high-stakes work, less on trivial work* — is the same everywhere; only the lever differs.

This file is the missing Claude-side twin of the Codex effort profiles: the stack always
had a budget dial for the Codex sub-engine, but no documented convention for the in-prompt
Claude trigger. This fixes that.

## 1. Claude (Claude Code / claude.ai) — in-prompt thinking triggers

Type the keyword in the prompt; escalating budget:

| Phrase | Use for |
|---|---|
| *(plain ask)* | trivial / mechanical: typos, renames, one-line edits, status checks |
| `think` | normal multi-step work: a focused change, a bounded bug |
| `think hard` / `think harder` / `megathink` | ambiguous design, multi-file change, tricky bug |
| `ultrathink` | maximum budget: architecture, security reasoning, gnarly concurrency/race bugs, anything where a wrong answer costs an hour |

Harness levers (Claude Code):
- **`/fast`** — Opus with faster output (same model, not a downgrade). Available on Opus 4.8/4.7/4.6.
- **ultracode on** — author/run a Workflow for substantive tasks; adversarially verify. Token cost is not the constraint; correctness is. Solo only on trivial/conversational turns.
- Reserve `ultrathink` + ultracode for genuinely hard work — they cost tokens and wall-clock.

## 2. Codex — persisted effort profiles

Set once with `/codex-gsd-set-profile`; all `codex-gsd-*` skills inherit it. Stored in
`.claude/codex-gsd-profile.json`.

| Profile | Codex effort | Use for |
|---|---|---|
| `quality` | **xhigh** | high-stakes: audits, security, architecture, adversarial review |
| `balanced` | medium | default day-to-day |
| `budget` | low | bulk/cheap passes |
| `inherit` | — | follow the caller |

- `claudex-Elite-Review --deep` forces **xhigh** on every Codex pass.
- **Subscription-only (hard rule):** Codex runs on ChatGPT-subscription auth, never
  pay-per-token API. Confirm `codex login status` → "Logged in using ChatGPT" before any
  Codex-firing skill. Never use the `openrouter` profile or set `OPENAI_API_KEY`.
  `service_tier` (fast|flex) is a compute tier, **not** API billing — safe. See the
  `codex-subscription-only` memory and `workspace/CONNECTION-PREFERENCES.md`.

## 3. Hermes

Reasoning depth = model choice + sampling params per task; there is no in-prompt `ultrathink`
keyword. Pick a stronger model for high-stakes work; keep `display.busy_input_mode: queue`
so model-load/switch doesn't cancel a deep generation (see `HERMES-USAGE-PREFERENCES.md`).

## 4. Pick by stakes (the practical rule)

| Stakes | Claude | Codex profile |
|---|---|---|
| Trivial (typo, config, rename) | plain ask | `budget` / `inherit` |
| Normal (bounded feature/bug) | `think` | `balanced` |
| High (architecture, security, hard debug, irreversible choice) | `ultrathink` + ultracode workflow | `quality` (xhigh) |

Diagnosis → planning → execution → verification should stay distinct on high-stakes work
(METHODOLOGY-CHARTER). Don't pay for depth you don't need; don't skimp where a wrong answer
is expensive.

## See also
- `AUTHORITY-CONSTITUTION.md` — precedence (effort never overrides evidence).
- `METHODOLOGY-CHARTER.md` — the orient→constrain→execute→verify loop this depth feeds.
- `/effort` slash command — surfaces this file and helps set the Codex profile.
