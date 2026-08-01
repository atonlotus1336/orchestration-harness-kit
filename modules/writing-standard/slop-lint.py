#!/usr/bin/env python
"""
slop-lint — deterministic anti-slop checker for prose (zero LLM tokens).

Counts six machine-checkable slop habits + approximate passive voice, scores
violations per 100 prose words. Markdown-aware: fenced code blocks, inline
code, and link URLs are stripped before scoring (code is not prose).

Usage:
  python slop-lint.py FILE [FILE...]      lint files (flavored: 25-word cap)
  python slop-lint.py --strict FILE       procedures/errors (20-word cap,
                                          semicolons weighted as violations)
  python slop-lint.py --max 1.0 FILE      exit 1 if score exceeds threshold
  python slop-lint.py --selftest          prove every rule on named inputs

Companion skill: writing-standard/SKILL.md (modes, scope, fixes).
Honest limits: passive detection is approximate; this checks FORM, not truth.
"""
import argparse
import re
import sys

HEDGES = [
    r"\bit(?:'s| is) important to note\b",
    r"\bit should be noted\b",
    r"\bplease note that\b",
    r"\bmay potentially\b",
    r"\bcould potentially\b",
    r"\bgenerally speaking\b",
    r"\bin order to\b",
    r"\bhelps? to (?:improve|ensure|enable|facilitate)\b",
    r"\bit is worth (?:noting|mentioning)\b",
]
NOMINALIZATIONS = [
    r"\bperform(?:s|ed)? (?:an?|the) \w+(?:tion|sis|ment|ance|al)\b",
    r"\bconduct(?:s|ed)? (?:an?|the) (?:analysis|review|assessment|evaluation)\b",
    r"\bprovide(?:s|d)? (?:assistance|support|guidance|clarification)\b",
    r"\bmake(?:s)? (?:a|an|the) (?:decision|determination|assessment|recommendation)\b",
    r"\bcarr(?:y|ies|ied) out (?:an?|the)\b",
]
MARKETING = [
    r"\bseamless(?:ly)?\b", r"\brobust(?:ly)?\b", r"\bpowerful(?:ly)?\b",
    r"\bcutting[- ]edge\b", r"\bstate[- ]of[- ]the[- ]art\b",
    r"\brevolutionary\b", r"\bgame[- ]chang\w+\b", r"\bbest[- ]in[- ]class\b",
    r"\bworld[- ]class\b", r"\bblazing(?:ly)?\b", r"\beffortless(?:ly)?\b",
    r"\bsupercharge\w*\b", r"\bnext[- ]level\b",
]
PHRASAL = [
    r"\bspin(?:ning)? up\b", r"\breach(?:ing)? out\b", r"\bdive(?:s|d)? into\b",
    r"\bdiving into\b", r"\bkick(?:s|ed)? off\b", r"\btouch base\b",
    r"\bcircle back\b", r"\bdrill(?:s|ed)? down\b", r"\bfire(?:s|d)? off\b",
]
# be-verb + participle; approximate by design (documented limit).
PASSIVE = re.compile(
    r"\b(?:is|are|was|were|been|being|be)\s+"
    r"(?:\w+ed|written|given|taken|made|done|run|set|found|held|kept|left|"
    r"sent|shown|told|known|seen|put|built|caught)\b", re.I)

CATS = ("long-sentence", "semicolon", "hedge", "nominalization",
        "marketing", "phrasal", "passive")


def strip_markdown(text):
    text = re.sub(r"```.*?```", "", text, flags=re.S)      # fenced code
    text = re.sub(r"`[^`\n]+`", "", text)                   # inline code
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)    # links -> text
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.M)       # heading hashes
    return text


def lint_text(text, strict=False):
    prose = strip_markdown(text)
    words_total = len(re.findall(r"\b\w+\b", prose))
    hits = []  # (category, line_no, excerpt)
    cap = 20 if strict else 25

    for i, line in enumerate(prose.splitlines(), 1):
        for sent in re.split(r"(?<=[.!?])\s+", line):
            n = len(re.findall(r"\b\w+\b", sent))
            if n > cap:
                hits.append(("long-sentence", i, f"{n} words: {sent[:60]}..."))
        if strict and ";" in line:
            hits.append(("semicolon", i, line.strip()[:60]))
        for name, patterns in (("hedge", HEDGES), ("nominalization", NOMINALIZATIONS),
                               ("marketing", MARKETING), ("phrasal", PHRASAL)):
            for p in patterns:
                for m in re.finditer(p, line, re.I):
                    hits.append((name, i, m.group(0)))
        for m in PASSIVE.finditer(line):
            hits.append(("passive", i, m.group(0)))

    score = (len(hits) / words_total * 100) if words_total else 0.0
    return score, hits, words_total


def report(path, score, hits, words):
    counts = {c: sum(1 for h in hits if h[0] == c) for c in CATS}
    print(f"{path}: {score:.2f} violations/100 words "
          f"({len(hits)} hits / {words} words)")
    for c, n in counts.items():
        if n:
            print(f"  {c:15} {n}")
    for cat, line, ex in hits[:40]:
        print(f"    L{line:<5} [{cat}] {ex}")
    if len(hits) > 40:
        print(f"    ... and {len(hits) - 40} more")


SELFTEST = [
    # (category-that-must-fire, sloppy input)
    ("long-sentence", "This sentence keeps going and going with idea after idea "
     "stitched together so that the reader loses the thread entirely before "
     "any actual point arrives at the end of it all somewhere."),
    ("hedge", "It's important to note that this may potentially help."),
    ("nominalization", "We perform an analysis of the logs and provide assistance."),
    ("marketing", "A seamless, robust, cutting-edge experience."),
    ("phrasal", "Spin up a server and reach out to the team."),
    ("passive", "The file was deleted and the config was written."),
]
CLEAN_CONTROLS = [
    "Restart the gateway.",
    "If the check fails, run the repair script.",
    "The installer deletes the temporary files.",   # active, not passive
]


def selftest():
    failures = 0
    for cat, sloppy in SELFTEST:
        _, hits, _ = lint_text(sloppy, strict=True)
        fired = {h[0] for h in hits}
        ok = cat in fired
        print(f"{'PASS' if ok else 'FAIL'}  trips [{cat:14}] <- {sloppy[:50]}...")
        failures += 0 if ok else 1
    for clean in CLEAN_CONTROLS:
        _, hits, _ = lint_text(clean, strict=True)
        ok = not hits
        print(f"{'PASS' if ok else 'FAIL'}  clean control stays silent <- {clean[:50]}"
              + ("" if ok else f"  [flagged: {[h[0] for h in hits]}]"))
        failures += 0 if ok else 1
    print(f"\n{failures} failures")
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--max", type=float, default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())
    if not args.files:
        ap.error("no files given (or use --selftest)")

    worst = 0.0
    for path in args.files:
        with open(path, encoding="utf-8", errors="replace") as f:
            score, hits, words = lint_text(f.read(), strict=args.strict)
        report(path, score, hits, words)
        worst = max(worst, score)
    if args.max is not None and worst > args.max:
        print(f"\nFAIL: worst score {worst:.2f} > max {args.max}")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
