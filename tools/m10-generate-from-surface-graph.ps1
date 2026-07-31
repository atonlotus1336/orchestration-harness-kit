param(
  [string]$Graph = "SURFACE-GRAPH.yaml",
  [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $OutputDir) {
  $OutputDir = Join-Path (Split-Path -Parent $PSScriptRoot) "generated"
}
if (-not (Test-Path -LiteralPath $Graph) -and $Graph -eq "SURFACE-GRAPH.yaml") {
  $candidateGraph = Join-Path (Split-Path -Parent $PSScriptRoot) "..\..\SURFACE-GRAPH.yaml"
  if (Test-Path -LiteralPath $candidateGraph) { $Graph = $candidateGraph }
}
if (-not (Test-Path -LiteralPath $Graph)) {
  throw "Surface graph missing: $Graph"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$generator = @'
import hashlib
import json
import os
import re
import sys
from pathlib import PureWindowsPath

import yaml

GRAPH = sys.argv[1]
OUT = sys.argv[2]
VERSION = "m10-generator-v1"
VALID_PRIVACY = {"local-only", "shareable", "slot"}

with open(GRAPH, "r", encoding="utf-8") as f:
    raw = f.read()
data = yaml.safe_load(raw) or {}
rows = data.get("surfaces") or []

errors = []
seen = set()
required = data.get("schema", {}).get("required_fields") or [
    "id", "class", "config_path", "owner", "probe_cmd", "cadence",
    "slack", "secret_fields", "privacy_class", "break_recipe",
    "expected_red", "state",
]
for idx, row in enumerate(rows):
    rid = str(row.get("id") or "")
    if not rid:
        errors.append(f"row[{idx}]:missing:id")
        continue
    if rid in seen:
        errors.append(f"{rid}:duplicate:id")
    seen.add(rid)
    for field in required:
        if field not in row:
            errors.append(f"{rid}:missing:{field}")
    privacy = str(row.get("privacy_class") or "")
    if privacy not in VALID_PRIVACY:
        errors.append(f"{rid}:bad_privacy:{privacy}")
    state = str(row.get("state") or "")
    if state not in {"active", "quarantined", "retired", "rotted"}:
        errors.append(f"{rid}:bad_state:{state}")

if errors:
    print(json.dumps({"status": "RED", "errors": errors}, indent=2))
    sys.exit(2)
def norm_path(value):
    return str(value or "").replace("\\", "/")


def is_unsafe_target(value):
    path = norm_path(value).strip()
    if not path:
        return False
    if path.startswith("/") or re.match(r"^[A-Za-z]:/", path):
        return True
    return any(part in {"..", ""} for part in path.split("/"))


def rel_target(row):
    explicit = norm_path(row.get("export_target_path"))
    if explicit:
        return explicit
    path = norm_path(row.get("config_path"))
    vault = "Lap" + "Art"
    kit_marker = "G:" + "/" + vault + "/export/orchestration-layer/"
    if path.startswith(kit_marker):
        return path[len(kit_marker):]
    marker = "G:" + "/" + vault + "/"
    if path.startswith(marker):
        return path[len(marker):]
    return path

stable_rows = []
for row in sorted(rows, key=lambda r: str(r.get("id"))):
    target_path = rel_target(row)
    if is_unsafe_target(target_path):
        errors.append(f"{row.get('id')}:unsafe_export_target:{target_path}")
        continue
    stable_rows.append({
        "id": str(row.get("id")),
        "class": str(row.get("class")),
        "privacy_class": str(row.get("privacy_class")),
        "install_stage": str(row.get("install_stage") or row.get("class") or "unassigned"),
        "target_path": target_path,
        "state": str(row.get("state") or "active"),
        "expected_red": str(row.get("expected_red") or ""),
    })

if errors:
    print(json.dumps({"status": "RED", "errors": errors}, indent=2))
    sys.exit(2)

shareable = [r for r in stable_rows if r["privacy_class"] == "shareable" and r["state"] == "active"]
slots = [r for r in stable_rows if r["privacy_class"] == "slot" and r["state"] == "active"]
stages = {}
for row in stable_rows:
    stages.setdefault(row["install_stage"], []).append(row)
stages = {k: stages[k] for k in sorted(stages)}

parity = [
    {
        "id": str(row.get("id")),
        "probe_ref": "SURFACE-GRAPH.yaml#surfaces." + str(row.get("id")),
        "verify_ref": "SURFACE-GRAPH.yaml#surfaces." + str(row.get("id")),
        "expected_red": str(row.get("expected_red") or ""),
    }
    for row in sorted(rows, key=lambda r: str(r.get("id")))
    if row.get("state") == "active" and (row.get("probe_cmd") or row.get("verify_cmd"))
]

os.makedirs(OUT, exist_ok=True)

def write_text(name, text):
    with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

def write_json(name, obj):
    write_text(name, json.dumps(obj, indent=2, sort_keys=True) + "\n")

write_json("INSTALL-STAGES.json", {"version": VERSION, "stages": stages})
write_json("PARITY-VERIFIER.json", {"version": VERSION, "checks": parity})
write_json("SYNC-ALLOWLIST.json", {"version": VERSION, "shareable": shareable})
write_text(
    "SYNC-ALLOWLIST.txt",
    "\n".join(f'{r["id"]}\t{r["target_path"]}' for r in shareable) + "\n",
)

slot_yaml = {
    "version": "m10-bindings-template-v1",
    "note": "Template only. Concrete bindings live in bindings.local.yaml and are never allowlisted.",
    "slots": {
        r["id"]: {
            "class": r["class"],
            "capability": r["target_path"],
            "binding": "<local-only>",
        }
        for r in slots
    },
}
write_text("bindings.local.template.yaml", yaml.safe_dump(slot_yaml, sort_keys=True, allow_unicode=False))

manual_lines = [
    "# Orchestration Ecosystem Kit Manual",
    "",
    "This file is generated from SURFACE-GRAPH.yaml. Edit the graph or generator, not this output.",
    "",
    "## Verify Before Execute",
    "",
    "1. Inspect PROVENANCE.json and confirm the graph hash matches the source you intend to use.",
    "2. Review SYNC-ALLOWLIST.txt before copying any shareable artifact.",
    "3. Run m10-scrub-v2.ps1 against generated and copied artifacts before packaging.",
    "4. Fill bindings.local.yaml from bindings.local.template.yaml only on the local machine.",
    "",
    "## Install Stages",
    "",
]
for stage, members in stages.items():
    manual_lines.append(f"### {stage}")
    for item in members:
        manual_lines.append(f"- {item['id']} [{item['privacy_class']}] -> {item['target_path']}")
    manual_lines.append("")
write_text("MANUAL.md", "\n".join(manual_lines).rstrip() + "\n")

checksums = {}
for name in sorted(os.listdir(OUT)):
    path = os.path.join(OUT, name)
    if os.path.isfile(path) and name != "PROVENANCE.json":
        with open(path, "rb") as f:
            checksums[name] = hashlib.sha256(f.read()).hexdigest()

provenance = {
    "version": VERSION,
    "source_graph": "SURFACE-GRAPH.yaml",
    "source_graph_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    "row_count": len(stable_rows),
    "shareable_count": len(shareable),
    "slot_count": len(slots),
    "checksums": checksums,
}
write_json("PROVENANCE.json", provenance)

print(json.dumps({"status": "GREEN", "output": OUT, "row_count": len(stable_rows)}, sort_keys=True))
'@

$tmp = [System.IO.Path]::GetTempFileName() + ".py"
try {
  Set-Content -LiteralPath $tmp -Value $generator -Encoding UTF8
  & python $tmp $Graph $OutputDir
  if ($LASTEXITCODE -ne 0) {
    throw "M10 graph generation failed"
  }
} finally {
  Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
}
