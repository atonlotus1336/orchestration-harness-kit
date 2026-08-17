# verified-maintenance — backup-gated improvement windows

**What it is:** a portable policy for daily, weekly, and monthly maintenance
windows. Each window begins only after a successful backup checkpoint and is
limited to sandbox analysis and a human-reviewed report.

**Why you'd want it:** self-improvement is useful only when it cannot quietly
rewrite a live system. This module makes the ordering explicit: checkpoint,
observe, report, then a human decides whether any change is worth making.

## Install just this part

1. Copy `maintenance-schedule.json` to a local policy directory.
2. Connect each `backup_task` to your own backup command. It must return a
   non-zero exit code on failure and produce a dated recovery artifact.
3. Register the daily, weekly, and monthly scheduler entries in your operating
   system. Keep them visible and configure them to call this policy only after
   the corresponding backup succeeds.
4. Run the verifier before enabling a schedule:

   ```bash
   python verify-maintenance-schedule.py maintenance-schedule.json
   ```

## Test it worked

```bash
python verify-maintenance-schedule.py --selftest
```

The verifier rejects a schedule that lacks any cadence, starts before backup,
or permits autonomous production writes. A real test should intentionally fail
a harmless backup command and confirm the maintenance task does not run.

`run-verified-maintenance.py` is the portable scheduler entry point. It defaults
to dry-run. With `--execute`, it runs the backup command first and launches the
improvement command only after exit code 0. It always appends `--report-only` to
the improvement command and writes a JSON receipt.

```bash
python run-verified-maintenance.py --selftest
python run-verified-maintenance.py --cadence daily \
  --backup-command '<local-backup-command>' \
  --improvement-command '<local-improvement-runner>'
```

Register the second command in your operating-system scheduler only after the
dry-run output is correct. Add `--execute` to the registered command. The
module does not choose your backup destination or silently register a task.
