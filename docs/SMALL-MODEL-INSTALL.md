# Small-model client install card

Use this index to select one state card under `installer/clone-eval/`. Give the
model only that state card. Never ask a small model to choose among states.

| State | Card |
|---|---|
| No prior result | `install-guidance-start.txt` |
| Install `DRY-RUN` | `install-guidance-after-dry-run.txt` |
| Install `PASS` | `install-guidance-after-install-pass.txt` |
| Doctor `PASS` | `install-guidance-after-doctor-pass.txt` |

For `FAIL` or `BLOCKED`, output exactly the result's `next_step` and stop.
