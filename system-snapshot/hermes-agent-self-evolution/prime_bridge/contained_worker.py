"""Production Prime worker assembled from the verified Phase 1 containment pieces."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from prime_bridge.capability_broker import CapabilityBroker, CapabilityBrokerError
from prime_bridge.inference_relay import InferenceRelay, RelayPolicy
from prime_bridge.job_cli import load_runtime_authority
from prime_bridge.prime_rpc_worker import PrimeRPCWorker
from prime_bridge.rpc_probe import (
    PRIME_RUNTIME_CREDENTIAL,
    _clean_child_environment,
    _resolve_probe_credential,
    _write_prime_config,
)
from prime_bridge.sandbox import Admission, PodmanSandbox, admit


class ContainedPrimeWorker:
    """Prime's native RPC harness inside the admitted rootless Podman boundary."""

    def __init__(
        self, *, repo_root: Path, runtime_authority: Path,
        prime_config: dict[str, Any], hermes_config: Path,
        capability_broker: CapabilityBroker | None = None,
        job_id: str | None = None, profile: str | None = None,
    ):
        self.repo_root = repo_root.resolve()
        self.runtime_authority = runtime_authority.resolve()
        self.prime_config = prime_config
        self.hermes_config = hermes_config.resolve()
        self.capability_broker = capability_broker
        self.job_id = job_id
        self.profile = profile
        self.runtime_fingerprint = load_runtime_authority(self.runtime_authority)["fingerprint"]
        self.admission: Admission | None = None
        self.sandbox: PodmanSandbox | None = None
        self.relay: InferenceRelay | None = None
        self.worker: PrimeRPCWorker | None = None
        self._temporary: tempfile.TemporaryDirectory[str] | None = None

    async def start(self, *, prompt: str, workspace: str, route: dict[str, str]) -> dict[str, str]:
        endpoint = urlsplit(route["endpoint"])
        hostname = str(endpoint.hostname or "")
        port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
        credentials, record = _resolve_probe_credential(
            self.prime_config, None, dict(os.environ), self.hermes_config,
        )
        # The local Ollama OpenAI-compatible endpoint authenticates upstream
        # itself. Its conventional client value is not a cloud credential and
        # is safe to synthesize inside the transient container.
        if not credentials and (endpoint.scheme, hostname, port) == ("http", "127.0.0.1", 11434):
            credentials = ("ollama",)
        if not credentials:
            raise RuntimeError(f"Prime credential unavailable: {record.get('reason', 'unverified')}")
        if (endpoint.scheme, hostname, port) not in {
            ("https", "ollama.com", 443), ("http", "127.0.0.1", 11434),
        }:
            raise RuntimeError("Prime route is outside the verified relay allowlist")
        self._temporary = tempfile.TemporaryDirectory(prefix="hermes-prime-")
        socket_path = Path(self._temporary.name) / "relay.sock"
        self.relay = InferenceRelay(
            socket_path, RelayPolicy.resolve(hostname, port),
            capability_handler=self._handle_capability if self.capability_broker is not None else None,
        )
        await self.relay.start()
        self.admission = admit(self.repo_root)
        self.sandbox = PodmanSandbox(
            self.admission, relay_socket=socket_path, relay_hostname=hostname, relay_port=port,
        )
        relay_config = {
            "listen_host": "127.0.0.1", "listen_port": port,
            "uds_path": "/run/prime-relay/relay.sock",
            "allowed_hostname": hostname, "allowed_port": port,
        }
        command = (
            "import asyncio,runpy; ns=runpy.run_path('/opt/phase01/relay_client.py'); "
            f"asyncio.run(ns['serve']({relay_config!r}))"
        )
        container_id = self.sandbox.create([command])
        self.sandbox.start()
        if self.capability_broker is not None:
            client_source = Path(__file__).with_name("capability_client.py").read_text(encoding="utf-8")
            try:
                self.sandbox.exec_python(
                    "from pathlib import Path; import sys; "
                    "p=Path('/tmp/.prime/agent/hermes_capability.py'); "
                    "p.parent.mkdir(parents=True, exist_ok=True); p.write_text(sys.argv[1], encoding='utf-8')",
                    client_source,
                )
            except Exception:  # noqa: BLE001 - collapse all subprocess details at the security boundary
                raise RuntimeError("capability client installation failed") from None
            prompt = (
                "Hermes capabilities are available during this loop with: "
                "from hermes_capability import call; "
                "call('/run/prime-relay/relay.sock', '<public-alias>', {...}). "
                "If a call returns approval_required, pause that action and retry with its request_id only after Hermes approves it.\n\n"
                + prompt
            )
        child_environment = _clean_child_environment((PRIME_RUNTIME_CREDENTIAL,))
        prime_environment = dict(child_environment)
        prime_environment[PRIME_RUNTIME_CREDENTIAL] = credentials[0]
        _write_prime_config(
            container_id, self.prime_config, PRIME_RUNTIME_CREDENTIAL,
            child_environment=child_environment,
        )
        argv = [
            "podman", "exec", "-i", "--env", PRIME_RUNTIME_CREDENTIAL, container_id,
            "/opt/prime-agent/prime-agent.sh", "--dist", "--mode", "rpc",
            "--session-dir", "/tmp/.prime/agent/sessions",
            "--provider", route["provider"], "--model", route["model"],
        ]
        self.worker = PrimeRPCWorker(
            argv, runtime_fingerprint=self.runtime_fingerprint, environment=prime_environment,
            response_timeout=60.0,
        )
        return await self.worker.start(prompt=prompt, workspace=workspace, route=route)

    async def _handle_capability(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.capability_broker is None or self.job_id is None or self.profile is None:
            return {"status": "error", "error": "capability boundary unavailable"}
        if set(request) - {"alias", "arguments", "request_id"}:
            return {"status": "error", "error": "capability identity is broker-owned"}
        try:
            return await self.capability_broker.handle({
                **request, "job_id": self.job_id, "profile": self.profile,
            })
        except CapabilityBrokerError as exc:
            return {"status": "error", "error": str(exc)[:256]}

    def events(self):
        if self.worker is None:
            raise RuntimeError("Prime worker has not started")
        return self.worker.events()

    async def command(self, *, command_id: str, kind: str, text: str | None = None) -> None:
        if self.worker is None:
            raise RuntimeError("Prime worker has not started")
        await self.worker.command(command_id=command_id, kind=kind, text=text)

    async def close(self) -> None:
        errors: list[str] = []
        if self.worker is not None:
            try:
                await self.worker.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)
        if self.sandbox is not None:
            try:
                self.sandbox.teardown()
            except Exception as exc:  # noqa: BLE001
                errors.append(type(exc).__name__)
        if self.admission is not None:
            self.admission.close()
        if self.relay is not None:
            await self.relay.close()
        if self._temporary is not None:
            self._temporary.cleanup()
        if errors:
            raise RuntimeError("contained worker cleanup failed: " + ",".join(errors))
