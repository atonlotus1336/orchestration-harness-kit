# Small-model start — local open-source LLMs

Use this page when the agent has limited context or is roughly 7B–14B. Give it
this file first. Do not ask it to read the whole repository.

## Goal

Install the solo-PC core, prove each step, and stop on the first failed check.

## Rules for the agent

1. Perform one numbered step at a time.
2. After every command, record exactly one flag: `PASS`, `FAIL`, or `BLOCKED`.
3. Never infer success from prose. Use the command output.
4. On `FAIL`, stop and report the command, exit code, and last useful line.
5. Do not enable VPS, second-PC, Telegram, Prime/OMP, or maintenance modules
   until the solo core passes.
6. Ask before installing packages, opening a port, writing production files,
   or changing a scheduled task.

## Words used here

- **Harness:** the agent application, such as Hermes, Codex, or Claude Code.
- **Gateway:** the authenticated chat API used by clients and Telegram.
- **Storage authority:** the only PC allowed to write the home disk and memory
  databases.
- **Profile:** a JSON or YAML file that selects a role and model route.

## Solo-PC checklist

1. Confirm Python 3.11 or newer: `python --version`.
2. Confirm local Ollama is reachable: `ollama list`.
3. Pick one installed instruct or coding model. Do not invent a model name.
4. Run the platform preflight from `installer/preflight/`.
5. Render the default topology:

   ```bash
   python modules/topology/configure-topology.py --role solo --output topology-local.json
   ```

6. Follow `docs/REPLICATION-GUIDE.md` sections 0–4 only.
7. Run `python modules/guards/test-dry-run-gate.py`.
8. Run one local clone evaluation. Replace `<installed-model>` with a value
   shown by `ollama list`:

   [PowerShell]

   ```powershell
   $env:M11_EVAL_ROUTE='local-ollama:<installed-model>'
   powershell -File installer\clone-eval\eval-run.ps1 --class agentic-file-op
   ```

9. Read `installer/clone-eval/small-model-reference.json` before interpreting
   the result. A small model may be useful while still failing code-fix parity.

## Reliability ladder

If no deterministic check can verify a plain answer, load one procedure from
`modules/reasoning-shapes/`: self-consistency for facts, ensemble for reviews,
tree of thoughts for planning, and LATS only for actions with real pass/fail
feedback. No majority means `BLOCKED`, not a guessed answer.

## Completion record

Return this exact shape:

```json
{
  "solo_core": "PASS|FAIL|BLOCKED",
  "local_model": "<installed name>",
  "guard_test": "PASS|FAIL|BLOCKED",
  "clone_eval": "PASS|FAIL|BLOCKED",
  "next_optional_module": "none|telegram|vps|second-pc|prime-omp|maintenance"
}
```
