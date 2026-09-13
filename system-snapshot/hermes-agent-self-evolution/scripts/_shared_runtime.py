"""Use one installed host interpreter for source-bound execution evidence."""
import os
from pathlib import Path
import sys

def ensure_runtime(script):
    script=Path(script).resolve()
    root=script.parent.parent
    expected=(root.parent/".hermes/hermes-agent/venv/Scripts/python.exe"
              if os.name=="nt" else Path.home()/".hermes/hermes-agent/venv/bin/python")
    if not expected.is_file():
        raise RuntimeError("The canonical evidence interpreter is unavailable; repair the host runtime")
    # Compare environment paths, not resolved binary symlinks: a venv may point
    # at the same binary while loading a different jsonschema distribution.
    if os.path.normcase(os.path.abspath(sys.executable)) == os.path.normcase(str(expected)):
        return
    marker="_LAPART_EVIDENCE_RUNTIME_REEXEC"
    if os.environ.get(marker)==str(script):
        raise RuntimeError("The canonical evidence interpreter could not be selected")
    env=dict(os.environ);env[marker]=str(script)
    if os.name == "nt":
        # Windows execve through a venv launcher can terminate without running
        # the target. Preserve streams and the exact configured interpreter.
        import subprocess
        raise SystemExit(subprocess.run([str(expected),str(script),*sys.argv[1:]],env=env).returncode)
    os.execve(str(expected),[str(expected),str(script),*sys.argv[1:]],env)
