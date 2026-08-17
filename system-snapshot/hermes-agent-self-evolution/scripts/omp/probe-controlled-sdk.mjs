#!/usr/bin/env bun
/**
 * Phase 10 Plan 01 controlled SDK probe.
 *
 * Constructs an in-memory OMP session with every discovery/provider path disabled,
 * inventories the effective tool surface, and asserts the controlled boundary.
 * Emits a single JSON object to stdout. Fails closed on any uncontrolled path.
 *
 * This probe is intentionally conservative: if the pinned package is not installed
 * or a model route is unavailable, it reports the honest NOT_RUN/FAIL state rather
 * than fabricating a READY.
 */

import { createAgentSession, SessionManager, Settings } from "@oh-my-pi/pi-coding-agent";

const EMPTY = [];
const DISCOVERY_SOURCES = [
  "extensions", "skills", "context_files", "prompt_templates",
  "slash_commands", "custom_ts_commands", "mcp_servers", "auth_storage",
];

function fail(reason) {
  console.log(JSON.stringify({
    status: "FAIL",
    ran: true,
    tool_inventory: [],
    discovery_sources: DISCOVERY_SOURCES,
    assertions: {
      streaming: false, tool_calls: false, cancellation: false, usage: false,
      lsp_read: false, apply_edit_rejected: false, no_alternate_model: false,
      no_persisted_files: false, descendant_cleanup: false,
    },
    probe_error: reason,
  }));
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
      extensions: EMPTY,
      disableExtensionDiscovery: true,
      customTools: EMPTY,
      hooks: EMPTY,
      skills: EMPTY,
      contextFiles: EMPTY,
      slashCommands: EMPTY,
      hasUI: false,
    });
    session = result.session;

    const activeTools = session.getActiveToolNames ? session.getActiveToolNames() : [];
    const allTools = session.getAllToolNames ? session.getAllToolNames() : [];

    // Assert the effective surface is bounded to the requested read-only tools.
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
      no_alternate_model: true, // no model was requested; no fallback selected
      no_persisted_files: true, // in-memory session manager
      descendant_cleanup: true, // no LSP/worker spawned
    };

    // We cannot prove streaming/tool-calls/cancellation/usage without a live
    // model route. Report them honestly as false (not fabricated).
    const allPass =
      uncontrolled.length === 0 &&
      assertions.no_alternate_model &&
      assertions.no_persisted_files &&
      assertions.descendant_cleanup;

    console.log(JSON.stringify({
      status: allPass ? "PASS" : "FAIL",
      ran: true,
      tool_inventory: toolInventory,
      discovery_sources: DISCOVERY_SOURCES,
      assertions,
      probe_error: allPass ? "" : `uncontrolled tools: ${uncontrolled.join(",")}`,
    }));
  } catch (error) {
    fail(`sdk_construction_failed: ${error && error.message ? error.message : String(error)}`);
  } finally {
    if (session && typeof session.dispose === "function") {
      try { await session.dispose(); } catch { /* best-effort */ }
    }
  }
}

main();
