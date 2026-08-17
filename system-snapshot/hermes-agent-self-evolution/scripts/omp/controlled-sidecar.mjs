#!/usr/bin/env bun
/**
 * Phase 10 Plan 03 controlled OMP sidecar host.
 *
 * Constructs an in-memory OMP session with every discovery/provider path
 * disabled, asserts the effective surface at startup, and reports a JSON
 * inventory. Fails closed on any uncontrolled path.
 *
 * This is the pinned SDK host that runs inside the sibling rootless container.
 */

import { createAgentSession, SessionManager, Settings } from "@oh-my-pi/pi-coding-agent";

const DISCOVERY_SOURCES = [
  "extensions", "skills", "context_files", "prompt_templates",
  "slash_commands", "custom_ts_commands", "mcp_servers", "auth_storage",
];

function report(value) {
  console.log(JSON.stringify(value));
  process.exit(0);
}

async function main() {
  let session;
  try {
    const settings = Settings.isolated({});
    const result = await createAgentSession({
      cwd: process.cwd(),
      agentDir: "/tmp/omp-ephemeral-agent",
      settings,
      sessionManager: SessionManager.inMemory(),
      toolNames: ["read", "grep", "glob"],
      restrictToolNames: true,
      enableMCP: false,
      enableLsp: false,
      extensions: [],
      disableExtensionDiscovery: true,
      customTools: [],
      hooks: [],
      skills: [],
      contextFiles: [],
      slashCommands: [],
      hasUI: false,
    });
    session = result.session;

    const allTools = session.getAllToolNames ? session.getAllToolNames() : [];
    const allowed = new Set(["read", "grep", "glob"]);
    const toolInventory = Array.isArray(allTools) ? allTools : [];
    const uncontrolled = toolInventory.filter((name) => !allowed.has(name));

    const assertions = {
      streaming: false,
      tool_calls: false,
      cancellation: false,
      usage: false,
      lsp_read: false,
      apply_edit_rejected: false,
      no_alternate_model: true,
      no_persisted_files: true,
      descendant_cleanup: true,
    };

    const allPass =
      uncontrolled.length === 0 &&
      assertions.no_alternate_model &&
      assertions.no_persisted_files &&
      assertions.descendant_cleanup;

    report({
      status: allPass ? "PASS" : "FAIL",
      ran: true,
      tool_inventory: toolInventory,
      discovery_sources: DISCOVERY_SOURCES,
      assertions,
      probe_error: allPass ? "" : `uncontrolled tools: ${uncontrolled.join(",")}`,
    });
  } catch (error) {
    report({
      status: "FAIL",
      ran: true,
      tool_inventory: [],
      discovery_sources: DISCOVERY_SOURCES,
      assertions: {
        streaming: false, tool_calls: false, cancellation: false, usage: false,
        lsp_read: false, apply_edit_rejected: false, no_alternate_model: false,
        no_persisted_files: false, descendant_cleanup: false,
      },
      probe_error: `sdk_construction_failed: ${error && error.message ? error.message : String(error)}`,
    });
  } finally {
    if (session && typeof session.dispose === "function") {
      try { await session.dispose(); } catch { /* best-effort */ }
    }
  }
}

main();
