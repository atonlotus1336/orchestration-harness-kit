import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_managed_mcp_stdio_lists_tools_and_reports_profile_health(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: custom:ollama-cloud-local\n  default: deepseek-v4-flash:cloud\n", encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "HERMES_PRIME_PROFILE": "home",
        "HERMES_PRIME_MODE": "prime-available",
        "HERMES_PRIME_POLICY_PATH": str(ROOT / "config/prime-profile-policy.json"),
        "HERMES_PRIME_LEDGER": str(tmp_path / "jobs.sqlite3"),
        "HERMES_PRIME_PROFILE_CONFIG": str(config),
        "PYTHONPATH": str(ROOT),
    })
    params = StdioServerParameters(
        command="bash", args=["-lc", f"exec bash {ROOT}/scripts/launch-prime-mcp.sh"], env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "prime_delegate", "prime_job_status", "prime_job_list", "prime_job_steer",
                "prime_job_cancel", "prime_bridge_health", "prime_capability_catalog",
                "prime_capability_approvals", "prime_capability_resolve",
                "prime_learning_list", "prime_promotion_validate", "prime_promotion_resolve",
                "prime_promotion_revoke", "prime_promotion_expire",
                "prime_connectivity_status", "prime_connectivity_assess_change",
                "prime_session_route", "prime_session_start", "prime_session_status",
                "prime_session_events", "prime_session_steer", "prime_session_approve",
                "prime_session_interrupt", "prime_session_detach", "prime_session_reattach",
                "prime_auto_session_start", "prime_rollout_status",
            }
            result = await session.call_tool("prime_bridge_health", {})
            assert len(result.content) == 1
            assert "prime-available" in result.content[0].text
            assert "restart-boundary" in result.content[0].text
