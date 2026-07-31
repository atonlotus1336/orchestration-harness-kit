# m15-practice-loop (opt-in kit module)

A curated weekly learning loop that SURFACES new AI tools/techniques for a human to
adopt or skip. It is **digest-only by design**: the loop never writes memory, never
changes routing or behavior, and ships with **no unattended auto-adopt mode** — that
mode does not exist in the code, not merely disabled.

## What ships
- `learning_loop.py` — transcript filter pipeline (local-model first; a strict-JSON
  relevance filter plus a lexical injection tripwire that quarantines any transcript
  containing instruction-shaped payloads, regardless of the model's verdict).
- `adopt_item.py` — THE GATE. Human-invoked, one item at a time, `--approved-by`
  required; writes a single provenance note into your notes directory. Refuses any
  item the filter did not surface.
- `fixtures/` — the canary pool: planted hype, planted real-tool, planted
  prompt-injection transcripts. Your first run MUST pass these (see below) before you
  point the loop at live sources.

## Configure (env, all optional)
```
LEARNING_LOOP_OUT=/path/to/digests        # default ./output/practice-loop
LEARNING_LOOP_ENDPOINT=http://127.0.0.1:11434   # your Ollama-compatible endpoint
LEARNING_LOOP_MEMORY_NOTES=/path/to/notes # where adopt_item writes provenance notes
```

## Fail-closed go-live check (run BEFORE any live week)
```
python learning_loop.py --dry-run-fixtures fixtures --week GO-LIVE-CHECK
```
Required outcome: the hype fixture lands in Discarded, the real-tool fixture is
Surfaced with a verify link, the injection fixture is Quarantined with its payload NOT
reproduced, and the summary reports `"adopt_ready": 0`. If any of those fail, do not
go live — the filter or tripwire is broken on your setup.

## Trust model (read this once)
The lexical injection tripwire is DEFENSE-IN-DEPTH, not the barrier — paraphrased or
encoded payloads can evade it. The actual barriers are: the strict-JSON filter that
fails closed to "hype", digest fields being sanitized/clipped, `adopt_ready` being
hardcoded to 0, and above all YOU at the adopt-gate. An evasive injection can at worst
reach the Surfaced list with a model-supplied link — verify links before clicking, and
never adopt an item you haven't independently checked.

## Live weekly run
Drop candidate transcripts (*.txt) into `$LEARNING_LOOP_OUT/inbox/<ISO-week>/` (a cron
or n8n workflow can do the fetching — keyless yt-dlp subtitle extraction works well),
then run `python learning_loop.py`. Read the digest; for anything worth keeping:
```
python adopt_item.py --digest LEARNING-DIGEST-<week>.md --item "<name>" --approved-by "<you>"
```
Anything that would change your system's BEHAVIOR (not just knowledge) is separate
work that should pass your eval suite before merging.
