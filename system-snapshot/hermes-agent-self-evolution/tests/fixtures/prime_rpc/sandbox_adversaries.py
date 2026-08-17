"""Python snippets used inside the immutable Phase 01 image."""

IDENTITY_AND_DENIALS = r"""
import json, os, pathlib, socket
def writable(path):
    try:
        probe = pathlib.Path(path) / "phase01-write-probe"
        probe.write_bytes(b"x")
        probe.unlink()
        return True
    except OSError:
        return False
network_denied = False
try:
    socket.create_connection(("1.1.1.1", 443), timeout=0.5)
except OSError:
    network_denied = True
print(json.dumps({
    "uid": os.getuid(), "gid": os.getgid(), "network_denied": network_denied,
    "root_writable": writable("/opt"), "work_writable": writable("/work"),
    "mnt_g_exists": pathlib.Path("/mnt/d").exists(),
    "hermes_profile_exists": pathlib.Path.home().joinpath(".hermes").exists(),
    "no_new_privs": next(line.split()[1] for line in pathlib.Path("/proc/self/status").read_text().splitlines() if line.startswith("NoNewPrivs:")),
    "cap_eff": next(line.split()[1] for line in pathlib.Path("/proc/self/status").read_text().splitlines() if line.startswith("CapEff:")),
}))
"""

DETACHED_DESCENDANTS = r"""
import json, os, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pids = []
for _ in range(2):
    pid = os.fork()
    if pid == 0:
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if os.fork() == 0:
            while True: time.sleep(1)
        os._exit(0)
    pids.append(pid)
print(json.dumps({"leader": os.getpid(), "children": pids}), flush=True)
while True: time.sleep(1)
"""
