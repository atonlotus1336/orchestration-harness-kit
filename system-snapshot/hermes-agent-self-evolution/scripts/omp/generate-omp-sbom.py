#!/usr/bin/env python3
"""Generate an SPDX SBOM for the OMP sidecar image (Phase 10 Plan 01)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "localhost/hermes-omp-sidecar:phase10-a"
OUT = ROOT / "containers" / "omp" / "sbom.spdx.json"


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def flatten_npm(node: dict, prefix: str = "") -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for name, info in node.get("dependencies", {}).items():
        version = info.get("version", "")
        items.append((f"{prefix}{name}", version))
        items.extend(flatten_npm(info, prefix=f"{prefix}{name}/"))
    return items


def main() -> int:
    npm_inventory = json.loads(run([
        "podman", "run", "--rm", "--workdir", "/opt/omp",
        "--entrypoint", "npm", IMAGE, "ls", "--all", "--json",
    ], check=False).stdout)
    image_id = run(["podman", "image", "inspect", IMAGE, "--format", "{{.Id}}"]).stdout.strip()
    context_sha = hashlib.sha256((ROOT / "containers/omp/Dockerfile").read_bytes()).hexdigest()

    packages: list[dict] = []
    for name, version in flatten_npm(npm_inventory):
        token = hashlib.sha256(f"npm:{name}:{version}".encode()).hexdigest()[:20]
        packages.append({
            "SPDXID": f"SPDXRef-npm-{token}",
            "name": name,
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "supplier": "NOASSERTION",
        })
    namespace = hashlib.sha256(f"{image_id}:{context_sha}".encode()).hexdigest()
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "hermes-omp-sidecar-v17.3.0",
        "documentNamespace": f"https://__PRIVATE_TOKEN_02__.local/spdx/omp-sidecar/{namespace}",
        "creationInfo": {"created": "2026-08-13T00:00:00Z", "creators": ["Tool: hermes-phase10-omp-lock"]},
        "packages": sorted(packages, key=lambda item: item["SPDXID"]),
        "annotations": [{
            "annotationType": "OTHER",
            "annotator": "Tool: hermes-phase10-omp-lock",
            "annotationDate": "2026-08-13T00:00:00Z",
            "comment": f"context-sha256={context_sha};image-id={image_id}",
        }],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(sbom, indent=2, sort_keys=True), encoding="utf-8")
    sbom_sha = hashlib.sha256(OUT.read_bytes()).hexdigest()
    print(json.dumps({"image_id": image_id, "sbom_path": str(OUT), "sbom_sha256": sbom_sha, "package_count": len(packages)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
